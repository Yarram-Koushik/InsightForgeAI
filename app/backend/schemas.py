"""
Pydantic schemas for the InsightForgeAI FastAPI boundary (Phase 3.4 + 4.5).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    table_name: str = Field(..., description="Registered dataset / table name")
    question: str = Field(..., min_length=1, description="Natural language question")
    history: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Optional prior turns for follow-up resolution",
    )


class SqlRequest(BaseModel):
    table_name: Optional[str] = Field(
        default=None,
        description="Optional primary table (for context / guards)",
    )
    sql: str = Field(..., min_length=1, description="DuckDB SQL (read-only)")


class DatasetInfo(BaseModel):
    name: str
    source_filename: Optional[str] = None
    rows: Optional[int] = None
    columns: Optional[int] = None
    id: Optional[str] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.4.5"
    service: str = "InsightForgeAI"


class ErrorDetail(BaseModel):
    code: str
    message: str
    detail: Optional[str] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail


class AskResponse(BaseModel):
    success: bool
    question: str
    intent: str
    intent_reason: Optional[str] = None
    message: Optional[str] = None
    sql: Optional[str] = None
    insight: Optional[str] = None
    clarify_questions: List[str] = Field(default_factory=list)
    result: Optional[List[Dict[str, Any]]] = None
    result_columns: Optional[List[str]] = None
    result_row_count: Optional[int] = None
    forecast: Optional[List[Dict[str, Any]]] = None
    forecast_method: Optional[str] = None
    forecast_horizon: Optional[int] = None
    trend_summary: Optional[str] = None
    anomalies: List[Any] = Field(default_factory=list)
    chart_type: Optional[str] = None
    chart_reason: Optional[str] = None
    steps: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class SqlResponse(BaseModel):
    success: bool
    sql: str
    result: Optional[List[Dict[str, Any]]] = None
    result_columns: Optional[List[str]] = None
    result_row_count: Optional[int] = None
    error: Optional[str] = None


class DatasetsResponse(BaseModel):
    datasets: List[DatasetInfo]
    count: int


class SchemaResponse(BaseModel):
    table_name: str
    columns: List[Dict[str, Any]]


class UploadResponse(BaseModel):
    success: bool
    table_name: str
    rows: int
    columns: int
    message: str
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 4.5 – Schedules & workspaces
# ---------------------------------------------------------------------------

class ScheduleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    workspace_id: str = Field(default="default")
    kind: str = Field(default="question", description="question | dashboard")
    question: str = Field(default="")
    table_name: str = Field(default="")
    interval_minutes: Optional[int] = Field(default=None, ge=1, le=10080)
    daily_at: Optional[str] = Field(default=None, description="HH:MM UTC")
    channel: str = Field(default="log", description="log | slack | email")
    webhook_url: Optional[str] = None
    email_to: Optional[str] = None
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    name: Optional[str] = None
    question: Optional[str] = None
    table_name: Optional[str] = None
    interval_minutes: Optional[int] = Field(default=None, ge=1, le=10080)
    daily_at: Optional[str] = None
    channel: Optional[str] = None
    webhook_url: Optional[str] = None
    email_to: Optional[str] = None
    enabled: Optional[bool] = None


class ScheduleResponse(BaseModel):
    id: str
    name: str
    workspace_id: str
    kind: str
    question: str = ""
    table_name: str = ""
    interval_minutes: Optional[int] = None
    daily_at: Optional[str] = None
    channel: str = "log"
    enabled: bool = True
    created_by: str = "anonymous"
    created_at: Optional[str] = None
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    last_status: str = "never"
    last_error: Optional[str] = None
    run_count: int = 0


class SchedulesListResponse(BaseModel):
    schedules: List[ScheduleResponse]
    count: int


class InsightCreateRequest(BaseModel):
    name: str
    question: str
    table_name: str
    workspace_id: str = "default"
    description: str = ""
    tags: List[str] = Field(default_factory=list)


class WorkspaceInfoResponse(BaseModel):
    workspace_id: str
    display_name: Optional[str] = None
    owner_id: Optional[str] = None
    org_id: Optional[str] = None
    dataset_count: int = 0
    chat_turns: int = 0
    insight_count: int = 0
    schedule_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
