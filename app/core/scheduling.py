"""
InsightForgeAI – Enterprise Scheduling & Saved Insights (Phase 4.5)

- Saved insights (named questions) persisted per workspace
- Schedules: interval or daily-at time, tied to workspace + question or dashboard
- Delivery: Slack webhook, SMTP email, or log-only (always audited)
- Least-privilege: runs read-only against workspace; no credential storage beyond env
- Free stack: stdlib json/pathlib/smtplib/urllib + existing Workspace / orchestrator

Layout:
  data/workspaces/{id}/insights.json          – named saved questions
  data/workspaces/{id}/schedules.json         – schedules for that workspace
  data/schedules/run_log.jsonl                – global run history (append)
"""

from __future__ import annotations

import json
import os
import smtplib
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_name(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_\-]+", "_", (name or "unnamed").strip()) or "unnamed"


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parents[2], Path.cwd()]:
        if (p / "app").exists() or (p / "data").exists():
            return p
    return Path.cwd()


def _workspaces_root() -> Path:
    root = _project_root() / "data" / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_log_path() -> Path:
    p = _project_root() / "data" / "schedules"
    p.mkdir(parents=True, exist_ok=True)
    return p / "run_log.jsonl"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Saved insights
# ---------------------------------------------------------------------------

@dataclass
class SavedInsight:
    id: str
    name: str
    question: str
    table_name: str
    workspace_id: str = "default"
    description: str = ""
    created_by: str = "anonymous"
    created_at: str = field(default_factory=_now_iso)
    last_run_at: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SavedInsight":
        return cls(
            id=str(d.get("id") or str(uuid.uuid4())[:8]),
            name=str(d.get("name") or "Untitled"),
            question=str(d.get("question") or ""),
            table_name=str(d.get("table_name") or ""),
            workspace_id=str(d.get("workspace_id") or "default"),
            description=str(d.get("description") or ""),
            created_by=str(d.get("created_by") or "anonymous"),
            created_at=str(d.get("created_at") or _now_iso()),
            last_run_at=d.get("last_run_at"),
            tags=list(d.get("tags") or []),
        )


def _insights_path(workspace_id: str) -> Path:
    return _workspaces_root() / _safe_name(workspace_id) / "insights.json"


def load_insights(workspace_id: str = "default") -> List[SavedInsight]:
    raw = _read_json(_insights_path(workspace_id), default=[])
    if not isinstance(raw, list):
        return []
    return [SavedInsight.from_dict(x) for x in raw if isinstance(x, dict)]


def save_insights(workspace_id: str, items: List[SavedInsight]) -> None:
    _write_json(_insights_path(workspace_id), [i.to_dict() for i in items])


def add_insight(insight: SavedInsight) -> SavedInsight:
    items = load_insights(insight.workspace_id)
    items = [i for i in items if i.id != insight.id]
    items.append(insight)
    save_insights(insight.workspace_id, items)
    return insight


def remove_insight(workspace_id: str, insight_id: str) -> bool:
    items = load_insights(workspace_id)
    new = [i for i in items if i.id != insight_id]
    if len(new) == len(items):
        return False
    save_insights(workspace_id, new)
    return True


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

@dataclass
class Schedule:
    """
    A recurring report job.

    kind:
      - "question"  → run NL question against table_name
      - "dashboard" → refresh all pinned widgets and summarize

    cadence:
      - interval_minutes > 0  → every N minutes
      - daily_at "HH:MM" (UTC) → once per day at that time
    """

    id: str
    name: str
    workspace_id: str = "default"
    kind: str = "question"  # question | dashboard
    question: str = ""
    table_name: str = ""
    interval_minutes: Optional[int] = None
    daily_at: Optional[str] = None  # "09:00" UTC
    channel: str = "log"  # log | slack | email
    webhook_url: Optional[str] = None
    email_to: Optional[str] = None
    created_by: str = "anonymous"
    enabled: bool = True
    created_at: str = field(default_factory=_now_iso)
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    last_status: str = "never"  # never | ok | error
    last_error: Optional[str] = None
    last_summary: Optional[str] = None
    run_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Schedule":
        return cls(
            id=str(d.get("id") or str(uuid.uuid4())[:8]),
            name=str(d.get("name") or "Untitled schedule"),
            workspace_id=str(d.get("workspace_id") or "default"),
            kind=str(d.get("kind") or "question"),
            question=str(d.get("question") or ""),
            table_name=str(d.get("table_name") or ""),
            interval_minutes=d.get("interval_minutes"),
            daily_at=d.get("daily_at"),
            channel=str(d.get("channel") or "log"),
            webhook_url=d.get("webhook_url"),
            email_to=d.get("email_to"),
            created_by=str(d.get("created_by") or "anonymous"),
            enabled=bool(d.get("enabled", True)),
            created_at=str(d.get("created_at") or _now_iso()),
            last_run_at=d.get("last_run_at"),
            next_run_at=d.get("next_run_at"),
            last_status=str(d.get("last_status") or "never"),
            last_error=d.get("last_error"),
            last_summary=d.get("last_summary"),
            run_count=int(d.get("run_count") or 0),
        )


def _schedules_path(workspace_id: str) -> Path:
    return _workspaces_root() / _safe_name(workspace_id) / "schedules.json"


def load_schedules(workspace_id: str = "default") -> List[Schedule]:
    raw = _read_json(_schedules_path(workspace_id), default=[])
    if not isinstance(raw, list):
        return []
    return [Schedule.from_dict(x) for x in raw if isinstance(x, dict)]


def save_schedules(workspace_id: str, items: List[Schedule]) -> None:
    _write_json(_schedules_path(workspace_id), [s.to_dict() for s in items])


def list_all_schedules() -> List[Schedule]:
    out: List[Schedule] = []
    root = _workspaces_root()
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if d.is_dir():
            out.extend(load_schedules(d.name))
    return out


def compute_next_run(
    *,
    interval_minutes: Optional[int] = None,
    daily_at: Optional[str] = None,
    after: Optional[datetime] = None,
) -> Optional[str]:
    """Return next_run_at ISO string, or None if cadence invalid."""
    base = after or _now_utc()
    if interval_minutes and int(interval_minutes) > 0:
        nxt = base + timedelta(minutes=int(interval_minutes))
        return nxt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if daily_at and isinstance(daily_at, str) and ":" in daily_at:
        try:
            hh, mm = daily_at.strip().split(":")[:2]
            hh_i, mm_i = int(hh), int(mm)
            candidate = base.replace(hour=hh_i, minute=mm_i, second=0, microsecond=0)
            if candidate <= base:
                candidate = candidate + timedelta(days=1)
            return candidate.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return None
    return None


def create_schedule(
    *,
    name: str,
    workspace_id: str = "default",
    kind: str = "question",
    question: str = "",
    table_name: str = "",
    interval_minutes: Optional[int] = None,
    daily_at: Optional[str] = None,
    channel: str = "log",
    webhook_url: Optional[str] = None,
    email_to: Optional[str] = None,
    created_by: str = "anonymous",
    enabled: bool = True,
) -> Schedule:
    if kind not in ("question", "dashboard"):
        raise ValueError("kind must be 'question' or 'dashboard'")
    if channel not in ("log", "slack", "email"):
        raise ValueError("channel must be log|slack|email")
    if kind == "question" and not (question or "").strip():
        raise ValueError("question is required for kind=question")
    if not interval_minutes and not daily_at:
        raise ValueError("Provide interval_minutes or daily_at")

    nxt = compute_next_run(interval_minutes=interval_minutes, daily_at=daily_at)
    sched = Schedule(
        id=str(uuid.uuid4())[:8],
        name=(name or "Schedule").strip()[:120],
        workspace_id=_safe_name(workspace_id),
        kind=kind,
        question=(question or "").strip(),
        table_name=(table_name or "").strip(),
        interval_minutes=int(interval_minutes) if interval_minutes else None,
        daily_at=daily_at.strip() if daily_at else None,
        channel=channel,
        webhook_url=(webhook_url or "").strip() or None,
        email_to=(email_to or "").strip() or None,
        created_by=created_by,
        enabled=enabled,
        next_run_at=nxt,
    )
    items = load_schedules(sched.workspace_id)
    items.append(sched)
    save_schedules(sched.workspace_id, items)
    return sched


def update_schedule(workspace_id: str, schedule_id: str, **patches: Any) -> Optional[Schedule]:
    items = load_schedules(workspace_id)
    found = None
    for i, s in enumerate(items):
        if s.id == schedule_id:
            data = s.to_dict()
            data.update({k: v for k, v in patches.items() if k in data and k != "id"})
            # recompute next if cadence changed
            if "interval_minutes" in patches or "daily_at" in patches:
                data["next_run_at"] = compute_next_run(
                    interval_minutes=data.get("interval_minutes"),
                    daily_at=data.get("daily_at"),
                )
            found = Schedule.from_dict(data)
            items[i] = found
            break
    if found is None:
        return None
    save_schedules(workspace_id, items)
    return found


def delete_schedule(workspace_id: str, schedule_id: str) -> bool:
    items = load_schedules(workspace_id)
    new = [s for s in items if s.id != schedule_id]
    if len(new) == len(items):
        return False
    save_schedules(workspace_id, new)
    return True


def get_schedule(workspace_id: str, schedule_id: str) -> Optional[Schedule]:
    for s in load_schedules(workspace_id):
        if s.id == schedule_id:
            return s
    return None


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def _append_run_log(entry: Dict[str, Any]) -> None:
    try:
        path = _run_log_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def deliver_slack(text: str, webhook_url: Optional[str] = None) -> Tuple[bool, str]:
    url = (webhook_url or os.getenv("SLACK_WEBHOOK_URL") or "").strip()
    if not url:
        return False, "No Slack webhook URL configured"
    payload = json.dumps({"text": text[:3500]}).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            code = getattr(resp, "status", 200)
            if 200 <= int(code) < 300:
                return True, f"slack:{code}"
            return False, f"slack_http_{code}"
    except urlerror.HTTPError as e:
        return False, f"slack_http_{e.code}"
    except Exception as e:
        return False, f"slack_error:{e}"


def deliver_email(
    subject: str,
    body: str,
    to_addr: Optional[str] = None,
) -> Tuple[bool, str]:
    to_addr = (to_addr or os.getenv("REPORT_EMAIL_TO") or "").strip()
    host = (os.getenv("SMTP_HOST") or "").strip()
    if not to_addr:
        return False, "No email recipient configured"
    if not host:
        return False, "SMTP_HOST not configured"
    port = int(os.getenv("SMTP_PORT") or "587")
    user = (os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("SMTP_PASSWORD") or "").strip()
    from_addr = (os.getenv("SMTP_FROM") or user or "insightforge@localhost").strip()
    use_tls = (os.getenv("SMTP_TLS") or "1").strip() not in ("0", "false", "False")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject[:200]
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body[:20000], "plain", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if use_tls:
                try:
                    server.starttls()
                except Exception:
                    pass
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return True, f"email_sent:{to_addr}"
    except Exception as e:
        return False, f"email_error:{e}"


def deliver(schedule: Schedule, title: str, body: str) -> Tuple[bool, str]:
    channel = (schedule.channel or "log").lower()
    if channel == "slack":
        return deliver_slack(f"*{title}*\n{body}", schedule.webhook_url)
    if channel == "email":
        return deliver_email(title, body, schedule.email_to)
    # log channel – success by definition
    return True, "log_only"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _format_result_summary(result: Any, question: str, table_name: str) -> str:
    lines = [
        f"InsightForgeAI scheduled report",
        f"Time (UTC): {_now_iso()}",
        f"Question: {question}",
        f"Dataset: {table_name or '—'}",
        "",
    ]
    success = bool(getattr(result, "success", False))
    lines.append(f"Success: {success}")
    insight = getattr(result, "insight", None) or getattr(result, "message", None)
    if insight:
        lines.append("")
        lines.append(str(insight)[:2000])
    sql = getattr(result, "sql", None)
    if sql:
        lines.append("")
        lines.append("SQL:")
        lines.append(str(sql)[:1500])
    rdf = getattr(result, "result_df", None)
    if rdf is not None:
        try:
            import pandas as pd
            if isinstance(rdf, pd.DataFrame):
                lines.append("")
                lines.append(f"Result rows: {len(rdf)}")
                if not rdf.empty:
                    preview = rdf.head(5).to_string(index=False)
                    lines.append(preview[:1500])
        except Exception:
            pass
    err = getattr(result, "error", None)
    if err:
        lines.append("")
        lines.append(f"Error: {err}")
    return "\n".join(lines)


def _format_dashboard_summary(widgets: List[Any], workspace_id: str) -> str:
    lines = [
        f"InsightForgeAI dashboard snapshot",
        f"Workspace: {workspace_id}",
        f"Time (UTC): {_now_iso()}",
        f"Widgets: {len(widgets)}",
        "",
    ]
    for i, w in enumerate(widgets):
        title = getattr(w, "title", None) or (w.get("title") if isinstance(w, dict) else None) or f"Widget {i+1}"
        status = getattr(w, "status", None) or (w.get("status") if isinstance(w, dict) else "?")
        insight = getattr(w, "insight", None) or (w.get("insight") if isinstance(w, dict) else None)
        lines.append(f"{i+1}. [{status}] {title}")
        if insight:
            lines.append(f"   {str(insight)[:400]}")
        lines.append("")
    return "\n".join(lines)


def run_schedule(
    schedule: Schedule,
    *,
    workspace: Any = None,
    orchestrator_run=None,
) -> Dict[str, Any]:
    """
    Execute one schedule once.

    Returns dict with success, summary, delivery_ok, delivery_detail, error.
    Updates schedule persistence + audit + metrics side effects.
    """
    from app.core.security import AuditEvent, audit_log, now_iso as sec_now

    result_payload: Dict[str, Any] = {
        "schedule_id": schedule.id,
        "name": schedule.name,
        "success": False,
        "summary": None,
        "delivery_ok": False,
        "delivery_detail": None,
        "error": None,
    }

    try:
        if schedule.kind == "dashboard":
            try:
                from app.core.dashboard import load_widgets, refresh_widget, save_widgets
                from app.core.workspace_store import get_or_create_store

                store = get_or_create_store(schedule.workspace_id)
                widgets = load_widgets(store)
                if workspace is not None and widgets:
                    updated = []
                    for w in widgets:
                        uw, _, _ = refresh_widget(w, workspace)
                        updated.append(uw)
                    save_widgets(store, updated)
                    widgets = updated
                summary = _format_dashboard_summary(widgets, schedule.workspace_id)
                result_payload["success"] = True
                result_payload["summary"] = summary
            except Exception as e:
                result_payload["error"] = f"dashboard_run_failed: {e}"
                summary = f"Dashboard schedule failed: {e}"
                result_payload["summary"] = summary
        else:
            # question path
            if workspace is None:
                result_payload["error"] = "workspace_required"
                result_payload["summary"] = "No workspace available for scheduled question"
            else:
                run_fn = orchestrator_run
                if run_fn is None:
                    try:
                        from app.agents.orchestrator import run_agent as run_fn
                    except Exception as e:
                        result_payload["error"] = f"orchestrator_unavailable: {e}"
                        run_fn = None
                if run_fn is not None:
                    table = schedule.table_name
                    if not table:
                        try:
                            tables = list(workspace.list_datasets() or [])
                            table = tables[0] if tables else ""
                        except Exception:
                            table = ""
                    try:
                        agent_result = run_fn(
                            workspace=workspace,
                            table_name=table,
                            question=schedule.question,
                        )
                        summary = _format_result_summary(agent_result, schedule.question, table)
                        result_payload["success"] = bool(getattr(agent_result, "success", False))
                        result_payload["summary"] = summary
                        if not result_payload["success"]:
                            result_payload["error"] = getattr(agent_result, "error", None) or "ask_failed"
                    except Exception as e:
                        result_payload["error"] = str(e)
                        result_payload["summary"] = f"Schedule run failed: {e}"
                else:
                    result_payload["summary"] = result_payload.get("error") or "No runner"

        # Deliver
        title = f"[InsightForgeAI] {schedule.name}"
        body = result_payload.get("summary") or "(empty)"
        d_ok, d_detail = deliver(schedule, title, body)
        result_payload["delivery_ok"] = d_ok
        result_payload["delivery_detail"] = d_detail
        if not d_ok and schedule.channel != "log":
            # delivery failure is recorded but does not force success=False if content ran
            if result_payload.get("error") is None:
                result_payload["error"] = f"delivery_failed: {d_detail}"

    except Exception as e:
        result_payload["error"] = str(e)
        result_payload["summary"] = f"Unhandled: {e}"

    # Persist schedule state
    try:
        nxt = compute_next_run(
            interval_minutes=schedule.interval_minutes,
            daily_at=schedule.daily_at,
            after=_now_utc(),
        )
        update_schedule(
            schedule.workspace_id,
            schedule.id,
            last_run_at=_now_iso(),
            next_run_at=nxt,
            last_status="ok" if result_payload["success"] else "error",
            last_error=result_payload.get("error"),
            last_summary=(result_payload.get("summary") or "")[:2000],
            run_count=int(schedule.run_count or 0) + 1,
        )
    except Exception:
        pass

    # Audit
    try:
        audit_log(AuditEvent(
            timestamp=sec_now(),
            action="schedule_run",
            principal_id=schedule.created_by or "scheduler",
            role="system",
            table_name=schedule.table_name or None,
            question=(schedule.question or schedule.name)[:500],
            success=bool(result_payload["success"]),
            error_code=None if result_payload["success"] else (result_payload.get("error") or "SCHEDULE_FAILED")[:120],
            extra={
                "schedule_id": schedule.id,
                "channel": schedule.channel,
                "delivery_ok": result_payload.get("delivery_ok"),
                "delivery_detail": result_payload.get("delivery_detail"),
                "kind": schedule.kind,
            },
        ))
    except Exception:
        pass

    # Metrics
    try:
        from app.core.observability import METRICS
        METRICS.record_schedule_run(success=bool(result_payload["success"]))
    except Exception:
        pass

    _append_run_log({
        "ts": _now_iso(),
        **result_payload,
        "workspace_id": schedule.workspace_id,
        "channel": schedule.channel,
    })

    return result_payload


def is_due(schedule: Schedule, now: Optional[datetime] = None) -> bool:
    if not schedule.enabled:
        return False
    now = now or _now_utc()
    if not schedule.next_run_at:
        # first run: if never run, treat as due
        return schedule.last_run_at is None
    try:
        nxt = datetime.strptime(schedule.next_run_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return now >= nxt
    except Exception:
        return False


def run_due_schedules(
    *,
    workspace_factory=None,
    orchestrator_run=None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Scan all workspaces for due schedules and execute them.

    workspace_factory: optional callable(workspace_id) -> Workspace
    """
    results = []
    now = now or _now_utc()
    for sched in list_all_schedules():
        if not is_due(sched, now=now):
            continue
        ws = None
        if workspace_factory is not None:
            try:
                ws = workspace_factory(sched.workspace_id)
            except Exception:
                ws = None
        results.append(run_schedule(sched, workspace=ws, orchestrator_run=orchestrator_run))
    return results


# ---------------------------------------------------------------------------
# Background worker (daemon thread)
# ---------------------------------------------------------------------------

_worker_lock = threading.Lock()
_worker_started = False
_worker_stop = threading.Event()


def start_background_scheduler(
    *,
    interval_sec: int = 60,
    workspace_factory=None,
    orchestrator_run=None,
) -> bool:
    """Start a daemon thread that polls due schedules. Idempotent."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return False

        def _loop():
            while not _worker_stop.is_set():
                try:
                    run_due_schedules(
                        workspace_factory=workspace_factory,
                        orchestrator_run=orchestrator_run,
                    )
                except Exception:
                    pass
                _worker_stop.wait(timeout=max(15, int(interval_sec)))

        t = threading.Thread(target=_loop, name="insightforge-scheduler", daemon=True)
        t.start()
        _worker_started = True
        return True


def stop_background_scheduler() -> None:
    _worker_stop.set()


# ---------------------------------------------------------------------------
# Workspace ownership helpers (multi-user)
# ---------------------------------------------------------------------------

def set_workspace_ownership(
    workspace_id: str,
    *,
    owner_id: Optional[str] = None,
    org_id: Optional[str] = None,
    display_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Update meta.json ownership fields on a durable workspace."""
    from app.core.workspace_store import WorkspaceStore

    store = WorkspaceStore(workspace_id=workspace_id)
    meta = store.load_meta()
    if owner_id is not None:
        meta["owner_id"] = owner_id
    if org_id is not None:
        meta["org_id"] = org_id
    if display_name is not None:
        meta["display_name"] = display_name
    store.save_meta(meta)
    return meta


def workspace_info(workspace_id: str) -> Dict[str, Any]:
    from app.core.workspace_store import WorkspaceStore

    store = WorkspaceStore(workspace_id=workspace_id)
    meta = store.load_meta()
    return {
        "workspace_id": store.workspace_id,
        "display_name": meta.get("display_name") or store.workspace_id,
        "owner_id": meta.get("owner_id"),
        "org_id": meta.get("org_id"),
        "datasets": store.list_saved_datasets(),
        "dataset_count": len(store.list_saved_datasets()),
        "chat_turns": len(store.load_chat_history()),
        "insight_count": len(load_insights(store.workspace_id)),
        "schedule_count": len(load_schedules(store.workspace_id)),
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
    }


def list_workspace_infos() -> List[Dict[str, Any]]:
    from app.core.workspace_store import list_workspaces

    return [workspace_info(w) for w in list_workspaces()]


__all__ = [
    "SavedInsight",
    "Schedule",
    "load_insights",
    "save_insights",
    "add_insight",
    "remove_insight",
    "load_schedules",
    "save_schedules",
    "list_all_schedules",
    "create_schedule",
    "update_schedule",
    "delete_schedule",
    "get_schedule",
    "compute_next_run",
    "is_due",
    "run_schedule",
    "run_due_schedules",
    "deliver",
    "deliver_slack",
    "deliver_email",
    "start_background_scheduler",
    "stop_background_scheduler",
    "set_workspace_ownership",
    "workspace_info",
    "list_workspace_infos",
]
