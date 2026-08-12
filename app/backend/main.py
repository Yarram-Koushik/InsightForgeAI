"""
InsightForgeAI – FastAPI Backend Boundary (Phase 3.4–3.7 + 4.5)

Endpoints: / /health /ready /metrics /datasets /upload /ask /sql /audit
           /workspaces /schedules /insights (Phase 4.5)
Phase 3.5 auth + Phase 3.7 observability + Phase 4.5 scheduling.
"""

from __future__ import annotations

try:
    from pathlib import Path as _EnvPath
    from dotenv import load_dotenv
    _env_file = _EnvPath(__file__).resolve().parents[2] / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
    else:
        load_dotenv(override=False)
except Exception:
    pass

import io
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

_BACKEND_DIR = Path(__file__).resolve().parent
_candidates = [
    _BACKEND_DIR.parents[1] if len(_BACKEND_DIR.parents) > 1 else _BACKEND_DIR,
    _BACKEND_DIR.parents[2] if len(_BACKEND_DIR.parents) > 2 else _BACKEND_DIR,
    Path.cwd(),
    Path.cwd() / "app",
]
_APP_DIR = None
_ROOT = None
for c in _candidates:
    if (c / "core" / "data_manager.py").exists():
        _APP_DIR = c
        _ROOT = c.parent if c.name == "app" else c
        break
    if (c / "app" / "core" / "data_manager.py").exists():
        _APP_DIR = c / "app"
        _ROOT = c
        break
if _APP_DIR is None:
    _APP_DIR = _BACKEND_DIR.parent
    _ROOT = _APP_DIR.parent if _APP_DIR.name == "app" else _APP_DIR

for p in (_ROOT, _APP_DIR, _BACKEND_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load_core():
    import importlib.util

    def _mod(name: str, path: Path):
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
        return m

    core = _APP_DIR / "core"
    dm = _mod("data_manager", core / "data_manager.py")
    ingestion = _mod("ingestion", core / "ingestion.py")
    cleaning = _mod("cleaning", core / "cleaning.py")
    return dm, ingestion, cleaning


def _load_orchestrator():
    import importlib.util
    path = _APP_DIR / "agents" / "orchestrator.py"
    if not path.exists():
        raise RuntimeError("orchestrator.py not found")
    spec = importlib.util.spec_from_file_location("orchestrator_api", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["orchestrator_api"] = mod
    spec.loader.exec_module(mod)
    return mod


try:
    from app.backend.schemas import (
        AskRequest, AskResponse, DatasetInfo, DatasetsResponse,
        ErrorDetail, ErrorResponse, HealthResponse, SchemaResponse,
        SqlRequest, SqlResponse, UploadResponse,
        ScheduleCreateRequest, ScheduleUpdateRequest, ScheduleResponse,
        SchedulesListResponse, InsightCreateRequest, WorkspaceInfoResponse,
    )
except ImportError:
    import importlib.util as _ilu
    _sch_path = _BACKEND_DIR / "schemas.py"
    _sch_spec = _ilu.spec_from_file_location("api_schemas", _sch_path)
    _sch = _ilu.module_from_spec(_sch_spec)
    sys.modules["api_schemas"] = _sch
    _sch_spec.loader.exec_module(_sch)
    AskRequest = _sch.AskRequest
    AskResponse = _sch.AskResponse
    DatasetInfo = _sch.DatasetInfo
    DatasetsResponse = _sch.DatasetsResponse
    ErrorDetail = _sch.ErrorDetail
    ErrorResponse = _sch.ErrorResponse
    HealthResponse = _sch.HealthResponse
    SchemaResponse = _sch.SchemaResponse
    SqlRequest = _sch.SqlRequest
    SqlResponse = _sch.SqlResponse
    UploadResponse = _sch.UploadResponse
    ScheduleCreateRequest = getattr(_sch, "ScheduleCreateRequest", None)
    ScheduleUpdateRequest = getattr(_sch, "ScheduleUpdateRequest", None)
    ScheduleResponse = getattr(_sch, "ScheduleResponse", None)
    SchedulesListResponse = getattr(_sch, "SchedulesListResponse", None)
    InsightCreateRequest = getattr(_sch, "InsightCreateRequest", None)
    WorkspaceInfoResponse = getattr(_sch, "WorkspaceInfoResponse", None)

try:
    from app.core.security import (
        Role, Principal, auth_enabled, authenticate, require_role,
        AuditEvent, audit_log, read_audit, now_iso, validate_upload, MAX_UPLOAD_BYTES,
    )
except ImportError:
    import importlib.util as _ilu
    _sec_path = (_APP_DIR / "core" / "security.py") if _APP_DIR else Path("security.py")
    if not _sec_path.exists():
        _sec_path = Path(__file__).resolve().parent.parent / "core" / "security.py"
    _sec_spec = _ilu.spec_from_file_location("api_security", _sec_path)
    _sec = _ilu.module_from_spec(_sec_spec)
    sys.modules["api_security"] = _sec
    _sec_spec.loader.exec_module(_sec)
    Role = _sec.Role
    Principal = _sec.Principal
    auth_enabled = _sec.auth_enabled
    authenticate = _sec.authenticate
    require_role = _sec.require_role
    AuditEvent = _sec.AuditEvent
    audit_log = _sec.audit_log
    read_audit = _sec.read_audit
    now_iso = _sec.now_iso
    validate_upload = _sec.validate_upload
    MAX_UPLOAD_BYTES = _sec.MAX_UPLOAD_BYTES

try:
    from app.core.observability import (
        log_event, METRICS, RATE_LIMITER, readiness_checks,
    )
except ImportError:
    import importlib.util as _ilu
    _obs_path = (_APP_DIR / "core" / "observability.py") if _APP_DIR else Path("observability.py")
    if not _obs_path.exists():
        _obs_path = Path(__file__).resolve().parent.parent / "core" / "observability.py"
    _obs_spec = _ilu.spec_from_file_location("api_obs", _obs_path)
    _obs = _ilu.module_from_spec(_obs_spec)
    sys.modules["api_obs"] = _obs
    _obs_spec.loader.exec_module(_obs)
    log_event = _obs.log_event
    METRICS = _obs.METRICS
    RATE_LIMITER = _obs.RATE_LIMITER
    readiness_checks = _obs.readiness_checks

app = FastAPI(
    title="InsightForgeAI API",
    description="Backend boundary for the multi-agent BI assistant (Phase 3.4–4.5)",
    version="0.4.5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    path = request.url.path
    start = time.perf_counter()
    if path not in ("/health", "/ready", "/metrics", "/"):
        key = request.headers.get("x-api-key") or ""
        if not key:
            auth = request.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                key = auth[7:].strip()
        if not key and request.client:
            key = f"ip:{request.client.host}"
        if not key:
            key = "anonymous"
        allowed, retry_after = RATE_LIMITER.allow(key)
        if not allowed:
            log_event("rate_limited", path=path, key=key[:32], retry_after=retry_after)
            METRICS.record_request(path, 429, (time.perf_counter() - start) * 1000)
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests",
                        "detail": f"Retry after {retry_after}s",
                    },
                },
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        log_event("request_error", path=path, method=request.method, error=str(e)[:300], latency_ms=round(latency, 1))
        METRICS.record_request(path, 500, latency)
        raise
    latency = (time.perf_counter() - start) * 1000
    METRICS.record_request(path, status, latency)
    log_event("request", path=path, method=request.method, status=status, latency_ms=round(latency, 1))
    return response


_workspace = None
_dm = None
_ingestion = None
_cleaning = None


def get_workspace():
    global _workspace, _dm, _ingestion, _cleaning
    if _workspace is None:
        try:
            _dm, _ingestion, _cleaning = _load_core()
            _workspace = _dm.Workspace()
        except Exception as e:
            raise RuntimeError(f"Could not initialise Workspace: {e}") from e
    return _workspace


def set_workspace(ws) -> None:
    global _workspace
    _workspace = ws


def _df_to_records(df: Optional[pd.DataFrame], max_rows: int = 500) -> tuple:
    if df is None or not isinstance(df, pd.DataFrame):
        return None, None, 0
    capped = df.head(max_rows)
    records = capped.astype(object).where(pd.notnull(capped), None).to_dict(orient="records")
    cols = list(capped.columns.astype(str))
    return records, cols, len(df)


def _agent_result_to_response(result: Any) -> AskResponse:
    records, cols, n = _df_to_records(getattr(result, "result_df", None))
    f_records, _, _ = _df_to_records(getattr(result, "forecast_df", None))
    return AskResponse(
        success=bool(getattr(result, "success", False)),
        question=str(getattr(result, "question", "")),
        intent=str(getattr(result, "intent", "unknown")),
        intent_reason=getattr(result, "intent_reason", None),
        message=getattr(result, "message", None),
        sql=getattr(result, "sql", None),
        insight=getattr(result, "insight", None),
        clarify_questions=list(getattr(result, "clarify_questions", []) or []),
        result=records,
        result_columns=cols,
        result_row_count=n if records is not None else None,
        forecast=f_records,
        forecast_method=getattr(result, "forecast_method", None),
        forecast_horizon=getattr(result, "forecast_horizon", None),
        trend_summary=getattr(result, "trend_summary", None),
        anomalies=list(getattr(result, "anomalies", []) or []),
        chart_type=getattr(result, "chart_type", None),
        chart_reason=getattr(result, "chart_reason", None),
        steps=list(getattr(result, "steps", []) or []),
        warnings=list(getattr(result, "warnings", []) or []),
        error=getattr(result, "error", None),
        provider=getattr(result, "provider", None),
        model=getattr(result, "model", None),
    )


def _error(code: str, message: str, status: int = 400, detail: str = None):
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(
            error=ErrorDetail(code=code, message=message, detail=detail)
        ).model_dump(),
    )


def _extract_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.query_params.get("api_key")


def get_principal(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> Optional[Principal]:
    key = _extract_api_key(request, x_api_key, authorization)
    return authenticate(key)


def require_auth(min_role: Role = Role.VIEWER):
    def _dep(principal: Optional[Principal] = Depends(get_principal)) -> Optional[Principal]:
        if not auth_enabled():
            return principal
        if principal is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "Valid API key required (X-API-Key or Bearer)"},
            )
        if not require_role(principal, min_role):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "FORBIDDEN",
                    "message": f"Role '{principal.role.value}' cannot perform this action (needs {min_role.value})",
                },
            )
        return principal
    return _dep


def _client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _schedule_to_response(s) -> ScheduleResponse:
    return ScheduleResponse(
        id=s.id,
        name=s.name,
        workspace_id=s.workspace_id,
        kind=s.kind,
        question=s.question or "",
        table_name=s.table_name or "",
        interval_minutes=s.interval_minutes,
        daily_at=s.daily_at,
        channel=s.channel,
        enabled=s.enabled,
        created_by=s.created_by,
        created_at=s.created_at,
        last_run_at=s.last_run_at,
        next_run_at=s.next_run_at,
        last_status=s.last_status,
        last_error=s.last_error,
        run_count=s.run_count,
    )


@app.get("/")
def root():
    return {
        "service": "InsightForgeAI",
        "version": "0.4.5",
        "docs": "/docs",
        "health": "/health",
        "ready": "/ready",
        "metrics": "/metrics",
        "schedules": "/schedules",
        "workspaces": "/workspaces",
    }


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(version="0.4.5")


@app.get("/ready")
def ready():
    checks = readiness_checks()
    status = 200 if checks.get("ready") else 503
    return JSONResponse(status_code=status, content=checks)


@app.get("/metrics")
def metrics():
    return METRICS.snapshot()


@app.get("/datasets", response_model=DatasetsResponse)
def list_datasets(
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.VIEWER)),
):
    ws = get_workspace()
    items = []
    for name in ws.list_datasets():
        rec = ws.get(name)
        if rec is None:
            continue
        items.append(
            DatasetInfo(
                name=name,
                source_filename=getattr(rec, "source_filename", None),
                rows=rec.metadata.get("cleaned_rows") or rec.metadata.get("original_rows"),
                columns=rec.metadata.get("cleaned_columns") or rec.metadata.get("original_columns"),
                id=getattr(rec, "id", None),
            )
        )
    return DatasetsResponse(datasets=items, count=len(items))


@app.get("/datasets/{name}/schema", response_model=SchemaResponse)
def dataset_schema(
    name: str,
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.VIEWER)),
):
    ws = get_workspace()
    if name not in ws.list_datasets():
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found")
    try:
        schema_df = ws.get_table_schema(name)
        if "error" in schema_df.columns:
            raise HTTPException(status_code=500, detail=str(schema_df["error"].iloc[0]))
        cols = schema_df.to_dict(orient="records")
        return SchemaResponse(table_name=name, columns=cols)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/datasets/upload", response_model=UploadResponse)
async def upload_dataset(
    request: Request,
    file: UploadFile = File(...),
    principal: Optional[Principal] = Depends(require_auth(Role.ANALYST)),
):
    ws = get_workspace()
    global _ingestion, _cleaning
    if _ingestion is None or _cleaning is None:
        _, _ingestion, _cleaning = _load_core()
    filename = file.filename or "upload.csv"
    try:
        content = await file.read()
        reject = validate_upload(filename, file.content_type, len(content) if content else 0)
        if reject == "EMPTY_FILE":
            return _error("EMPTY_FILE", "Uploaded file is empty", 400)
        if reject == "FILE_TOO_LARGE":
            return _error("FILE_TOO_LARGE", f"File exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB limit", 413)
        if reject and reject.startswith("UNSUPPORTED_TYPE"):
            return _error("UNSUPPORTED_TYPE", f"Unsupported file type: {reject.split(':',1)[-1]}", 415)
        bio = io.BytesIO(content)
        bio.name = filename
        try:
            if filename.lower().endswith((".xlsx", ".xls")):
                raw_df = _ingestion.read_file(bio, sheet_name=0)
            else:
                raw_df = _ingestion.read_file(bio)
        except Exception as e:
            return _error("PARSE_FAILED", f"Could not parse file: {e}", 400)
        if raw_df is None or raw_df.empty:
            return _error("EMPTY_DATA", "Parsed DataFrame is empty", 400)
        table_name = _ingestion.make_safe_table_name(Path(filename).stem)
        final_name = ws.add_dataset(name=table_name, raw_df=raw_df, source_filename=filename)
        issues = _cleaning.detect_cleaning_issues(raw_df)
        cleaned_df, change_log = _cleaning.apply_safe_cleaning(raw_df, issues)
        record = ws.get(final_name)
        record.apply_cleaning(cleaned_df, issues, change_log)
        ws.register_in_duckdb(final_name)
        audit_log(AuditEvent(
            timestamp=now_iso(), action="upload",
            principal_id=(principal.key_id if principal else "anonymous"),
            role=(principal.role.value if principal else "none"),
            table_name=final_name, success=True, result_rows=len(cleaned_df),
            ip=_client_ip(request),
            extra={"filename": filename, "columns": len(cleaned_df.columns)},
        ))
        return UploadResponse(
            success=True, table_name=final_name, rows=len(cleaned_df),
            columns=len(cleaned_df.columns),
            message=f"Loaded and cleaned as `{final_name}`",
            warnings=[f"{len(issues)} issue(s) detected"] if issues else [],
        )
    except Exception as e:
        return _error("UPLOAD_FAILED", str(e), 500, detail=traceback.format_exc()[-500:])


@app.post("/ask", response_model=AskResponse)
def ask(
    req: AskRequest,
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.ANALYST)),
):
    ws = get_workspace()
    if req.table_name not in ws.list_datasets():
        audit_log(AuditEvent(
            timestamp=now_iso(), action="ask",
            principal_id=(principal.key_id if principal else "anonymous"),
            role=(principal.role.value if principal else "none"),
            table_name=req.table_name, question=req.question[:500],
            success=False, error_code="DATASET_NOT_FOUND", ip=_client_ip(request),
        ))
        return _error("DATASET_NOT_FOUND", f"Dataset '{req.table_name}' is not loaded.", 404)
    try:
        orch = _load_orchestrator()
        result = orch.run_agent(
            workspace=ws, table_name=req.table_name,
            question=req.question, history=req.history,
        )
        resp = _agent_result_to_response(result)
        audit_log(AuditEvent(
            timestamp=now_iso(), action="ask",
            principal_id=(principal.key_id if principal else "anonymous"),
            role=(principal.role.value if principal else "none"),
            table_name=req.table_name, question=req.question[:500],
            sql=(resp.sql[:2000] if resp.sql else None),
            success=resp.success, intent=resp.intent,
            result_rows=resp.result_row_count,
            error_code=None if resp.success else (resp.error or "ASK_FAILED"),
            ip=_client_ip(request),
            extra={"provider": resp.provider, "model": resp.model},
        ))
        return resp
    except Exception as e:
        audit_log(AuditEvent(
            timestamp=now_iso(), action="ask",
            principal_id=(principal.key_id if principal else "anonymous"),
            role=(principal.role.value if principal else "none"),
            table_name=req.table_name, question=req.question[:500],
            success=False, error_code="AGENT_FAILED", ip=_client_ip(request),
            extra={"exception": str(e)[:300]},
        ))
        return _error("AGENT_FAILED", f"Orchestrator failed: {e}", 500, detail=traceback.format_exc()[-800:])


@app.post("/sql", response_model=SqlResponse)
def run_sql(
    req: SqlRequest,
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.VIEWER)),
):
    ws = get_workspace()
    try:
        df, err = ws.execute_sql(req.sql)
        if err:
            audit_log(AuditEvent(
                timestamp=now_iso(), action="sql",
                principal_id=(principal.key_id if principal else "anonymous"),
                role=(principal.role.value if principal else "none"),
                table_name=req.table_name, sql=req.sql[:2000],
                success=False, error_code="SQL_ERROR", ip=_client_ip(request),
                extra={"error": err[:300]},
            ))
            return SqlResponse(success=False, sql=req.sql, error=err)
        records, cols, n = _df_to_records(df)
        audit_log(AuditEvent(
            timestamp=now_iso(), action="sql",
            principal_id=(principal.key_id if principal else "anonymous"),
            role=(principal.role.value if principal else "none"),
            table_name=req.table_name, sql=req.sql[:2000],
            success=True, result_rows=n, ip=_client_ip(request),
        ))
        return SqlResponse(success=True, sql=req.sql, result=records, result_columns=cols, result_row_count=n)
    except Exception as e:
        return SqlResponse(success=False, sql=req.sql, error=str(e))


@app.get("/audit")
def get_audit(
    request: Request,
    limit: int = 100,
    day: Optional[str] = None,
    principal: Optional[Principal] = Depends(require_auth(Role.ADMIN)),
):
    events = read_audit(limit=min(limit, 1000), day=day)
    return {"count": len(events), "events": events}


# ---------------------------------------------------------------------------
# Phase 4.5 – Workspaces, schedules, saved insights
# ---------------------------------------------------------------------------

@app.get("/workspaces")
def list_workspaces_api(
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.VIEWER)),
):
    try:
        from app.core.scheduling import list_workspace_infos
        infos = list_workspace_infos()
        return {"workspaces": infos, "count": len(infos)}
    except Exception as e:
        return {"workspaces": [], "count": 0, "error": str(e)}


@app.get("/workspaces/{workspace_id}")
def get_workspace_api(
    workspace_id: str,
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.VIEWER)),
):
    try:
        from app.core.scheduling import workspace_info
        return workspace_info(workspace_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/schedules", response_model=SchedulesListResponse)
def list_schedules_api(
    request: Request,
    workspace_id: Optional[str] = None,
    principal: Optional[Principal] = Depends(require_auth(Role.VIEWER)),
):
    from app.core.scheduling import load_schedules, list_all_schedules
    if workspace_id:
        items = load_schedules(workspace_id)
    else:
        items = list_all_schedules()
    return SchedulesListResponse(
        schedules=[_schedule_to_response(s) for s in items],
        count=len(items),
    )


@app.post("/schedules", response_model=ScheduleResponse)
def create_schedule_api(
    req: ScheduleCreateRequest,
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.ADMIN)),
):
    from app.core.scheduling import create_schedule
    try:
        created_by = principal.key_id if principal else "anonymous"
        sched = create_schedule(
            name=req.name,
            workspace_id=req.workspace_id or "default",
            kind=req.kind or "question",
            question=req.question or "",
            table_name=req.table_name or "",
            interval_minutes=req.interval_minutes,
            daily_at=req.daily_at,
            channel=req.channel or "log",
            webhook_url=req.webhook_url,
            email_to=req.email_to,
            created_by=created_by,
            enabled=bool(req.enabled),
        )
        audit_log(AuditEvent(
            timestamp=now_iso(), action="schedule_create",
            principal_id=created_by,
            role=(principal.role.value if principal else "none"),
            table_name=req.table_name or None,
            question=(req.question or req.name)[:500],
            success=True,
            ip=_client_ip(request),
            extra={"schedule_id": sched.id, "channel": sched.channel, "kind": sched.kind},
        ))
        return _schedule_to_response(sched)
    except ValueError as e:
        return _error("INVALID_SCHEDULE", str(e), 400)
    except Exception as e:
        return _error("SCHEDULE_CREATE_FAILED", str(e), 500)


@app.patch("/schedules/{workspace_id}/{schedule_id}", response_model=ScheduleResponse)
def update_schedule_api(
    workspace_id: str,
    schedule_id: str,
    req: ScheduleUpdateRequest,
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.ADMIN)),
):
    from app.core.scheduling import update_schedule
    patches = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
    updated = update_schedule(workspace_id, schedule_id, **patches)
    if updated is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    audit_log(AuditEvent(
        timestamp=now_iso(), action="schedule_update",
        principal_id=(principal.key_id if principal else "anonymous"),
        role=(principal.role.value if principal else "none"),
        success=True, ip=_client_ip(request),
        extra={"schedule_id": schedule_id, "patches": list(patches.keys())},
    ))
    return _schedule_to_response(updated)


@app.delete("/schedules/{workspace_id}/{schedule_id}")
def delete_schedule_api(
    workspace_id: str,
    schedule_id: str,
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.ADMIN)),
):
    from app.core.scheduling import delete_schedule
    ok = delete_schedule(workspace_id, schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    audit_log(AuditEvent(
        timestamp=now_iso(), action="schedule_delete",
        principal_id=(principal.key_id if principal else "anonymous"),
        role=(principal.role.value if principal else "none"),
        success=True, ip=_client_ip(request),
        extra={"schedule_id": schedule_id, "workspace_id": workspace_id},
    ))
    return {"success": True, "deleted": schedule_id}


@app.post("/schedules/{workspace_id}/{schedule_id}/run")
def run_schedule_now_api(
    workspace_id: str,
    schedule_id: str,
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.ADMIN)),
):
    from app.core.scheduling import get_schedule, run_schedule
    sched = get_schedule(workspace_id, schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    ws = get_workspace()
    try:
        orch = _load_orchestrator()
        run_fn = orch.run_agent
    except Exception:
        run_fn = None
    result = run_schedule(sched, workspace=ws, orchestrator_run=run_fn)
    return result


@app.get("/insights")
def list_insights_api(
    request: Request,
    workspace_id: str = "default",
    principal: Optional[Principal] = Depends(require_auth(Role.VIEWER)),
):
    from app.core.scheduling import load_insights
    items = load_insights(workspace_id)
    return {"insights": [i.to_dict() for i in items], "count": len(items)}


@app.post("/insights")
def create_insight_api(
    req: InsightCreateRequest,
    request: Request,
    principal: Optional[Principal] = Depends(require_auth(Role.ANALYST)),
):
    from app.core.scheduling import SavedInsight, add_insight
    import uuid as _uuid
    insight = SavedInsight(
        id=str(_uuid.uuid4())[:8],
        name=req.name,
        question=req.question,
        table_name=req.table_name,
        workspace_id=req.workspace_id or "default",
        description=req.description or "",
        created_by=(principal.key_id if principal else "anonymous"),
        tags=list(req.tags or []),
    )
    add_insight(insight)
    audit_log(AuditEvent(
        timestamp=now_iso(), action="insight_save",
        principal_id=insight.created_by,
        role=(principal.role.value if principal else "none"),
        table_name=req.table_name,
        question=req.question[:500],
        success=True, ip=_client_ip(request),
        extra={"insight_id": insight.id},
    ))
    return insight.to_dict()


@app.on_event("startup")
def _startup_scheduler():
    """Optional in-process due-schedule poller (least privilege, daemon)."""
    if (os.getenv("INSIGHTFORGE_SCHEDULER") or "1").strip() in ("0", "false", "False"):
        log_event("scheduler_disabled")
        return
    try:
        from app.core.scheduling import start_background_scheduler

        def _ws_factory(workspace_id: str):
            # Shared process workspace – durable restore can be added later
            return get_workspace()

        started = start_background_scheduler(
            interval_sec=int(os.getenv("SCHEDULER_POLL_SEC", "60")),
            workspace_factory=_ws_factory,
        )
        log_event("scheduler_started", started=started)
    except Exception as e:
        log_event("scheduler_start_failed", error=str(e)[:300])


def create_app() -> FastAPI:
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
