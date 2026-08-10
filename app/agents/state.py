"""
Shared state and result types for the multi-agent pipeline.
All dataclasses are plain so they work under importlib loading.

Phase 4.2: citations + grounding_line for trust / multi-turn evidence.
Phase 4.3: eda_pack, extra_charts, root_cause, whatif, rfm artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Any, Dict
import pandas as pd


class Intent(str, Enum):
    DATA_QUERY = "data_query"       # needs SQL, return table
    INSIGHT = "insight"             # needs SQL + business explanation
    CLARIFY = "clarify"             # question too vague / missing context
    META = "meta"                   # about the system itself
    UNSUPPORTED = "unsupported"     # cannot be answered from current data
    FORECAST = "forecast"           # time-series forecast / trend / anomaly


@dataclass
class AgentState:
    """Mutable state passed through the agent pipeline."""
    question: str
    table_name: str
    workspace: Any = None

    # Optional multi-turn context (injected by UI for context_memory)
    chat_history: Optional[List[Dict[str, Any]]] = None
    history: Optional[List[Dict[str, Any]]] = None
    raw_question: Optional[str] = None

    intent: Optional[Intent] = None
    intent_reason: Optional[str] = None

    # SQL stage
    sql: Optional[str] = None
    sql_success: bool = False
    result_df: Optional[pd.DataFrame] = None
    sql_error: Optional[str] = None
    sql_attempts: int = 0
    provider: Optional[str] = None
    model: Optional[str] = None

    # Insight stage
    insight_text: Optional[str] = None

    # Visualization stage (Phase 2.4)
    chart_fig: Any = None
    chart_type: Optional[str] = None
    chart_reason: Optional[str] = None

    # Forecast stage (Phase 2.5)
    forecast_success: bool = False
    forecast_df: Optional[pd.DataFrame] = None
    forecast_fig: Any = None
    forecast_method: Optional[str] = None
    forecast_horizon: Optional[int] = None
    forecast_error: Optional[str] = None
    trend_summary: Optional[str] = None
    anomalies: List[Any] = field(default_factory=list)

    # Clarify stage
    clarify_questions: List[str] = field(default_factory=list)

    # Phase 4.2 – evidence / grounding
    citations: List[Dict[str, Any]] = field(default_factory=list)
    grounding_line: Optional[str] = None

    # Phase 4.3 – specialized analytics artifacts
    extra_charts: List[Dict[str, Any]] = field(default_factory=list)  # [{title, fig, reason}, ...]
    eda_pack: Any = None
    root_cause: Any = None
    whatif: Any = None
    rfm: Any = None

    # Pipeline bookkeeping
    steps: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class AgentResult:
    """Final structured response returned to the UI."""
    success: bool
    question: str
    intent: str
    intent_reason: Optional[str] = None

    # Evidence
    sql: Optional[str] = None
    result_df: Optional[pd.DataFrame] = None
    insight: Optional[str] = None
    clarify_questions: List[str] = field(default_factory=list)

    # Chart (Phase 2.4)
    chart_fig: Any = None
    chart_type: Optional[str] = None
    chart_reason: Optional[str] = None

    # Forecast (Phase 2.5)
    forecast_df: Optional[pd.DataFrame] = None
    forecast_method: Optional[str] = None
    forecast_horizon: Optional[int] = None
    trend_summary: Optional[str] = None
    anomalies: List[Any] = field(default_factory=list)

    # Phase 4.2 – citations & grounding (shown in UI on every answer)
    citations: List[Dict[str, Any]] = field(default_factory=list)
    grounding_line: Optional[str] = None

    # Phase 4.3 – analytics extras
    extra_charts: List[Dict[str, Any]] = field(default_factory=list)
    eda_pack: Any = None

    # Meta
    steps: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "intent": self.intent,
            "sql": self.sql,
            "insight": self.insight,
            "error": self.error,
            "message": self.message,
            "steps": self.steps,
            "warnings": self.warnings,
            "grounding_line": self.grounding_line,
            "citations": self.citations,
            "extra_chart_titles": [c.get("title") for c in (self.extra_charts or [])],
        }
