"""
InsightForgeAI – Time Intelligence (Phase 3.4)

Period-over-period, year-over-year, YTD, and rolling-window comparisons
on top of governed metrics (3.1) and the metric compiler (3.2).

Industry behaviours
-------------------
- Explicit current vs comparison windows (never silent off-by-one)
- DuckDB-native DATE_TRUNC / INTERVAL arithmetic
- Safe division for growth rates (NULLIF)
- Works with a metric SQL expression or a simple column aggregate
- NL helpers to detect “vs last month”, “YTD”, “rolling 7 days”, etc.
- Fail closed when no time column or insufficient history

Single-period queries remain handled by metric_compiler; this module
adds *comparison* queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import re
from datetime import datetime, timedelta

import pandas as pd


# ---------------------------------------------------------------------------
# Optional semantic / compiler helpers
# ---------------------------------------------------------------------------
try:
    from app.core.semantic_layer import SemanticModel, build_semantic_model, _q, _norm
except Exception:
    import importlib.util
    import sys
    from pathlib import Path

    _core = Path(__file__).resolve().parent
    if str(_core.parent.parent) not in sys.path:
        sys.path.insert(0, str(_core.parent.parent))
    _spec = importlib.util.spec_from_file_location(
        "semantic_layer", _core / "semantic_layer.py"
    )
    _sl = importlib.util.module_from_spec(_spec)
    sys.modules["semantic_layer"] = _sl
    _spec.loader.exec_module(_sl)
    SemanticModel = _sl.SemanticModel
    build_semantic_model = _sl.build_semantic_model
    _q = _sl._q
    _norm = _sl._norm


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------

class ComparisonKind(str, Enum):
    PERIOD_OVER_PERIOD = "period_over_period"  # vs previous equal-length window
    YEAR_OVER_YEAR = "year_over_year"
    MONTH_OVER_MONTH = "month_over_month"
    WEEK_OVER_WEEK = "week_over_week"
    YTD = "ytd"
    ROLLING = "rolling"


class TimeGrain(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


_GRAIN_INTERVAL = {
    TimeGrain.DAY: "1 DAY",
    TimeGrain.WEEK: "7 DAYS",
    TimeGrain.MONTH: "1 MONTH",
    TimeGrain.QUARTER: "3 MONTHS",
    TimeGrain.YEAR: "1 YEAR",
}

_GRAIN_TRUNC = {
    TimeGrain.DAY: "day",
    TimeGrain.WEEK: "week",
    TimeGrain.MONTH: "month",
    TimeGrain.QUARTER: "quarter",
    TimeGrain.YEAR: "year",
}


@dataclass
class TimeIntelRequest:
    """Structured time-intelligence request."""
    table: str
    time_column: str
    metric_expr: str                      # e.g. SUM("amount") or full expression
    metric_alias: str = "metric_value"
    kind: ComparisonKind = ComparisonKind.PERIOD_OVER_PERIOD
    grain: TimeGrain = TimeGrain.MONTH
    rolling_periods: int = 7              # for ROLLING
    # Optional anchors (ISO date strings). If None, use MAX(time_column) as "as of".
    as_of: Optional[str] = None
    filters_sql: Optional[str] = None     # extra AND ... predicates (already safe)


@dataclass
class TimeIntelResult:
    success: bool
    sql: Optional[str] = None
    explanation: str = ""
    kind: Optional[str] = None
    grain: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    labels: Dict[str, str] = field(default_factory=dict)  # current / previous labels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_ident(name: str) -> bool:
    if not name or not isinstance(name, str) or len(name) > 128:
        return False
    if re.search(r'[;"\x00]|--', name):
        return False
    return True


def detect_time_column(model: Optional[SemanticModel], columns: Optional[List[str]] = None) -> Optional[str]:
    if model and model.time_dimension:
        return model.time_dimension
    if model:
        for d in model.dimensions:
            if getattr(d, "dim_type", None) and str(d.dim_type.value) == "time":
                return d.column
    if columns:
        for c in columns:
            n = _norm(c)
            if any(h in n for h in ("date", "time", "timestamp", "created", "order date")):
                return c
    return None


def resolve_metric_expr(
    model: Optional[SemanticModel],
    metric_name: Optional[str] = None,
    fallback_expr: str = "COUNT(*)",
) -> Tuple[str, str]:
    """Return (sql_expression, alias)."""
    if model and metric_name:
        m = model.metric_by_name(metric_name)
        if m is None:
            # soft label match
            target = _norm(metric_name)
            for cand in model.metrics:
                if _norm(cand.name) == target or _norm(cand.label) == target:
                    m = cand
                    break
        if m is not None:
            return m.sql_expression(quote=True), m.name
    return fallback_expr, "metric_value"


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------

def compile_time_intel(req: TimeIntelRequest) -> TimeIntelResult:
    """
    Compile a comparison query.

    Shape of result SQL (typical PoP / YoY):
      one row with current_value, previous_value, delta, growth_pct
    YTD / rolling may return a short series or a single current aggregate
    with a comparison baseline where applicable.
    """
    warnings: List[str] = []

    if not req.table or not _validate_ident(req.table):
        return TimeIntelResult(success=False, error=f"Invalid table: {req.table!r}")
    if not req.time_column or not _validate_ident(req.time_column):
        return TimeIntelResult(success=False, error=f"Invalid time column: {req.time_column!r}")
    if not req.metric_expr or not str(req.metric_expr).strip():
        return TimeIntelResult(success=False, error="metric_expr is empty.")

    t = _q(req.table)
    tc = _q(req.time_column)
    alias = req.metric_alias if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", req.metric_alias) else "metric_value"
    metric = req.metric_expr
    filt = f" AND ({req.filters_sql})" if req.filters_sql else ""

    as_of_expr = f"CAST('{req.as_of}' AS DATE)" if req.as_of else f"(SELECT MAX({tc}) FROM {t})"

    kind = req.kind
    grain = req.grain
    trunc = _GRAIN_TRUNC[grain]
    interval = _GRAIN_INTERVAL[grain]

    if kind == ComparisonKind.ROLLING:
        n = max(1, min(int(req.rolling_periods or 7), 365))
        sql = f"""
WITH bounds AS (
  SELECT {as_of_expr} AS as_of
),
windowed AS (
  SELECT
    DATE_TRUNC('{trunc}', {tc}) AS period,
    {metric} AS {alias}
  FROM {t}, bounds
  WHERE {tc} > bounds.as_of - INTERVAL '{n} {trunc.upper() if trunc != "day" else "DAY"}'
    AND {tc} <= bounds.as_of
    {filt}
  GROUP BY 1
)
SELECT period, {alias}
FROM windowed
ORDER BY period
""".strip()
        # Fix interval wording for DuckDB – use days for rolling when grain is day
        if grain == TimeGrain.DAY:
            sql = f"""
WITH bounds AS (
  SELECT {as_of_expr} AS as_of
)
SELECT
  DATE_TRUNC('day', {tc}) AS period,
  {metric} AS {alias}
FROM {t}, bounds
WHERE {tc} > bounds.as_of - INTERVAL '{n} DAYS'
  AND {tc} <= bounds.as_of
  {filt}
GROUP BY 1
ORDER BY 1
""".strip()
        return TimeIntelResult(
            success=True,
            sql=sql,
            explanation=f"Rolling {n} {grain.value}(s) of {alias} ending at as-of date.",
            kind=kind.value,
            grain=grain.value,
            warnings=warnings,
            labels={"series": f"rolling_{n}_{grain.value}"},
        )

    if kind == ComparisonKind.YTD:
        sql = f"""
WITH bounds AS (
  SELECT {as_of_expr} AS as_of
),
curr AS (
  SELECT {metric} AS current_value
  FROM {t}, bounds
  WHERE {tc} >= DATE_TRUNC('year', bounds.as_of)
    AND {tc} <= bounds.as_of
    {filt}
),
prev AS (
  SELECT {metric} AS previous_value
  FROM {t}, bounds
  WHERE {tc} >= DATE_TRUNC('year', bounds.as_of) - INTERVAL '1 YEAR'
    AND {tc} < DATE_TRUNC('year', bounds.as_of)
      + (bounds.as_of - DATE_TRUNC('year', bounds.as_of))
    {filt}
)
SELECT
  curr.current_value,
  prev.previous_value,
  curr.current_value - prev.previous_value AS delta,
  (curr.current_value - prev.previous_value)
    / NULLIF(prev.previous_value, 0) AS growth_pct
FROM curr, prev
""".strip()
        return TimeIntelResult(
            success=True,
            sql=sql,
            explanation="Year-to-date vs same span of previous year.",
            kind=kind.value,
            grain="year",
            warnings=warnings,
            labels={"current": "YTD", "previous": "Prior YTD span"},
        )

    # Shared PoP / YoY / MoM / WoW pattern: current grain bucket vs previous bucket
    if kind == ComparisonKind.YEAR_OVER_YEAR:
        shift = "1 YEAR"
        label_prev = "same period last year"
    elif kind == ComparisonKind.MONTH_OVER_MONTH:
        shift = "1 MONTH"
        label_prev = "previous month"
        trunc = "month"
    elif kind == ComparisonKind.WEEK_OVER_WEEK:
        shift = "7 DAYS"
        label_prev = "previous week"
        trunc = "week"
    else:
        # generic period-over-period by grain
        shift = interval
        label_prev = f"previous {grain.value}"

    sql = f"""
WITH bounds AS (
  SELECT
    {as_of_expr} AS as_of,
    DATE_TRUNC('{trunc}', {as_of_expr}) AS curr_start
),
curr AS (
  SELECT {metric} AS current_value
  FROM {t}, bounds
  WHERE {tc} >= bounds.curr_start
    AND {tc} < bounds.curr_start + INTERVAL '{shift}'
    {filt}
),
prev AS (
  SELECT {metric} AS previous_value
  FROM {t}, bounds
  WHERE {tc} >= bounds.curr_start - INTERVAL '{shift}'
    AND {tc} < bounds.curr_start
    {filt}
)
SELECT
  curr.current_value,
  prev.previous_value,
  curr.current_value - prev.previous_value AS delta,
  (curr.current_value - prev.previous_value)
    / NULLIF(ABS(prev.previous_value), 0) AS growth_pct
FROM curr, prev
""".strip()

    # For MONTH interval DuckDB prefers '1 MONTH' which we already set
    return TimeIntelResult(
        success=True,
        sql=sql,
        explanation=f"{kind.value} comparison at {trunc} grain ({label_prev}).",
        kind=kind.value,
        grain=trunc,
        warnings=warnings,
        labels={"current": f"current {trunc}", "previous": label_prev},
    )


# ---------------------------------------------------------------------------
# NL intent parsing
# ---------------------------------------------------------------------------

_KIND_PATTERNS: List[Tuple[re.Pattern, ComparisonKind, Optional[TimeGrain]]] = [
    (re.compile(r"\bytd\b|year[\s-]*to[\s-]*date|year to date", re.I), ComparisonKind.YTD, TimeGrain.YEAR),
    (re.compile(r"\byoy\b|year[\s-]*over[\s-]*year|vs\.?\s*last\s*year|compared to last year", re.I), ComparisonKind.YEAR_OVER_YEAR, TimeGrain.YEAR),
    (re.compile(r"\bmom\b|month[\s-]*over[\s-]*month|vs\.?\s*last\s*month|compared to last month", re.I), ComparisonKind.MONTH_OVER_MONTH, TimeGrain.MONTH),
    (re.compile(r"\bwow\b|week[\s-]*over[\s-]*week|vs\.?\s*last\s*week", re.I), ComparisonKind.WEEK_OVER_WEEK, TimeGrain.WEEK),
    (re.compile(r"\brolling\s+(\d+)\s*(day|days|week|weeks|month|months)\b", re.I), ComparisonKind.ROLLING, None),
    (re.compile(r"\blast\s+(\d+)\s*(day|days|week|weeks|month|months)\b", re.I), ComparisonKind.ROLLING, None),
    (re.compile(r"\bperiod[\s-]*over[\s-]*period\b|\bpop\b|vs\.?\s*previous\s*period", re.I), ComparisonKind.PERIOD_OVER_PERIOD, None),
]


def parse_time_intel_intent(question: str) -> Optional[Dict[str, Any]]:
    """
    Detect time-intelligence intent from a natural language question.
    Returns dict with kind, grain, rolling_periods — or None if not a TI question.
    """
    q = question or ""
    if not q.strip():
        return None

    for pattern, kind, grain in _KIND_PATTERNS:
        m = pattern.search(q)
        if not m:
            continue
        out: Dict[str, Any] = {"kind": kind, "grain": grain or TimeGrain.MONTH, "rolling_periods": 7}
        if kind == ComparisonKind.ROLLING and m.lastindex and m.lastindex >= 2:
            try:
                out["rolling_periods"] = int(m.group(1))
            except Exception:
                out["rolling_periods"] = 7
            unit = m.group(2).lower()
            if unit.startswith("day"):
                out["grain"] = TimeGrain.DAY
            elif unit.startswith("week"):
                out["grain"] = TimeGrain.WEEK
            elif unit.startswith("month"):
                out["grain"] = TimeGrain.MONTH
        return out

    # softer: "growth", "change vs", "compared with previous"
    if re.search(r"\b(growth|change|delta)\b.*\b(month|week|year|period)\b", q, re.I):
        return {"kind": ComparisonKind.PERIOD_OVER_PERIOD, "grain": TimeGrain.MONTH, "rolling_periods": 7}
    return None


def try_compile_time_intel_from_question(
    question: str,
    model: SemanticModel,
    table_name: str,
    *,
    metric_name: Optional[str] = None,
) -> TimeIntelResult:
    """
    Bridge: NL → TimeIntelRequest → SQL when the question is clearly comparative.
    """
    intent = parse_time_intel_intent(question)
    if intent is None:
        return TimeIntelResult(
            success=False,
            error="No time-intelligence intent detected; use standard metric compile or NL→SQL.",
        )

    time_col = detect_time_column(model)
    if not time_col:
        return TimeIntelResult(
            success=False,
            error="No time dimension available for time intelligence.",
        )

    # Prefer metric resolved from question if not provided
    if not metric_name:
        try:
            from app.core.semantic_layer import resolve_metrics_for_question
            tips = resolve_metrics_for_question(question, model, top_k=1)
            if tips:
                metric_name = tips[0]["key"]
        except Exception:
            pass

    expr, alias = resolve_metric_expr(model, metric_name, fallback_expr="COUNT(*)")

    req = TimeIntelRequest(
        table=table_name,
        time_column=time_col,
        metric_expr=expr,
        metric_alias=alias,
        kind=intent["kind"],
        grain=intent["grain"],
        rolling_periods=int(intent.get("rolling_periods") or 7),
    )
    result = compile_time_intel(req)
    if result.success:
        result.explanation = (
            f"Time intelligence from question ({intent['kind'].value}). {result.explanation}"
        )
    return result


def time_intel_prompt_block(question: str, model: Optional[SemanticModel] = None) -> str:
    """Hints for the LLM when TI patterns appear."""
    intent = parse_time_intel_intent(question)
    lines = [
        "TIME INTELLIGENCE RULES (Phase 3.4):",
        "- For YoY / MoM / WoW / PoP: compute current period and previous period separately, then delta and growth_pct = delta / NULLIF(previous, 0).",
        "- For YTD: from DATE_TRUNC('year', as_of) through as_of; compare to the same span last year.",
        "- For rolling N days/weeks: filter time column to the trailing window and GROUP BY period.",
        "- Never average a growth rate across groups; compute ratio after aggregation.",
    ]
    if intent:
        lines.append(
            f"- Detected intent: {intent['kind'].value}, grain={intent['grain'].value}, "
            f"rolling_periods={intent.get('rolling_periods')}"
        )
    if model and model.time_dimension:
        lines.append(f"- Preferred time column: {_q(model.time_dimension)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ComparisonKind",
    "TimeGrain",
    "TimeIntelRequest",
    "TimeIntelResult",
    "compile_time_intel",
    "parse_time_intel_intent",
    "try_compile_time_intel_from_question",
    "time_intel_prompt_block",
    "detect_time_column",
    "resolve_metric_expr",
]
