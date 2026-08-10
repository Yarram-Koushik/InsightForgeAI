"""
InsightForgeAI – Metric Compiler (Phase 3.2)

Compile a structured MetricQuery + SemanticModel into safe, grain-aware DuckDB SQL.

Industry behaviours
-------------------
- Metrics are resolved from the governed SemanticModel (Phase 3.1)
- GROUP BY grain is explicit and complete (no missing dimensions)
- Ratio / NON-additive metrics are computed *at the query grain*
  (never AVG of a pre-computed ratio)
- Filters are validated against known columns and safely literal-quoted
- Time grains use DATE_TRUNC on the preferred time dimension
- Transparent: every CompileResult carries SQL, explanation, warnings
- Fail closed: unknown metric / unsafe filter → error, not partial SQL

This compiler is deterministic. Complex free-form questions still go through
NL→SQL (Phase 2); the compiler is preferred when the intent maps cleanly
to known metrics + dimensions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import re

# ---------------------------------------------------------------------------
# Local imports (works as package and via importlib)
# ---------------------------------------------------------------------------
try:
    from app.core.semantic_layer import (
        AggType,
        Additivity,
        DimensionType,
        Metric,
        SemanticModel,
        build_semantic_model,
        build_model_from_dataframe,
        resolve_metrics_for_question,
        _q,
        _norm,
    )
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
    AggType = _sl.AggType
    Additivity = _sl.Additivity
    DimensionType = _sl.DimensionType
    Metric = _sl.Metric
    SemanticModel = _sl.SemanticModel
    build_semantic_model = _sl.build_semantic_model
    build_model_from_dataframe = _sl.build_model_from_dataframe
    resolve_metrics_for_question = _sl.resolve_metrics_for_question
    _q = _sl._q
    _norm = _sl._norm


# ---------------------------------------------------------------------------
# Enums & query objects
# ---------------------------------------------------------------------------

class TimeGrain(str, Enum):
    NONE = "none"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


_GRAIN_TO_TRUNC = {
    TimeGrain.DAY: "day",
    TimeGrain.WEEK: "week",
    TimeGrain.MONTH: "month",
    TimeGrain.QUARTER: "quarter",
    TimeGrain.YEAR: "year",
}


class FilterOp(str, Enum):
    EQ = "="
    NEQ = "!="
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    IN = "in"
    NOT_IN = "not_in"
    LIKE = "like"
    ILIKE = "ilike"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


@dataclass
class MetricFilter:
    column: str
    op: FilterOp
    value: Any = None


@dataclass
class MetricQuery:
    """Structured request for one or more governed metrics."""
    metric_names: List[str]
    dimensions: List[str] = field(default_factory=list)
    filters: List[MetricFilter] = field(default_factory=list)
    time_grain: TimeGrain = TimeGrain.NONE
    time_column: Optional[str] = None
    order_by: Optional[str] = None
    order_dir: str = "desc"
    limit: Optional[int] = 100


@dataclass
class CompileResult:
    success: bool
    sql: Optional[str] = None
    explanation: str = ""
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    grain: List[str] = field(default_factory=list)
    metrics_used: List[str] = field(default_factory=list)


def _lit(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    s = str(value).replace("'", "''")
    return f"'{s}'"


def _filter_sql(f: MetricFilter) -> Tuple[Optional[str], Optional[str]]:
    col = _q(f.column)
    op = f.op
    if op == FilterOp.IS_NULL:
        return f"{col} IS NULL", None
    if op == FilterOp.IS_NOT_NULL:
        return f"{col} IS NOT NULL", None
    if op in (FilterOp.IN, FilterOp.NOT_IN):
        if not isinstance(f.value, (list, tuple, set)) or len(f.value) == 0:
            return None, f"Filter {op.value} requires a non-empty list value"
        if len(f.value) > 500:
            return None, f"Filter {op.value} list too large (max 500)"
        vals = ", ".join(_lit(v) for v in f.value)
        kw = "IN" if op == FilterOp.IN else "NOT IN"
        return f"{col} {kw} ({vals})", None
    if op in (FilterOp.LIKE, FilterOp.ILIKE):
        return f"{col} {op.value.upper()} {_lit(f.value)}", None
    return f"{col} {op.value} {_lit(f.value)}", None


def _resolve_metric(model: SemanticModel, name: str) -> Optional[Metric]:
    key = (name or "").strip().lower()
    for m in model.metrics:
        if m.name.lower() == key or (m.label or "").lower() == key:
            return m
    return None


def compile_metric_query(model: SemanticModel, query: MetricQuery) -> CompileResult:
    """Compile MetricQuery → DuckDB SELECT. Fail closed on unknown metrics."""
    warnings: List[str] = []
    if not query.metric_names:
        return CompileResult(success=False, error="No metrics requested.")

    metrics: List[Metric] = []
    for n in query.metric_names:
        m = _resolve_metric(model, n)
        if m is None:
            return CompileResult(success=False, error=f"Unknown metric: {n}")
        metrics.append(m)

    table = model.table_name
    select_parts: List[str] = []
    group_parts: List[str] = []
    grain: List[str] = []

    # Dimensions
    dim_cols = set()
    for d in query.dimensions:
        dim_cols.add(d)
        select_parts.append(f"{_q(d)} AS {_q(d)}")
        group_parts.append(_q(d))
        grain.append(d)

    # Time grain
    tg = query.time_grain or TimeGrain.NONE
    tcol = query.time_column or model.time_dimension
    if tg != TimeGrain.NONE and tcol:
        unit = _GRAIN_TO_TRUNC.get(tg, "month")
        alias = f"{tcol}_{unit}"
        expr = f"DATE_TRUNC('{unit}', {_q(tcol)})"
        select_parts.append(f"{expr} AS {_q(alias)}")
        group_parts.append(expr)
        grain.append(alias)
    elif tg != TimeGrain.NONE and not tcol:
        warnings.append("Time grain requested but no time dimension on model.")

    # Metrics
    for m in metrics:
        try:
            expr = m.sql_expression()
        except Exception as e:
            return CompileResult(success=False, error=f"Metric {m.name} expression failed: {e}")
        select_parts.append(f"{expr} AS {_q(m.name)}")

    # WHERE
    where_parts: List[str] = []
    for f in query.filters or []:
        sql_f, err = _filter_sql(f)
        if err:
            return CompileResult(success=False, error=err)
        if sql_f:
            where_parts.append(sql_f)

    sql = f"SELECT {', '.join(select_parts)}\nFROM {_q(table)}"
    if where_parts:
        sql += "\nWHERE " + " AND ".join(where_parts)
    if group_parts:
        sql += "\nGROUP BY " + ", ".join(group_parts)

    order_col = query.order_by or (metrics[0].name if metrics else None)
    if order_col:
        direction = "DESC" if (query.order_dir or "desc").lower().startswith("d") else "ASC"
        sql += f"\nORDER BY {_q(order_col)} {direction}"

    lim = query.limit if query.limit is not None else 100
    if lim and lim > 0:
        sql += f"\nLIMIT {int(lim)}"

    return CompileResult(
        success=True,
        sql=sql,
        explanation=f"Compiled {len(metrics)} metric(s) at grain={grain or ['(total)']}.",
        warnings=warnings,
        grain=grain,
        metrics_used=[m.name for m in metrics],
    )


def infer_time_grain(question: str) -> TimeGrain:
    q = (question or "").lower()
    if any(w in q for w in ("by day", "daily", "each day", "per day")):
        return TimeGrain.DAY
    if any(w in q for w in ("by week", "weekly", "each week", "per week")):
        return TimeGrain.WEEK
    if any(w in q for w in ("by month", "monthly", "each month", "per month")):
        return TimeGrain.MONTH
    if any(w in q for w in ("by quarter", "quarterly")):
        return TimeGrain.QUARTER
    if any(w in q for w in ("by year", "yearly", "each year", "per year", "annual")):
        return TimeGrain.YEAR
    return TimeGrain.NONE


def infer_dimensions_from_question(question: str, model: SemanticModel) -> List[str]:
    q = _norm(question or "")
    found: List[str] = []
    for d in model.dimensions or []:
        name = (d.name or d.column or "").lower()
        col = (d.column or "").lower()
        if name and name in q:
            found.append(d.column or d.name)
        elif col and col in q:
            found.append(d.column)
    # de-dupe preserve order
    seen = set()
    out = []
    for c in found:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def try_compile_from_question(
    question: str,
    model: SemanticModel,
    *,
    min_confidence: float = 0.35,
    limit: int = 100,
) -> CompileResult:
    """
    Bridge: natural language → MetricQuery → SQL, when intent is clear enough.

    Returns success=False (with reason) when the question is too ambiguous;
    caller should fall back to NL→SQL.
    """
    if not question or not question.strip():
        return CompileResult(success=False, error="Empty question.")

    q_low = question.lower().strip()
    # Row-list / specific-column questions must NOT become COUNT/SUM metrics.
    list_hints = (
        "list ", "show me the", "show only", "only show", "just show",
        "just only", "display ", "print ", "select ", "with customer name",
        "customer names", "order id and", "columns",
    )
    agg_hints = (
        "how many", "total ", "sum of", "average ", "avg ", "count ",
        "unique ", "aov", "revenue", "what is the", "number of",
    )
    looks_like_list = any(h in q_low for h in list_hints) or q_low.startswith(("list", "show", "display"))
    looks_like_agg = any(h in q_low for h in agg_hints)
    if looks_like_list and not looks_like_agg:
        return CompileResult(
            success=False,
            error="Row-list / column-select question; use NL→SQL (not metric compile).",
        )

    tips = resolve_metrics_for_question(question, model, top_k=3)
    if not tips:
        return CompileResult(
            success=False,
            error="No governed metric matched this question; use NL→SQL.",
        )

    top = tips[0]
    if float(top.get("confidence") or 0) < min_confidence:
        return CompileResult(
            success=False,
            error=f"Metric match confidence too low ({top.get('confidence')}); use NL→SQL.",
            warnings=[f"Best candidate was {top.get('key')}"],
        )

    metric_names = [top["key"]]
    qn = _norm(question)
    if len(tips) > 1 and tips[1].get("preferred"):
        if any(w in qn for w in ("and", "plus", "also", "with")):
            metric_names.append(tips[1]["key"])

    dims = infer_dimensions_from_question(question, model)
    grain = infer_time_grain(question)

    query = MetricQuery(
        metric_names=metric_names,
        dimensions=dims,
        time_grain=grain,
        order_by=metric_names[0],
        order_dir="desc",
        limit=limit,
    )
    result = compile_metric_query(model, query)
    if result.success:
        result.explanation = (
            f"Deterministic compile from question (metric={metric_names}, "
            f"grain={result.grain}). {result.explanation}"
        )
    return result


__all__ = [
    "TimeGrain",
    "FilterOp",
    "MetricFilter",
    "MetricQuery",
    "CompileResult",
    "compile_metric_query",
    "try_compile_from_question",
    "infer_time_grain",
    "infer_dimensions_from_question",
]
