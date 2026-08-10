"""
InsightForgeAI – Dashboards & Pinned Widgets (Phase 4.4)

Turn conversational answers into durable, shareable dashboard widgets.

- Pin a chat turn (SQL + question + insight + chart metadata)
- Persist under data/workspaces/{id}/dashboard/widgets.json
- Refresh by re-executing stored SQL (preferred) or light re-ask
- Detect staleness when table is missing or SQL fails after schema change
- Never stores Plotly figures or full DataFrames on disk (re-render on demand)

Free-stack: dataclasses + json + existing Workspace / DuckDB.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_name(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_\-]+", "_", (name or "unnamed").strip()) or "unnamed"


@dataclass
class DashboardWidget:
    """Serializable pin from a successful chat turn."""

    id: str
    title: str
    question: str
    table_name: str
    sql: Optional[str] = None
    insight: Optional[str] = None
    chart_type: Optional[str] = None
    chart_reason: Optional[str] = None
    grounding_line: Optional[str] = None
    citations: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    last_refreshed: Optional[str] = None
    status: str = "ok"  # ok | stale | error
    error: Optional[str] = None
    # Lightweight snapshot of last successful result shape (not the data)
    last_row_count: Optional[int] = None
    last_col_count: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DashboardWidget":
        return cls(
            id=str(d.get("id") or str(uuid.uuid4())[:8]),
            title=str(d.get("title") or d.get("question") or "Untitled"),
            question=str(d.get("question") or ""),
            table_name=str(d.get("table_name") or ""),
            sql=d.get("sql"),
            insight=d.get("insight"),
            chart_type=d.get("chart_type"),
            chart_reason=d.get("chart_reason"),
            grounding_line=d.get("grounding_line"),
            citations=list(d.get("citations") or []),
            created_at=str(d.get("created_at") or _now_iso()),
            last_refreshed=d.get("last_refreshed"),
            status=str(d.get("status") or "ok"),
            error=d.get("error"),
            last_row_count=d.get("last_row_count"),
            last_col_count=d.get("last_col_count"),
        )


def pin_from_turn(
    turn: Dict[str, Any],
    *,
    table_name: Optional[str] = None,
    title: Optional[str] = None,
) -> DashboardWidget:
    """
    Create a DashboardWidget from a chat history turn dict.

    Only call for successful turns that have SQL or a result.
    """
    q = (turn.get("question") or "").strip()
    tbl = table_name or turn.get("table_name") or ""
    sql = turn.get("sql")
    insight = turn.get("insight") or turn.get("message")
    chart_type = turn.get("chart_type")
    chart_reason = turn.get("chart_reason")
    grounding = turn.get("grounding_line")
    citations = list(turn.get("citations") or [])

    # Prefer a short title from insight first line or question
    if title:
        wtitle = title.strip()[:120]
    elif insight:
        first = str(insight).strip().split("\n")[0].strip()
        wtitle = (first[:100] + "…") if len(first) > 100 else first
    else:
        wtitle = q[:100] if q else "Pinned insight"

    row_count = col_count = None
    rdf = turn.get("result_df")
    if isinstance(rdf, pd.DataFrame) and not rdf.empty:
        row_count = int(len(rdf))
        col_count = int(len(rdf.columns))

    return DashboardWidget(
        id=str(uuid.uuid4())[:8],
        title=wtitle or "Pinned",
        question=q,
        table_name=tbl,
        sql=sql,
        insight=insight,
        chart_type=chart_type,
        chart_reason=chart_reason,
        grounding_line=grounding,
        citations=citations,
        created_at=_now_iso(),
        last_refreshed=_now_iso(),
        status="ok",
        last_row_count=row_count,
        last_col_count=col_count,
    )


def refresh_widget(
    widget: DashboardWidget,
    workspace: Any,
    *,
    limit: int = 500,
) -> Tuple[DashboardWidget, Optional[pd.DataFrame], Optional[str]]:
    """
    Re-execute the widget against the current workspace.

    Returns (updated_widget, result_df or None, error_message or None).

    Strategy:
    1. If table is missing → status=stale
    2. If SQL present → execute via workspace.execute_sql (guarded)
    3. Else → status=error (cannot refresh without SQL)
    """
    w = DashboardWidget.from_dict(widget.to_dict())  # copy

    tables = []
    try:
        tables = list(workspace.list_datasets() or [])
    except Exception:
        pass

    if w.table_name and w.table_name not in tables:
        w.status = "stale"
        w.error = f"Dataset `{w.table_name}` is no longer in the workspace."
        w.last_refreshed = _now_iso()
        return w, None, w.error

    if not (w.sql or "").strip():
        w.status = "error"
        w.error = "No SQL stored on this widget – cannot refresh."
        w.last_refreshed = _now_iso()
        return w, None, w.error

    try:
        df, err = workspace.execute_sql(w.sql, limit=limit)
        if err:
            w.status = "stale"
            w.error = f"SQL failed after schema/data change: {err}"
            w.last_refreshed = _now_iso()
            return w, None, w.error
        w.status = "ok"
        w.error = None
        w.last_refreshed = _now_iso()
        if isinstance(df, pd.DataFrame):
            w.last_row_count = int(len(df))
            w.last_col_count = int(len(df.columns))
        return w, df, None
    except Exception as e:
        w.status = "error"
        w.error = str(e)
        w.last_refreshed = _now_iso()
        return w, None, str(e)


# ---------------------------------------------------------------------------
# Persistence (workspace-scoped)
# ---------------------------------------------------------------------------

def _dashboard_dir(store_or_root: Any) -> Path:
    """Accept a WorkspaceStore instance or a Path root."""
    if hasattr(store_or_root, "root"):
        root = Path(store_or_root.root)
    else:
        root = Path(store_or_root)
    d = root / "dashboard"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _widgets_path(store_or_root: Any) -> Path:
    return _dashboard_dir(store_or_root) / "widgets.json"


def load_widgets(store_or_root: Any) -> List[DashboardWidget]:
    path = _widgets_path(store_or_root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        return [DashboardWidget.from_dict(x) for x in raw if isinstance(x, dict)]
    except Exception:
        return []


def save_widgets(store_or_root: Any, widgets: List[DashboardWidget]) -> None:
    path = _widgets_path(store_or_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [w.to_dict() for w in widgets]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def add_widget(store_or_root: Any, widget: DashboardWidget) -> List[DashboardWidget]:
    widgets = load_widgets(store_or_root)
    # Replace if same id already present
    widgets = [w for w in widgets if w.id != widget.id]
    widgets.append(widget)
    save_widgets(store_or_root, widgets)
    return widgets


def remove_widget(store_or_root: Any, widget_id: str) -> List[DashboardWidget]:
    widgets = [w for w in load_widgets(store_or_root) if w.id != widget_id]
    save_widgets(store_or_root, widgets)
    return widgets


def clear_dashboard(store_or_root: Any) -> None:
    save_widgets(store_or_root, [])


def list_widget_summaries(store_or_root: Any) -> List[Dict[str, Any]]:
    return [
        {
            "id": w.id,
            "title": w.title,
            "table_name": w.table_name,
            "status": w.status,
            "chart_type": w.chart_type,
            "created_at": w.created_at,
            "last_refreshed": w.last_refreshed,
            "has_sql": bool(w.sql),
        }
        for w in load_widgets(store_or_root)
    ]


__all__ = [
    "DashboardWidget",
    "pin_from_turn",
    "refresh_widget",
    "load_widgets",
    "save_widgets",
    "add_widget",
    "remove_widget",
    "clear_dashboard",
    "list_widget_summaries",
]
