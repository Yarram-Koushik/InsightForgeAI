"""
InsightForgeAI – FastAPI Backend Boundary (Phase 3.4)

Makes the multi-agent orchestrator callable over HTTP so Streamlit
(or any other client) is just one front-end.

Endpoints
---------
  GET  /health
  GET  /datasets
  GET  /datasets/{name}/schema
  POST /datasets/upload
  POST /ask
  POST /sql

Design
------
- Single process-wide Workspace (can later become per-workspace_id using
  the durable store from Phase 3.3).
- AgentResult is serialized to JSON-safe structures (DataFrames → records).
- Structured error codes for partial failures.
- Free-stack: FastAPI + uvicorn (already in requirements).
"""

from __future__ import annotations

import io
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Path bootstrap (works both as package and when run directly)
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent

# Real layout: <root>/app/backend/main.py
# Also support running from artifacts or flat test dirs
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

# ---------------------------------------------------------------------------
# Lazy / robust imports of core + agents
# ---------------------------------------------------------------------------
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


# Schemas – support both package import and direct file load
try:
    from app.backend.schemas import (
        AskRequest,
        AskResponse,
        DatasetInfo,
        DatasetsResponse,
        ErrorDetail,
        ErrorResponse,
        HealthResponse,
        SchemaResponse,
        SqlRequest,
        SqlResponse,
        UploadResponse,
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


# ---------------------------------------------------------------------------
# App + shared workspace
# ---------------------------------------------------------------------------

app = FastAPI(
    title="InsightForgeAI API",
    description="Backend boundary for the multi-agent BI assistant (Phase 3.4)",
    version="0.3.4",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Process-wide workspace (Phase 3.3 durable store can be wired later)
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
            raise RuntimeError(
                f"Could not initialise Workspace (is app/core present?): {e}"
            ) from e
    return _workspace


def set_workspace(ws) -> None:
    """Test helper – inject a pre-built workspace."""
    global _workspace
    _workspace = ws


def _df_to_records(df: Optional[pd.DataFrame], max_rows: int = 500) -> tuple:
    if df is None or not isinstance(df, pd.DataFrame):
        return None, None, 0
    capped = df.head(max_rows)
    # Convert non-JSON-native types
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse()


@app.get("/datasets", response_model=DatasetsResponse)
def list_datasets():
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
def dataset_schema(name: str):
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
async def upload_dataset(file: UploadFile = File(...)):
    """
    Upload a CSV / Excel / JSON / Parquet file, clean it, register in DuckDB.
    """
    ws = get_workspace()
    global _ingestion, _cleaning
    if _ingestion is None or _cleaning is None:
        _, _ingestion, _cleaning = _load_core()

    filename = file.filename or "upload.csv"
    try:
        content = await file.read()
        if not content:
            return _error("EMPTY_FILE", "Uploaded file is empty", 400)

        # Size guard (50 MB)
        if len(content) > 50 * 1024 * 1024:
            return _error("FILE_TOO_LARGE", "File exceeds 50 MB limit", 413)

        bio = io.BytesIO(content)
        bio.name = filename  # some readers use .name

        # Re-use existing ingestion helpers
        try:
            if filename.lower().endswith((".xlsx", ".xls")):
                # First sheet only for the simple API path
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

        return UploadResponse(
            success=True,
            table_name=final_name,
            rows=len(cleaned_df),
            columns=len(cleaned_df.columns),
            message=f"Loaded and cleaned as `{final_name}`",
            warnings=[f"{len(issues)} issue(s) detected" ] if issues else [],
        )
    except Exception as e:
        return _error("UPLOAD_FAILED", str(e), 500, detail=traceback.format_exc()[-500:])


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    """
    Run the full multi-agent pipeline (router → SQL → insight/forecast/viz).
    """
    ws = get_workspace()
    if req.table_name not in ws.list_datasets():
        return _error(
            "DATASET_NOT_FOUND",
            f"Dataset '{req.table_name}' is not loaded. Upload or select a dataset first.",
            404,
        )

    try:
        orch = _load_orchestrator()
        result = orch.run_agent(
            workspace=ws,
            table_name=req.table_name,
            question=req.question,
            history=req.history,
        )
        return _agent_result_to_response(result)
    except Exception as e:
        return _error(
            "AGENT_FAILED",
            f"Orchestrator failed: {e}",
            500,
            detail=traceback.format_exc()[-800:],
        )


@app.post("/sql", response_model=SqlResponse)
def run_sql(req: SqlRequest):
    """
    Execute a read-only SQL statement against the workspace DuckDB.
    """
    ws = get_workspace()
    try:
        df, err = ws.execute_sql(req.sql)
        if err:
            return SqlResponse(success=False, sql=req.sql, error=err)
        records, cols, n = _df_to_records(df)
        return SqlResponse(
            success=True,
            sql=req.sql,
            result=records,
            result_columns=cols,
            result_row_count=n,
        )
    except Exception as e:
        return SqlResponse(success=False, sql=req.sql, error=str(e))


# ---------------------------------------------------------------------------
# Entrypoint helpers
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Factory used by tests and alternative runners."""
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
