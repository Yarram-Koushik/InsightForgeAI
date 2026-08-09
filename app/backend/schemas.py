"""
Pydantic schemas for the InsightForgeAI FastAPI boundary (Phase 3.4).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.3.4"
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
    result: Optional[List[Dict[str, Any]]] = None  # DataFrame as records
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
