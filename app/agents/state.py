"""
Shared state and result types for the multi-agent pipeline.
All dataclasses are plain so they work under importlib loading.
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


@dataclass
class AgentState:
    """Mutable state passed through the agent pipeline."""
    question: str
    table_name: str
    workspace: Any = None

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

    # Clarify stage
    clarify_questions: List[str] = field(default_factory=list)

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

    # Meta
    steps: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    message: Optional[str] = None   # human-readable summary for UI

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
        }
