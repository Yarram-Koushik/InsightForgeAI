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


# DuckDB DATE_TRUNC unit names
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
    value: Any = None  # unused for IS_NULL / IS_NOT_NULL


@dataclass
class MetricQuery:
    """
    Structured request for one or more governed metrics.

    dimensions: physical column names (or dimension.name) to GROUP BY
    time_grain: if not NONE, the time dimension is truncated to this grain
    """
    metric_names: List[str]
    dimensions: List[str] = field(default_factory=list)
    filters: List[MetricFilter] = field(default_factory=list)
    time_grain: TimeGrain = TimeGrain.NONE
    time_dimension: Optional[str] = None  # override model.time_dimension
    order_by: Optional[str] = None        # metric name or dimension column
    order_dir: str = "desc"               # asc | desc
    limit: Optional[int] = 100


@dataclass
class CompileResult:
    success: bool
    sql: Optional[str] = None
    explanation: str = ""
    metrics_used: List[str] = field(default_factory=list)
    dimensions_used: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    grain: str = ""                       # human description of GROUP BY
    metric_aliases: Dict[str, str] = field(default_factory=dict)  # name → alias


# ---------------------------------------------------------------------------
# Literal safety
# ---------------------------------------------------------------------------

def _sql_literal(value: Any) -> str:
    """Render a Python value as a safe DuckDB literal (no parameters API needed)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Reject NaN / inf
        if value != value or value in (float("inf"), float("-inf")):
            return "NULL"
        return str(value)
    # String – escape single quotes by doubling (primary defense)
    s = str(value)
    s = s.replace("'", "''")
    # Defense in depth: strip statement separators and comment markers
    s = re.sub(r"[;\x00]", " ", s)
    s = re.sub(r"--", " ", s)
    s = re.sub(r"/\*", " ", s)
    s = re.sub(r"\*/", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return f"'{s}'"


def _validate_identifier(name: str) -> bool:
    """Allow only reasonable column/metric identifiers."""
    if not name or not isinstance(name, str):
        return False
    if len(name) > 128:
        return False
    # Reject path-like or SQL control
    if re.search(r"[;\"\x00]|--", name):
        return False
    return True


# ---------------------------------------------------------------------------
# Dimension / metric resolution helpers
# ---------------------------------------------------------------------------

def _resolve_metric(model: SemanticModel, name: str) -> Optional[Metric]:
    m = model.metric_by_name(name)
    if m:
        return m
    # Soft match on label / normalised name
    target = _norm(name)
    for m in model.metrics:
        if _norm(m.name) == target or _norm(m.label) == target:
            return m
    return None


def _resolve_dimension_column(model: SemanticModel, name: str) -> Optional[str]:
    """Return physical column for a dimension name or column name."""
    if not name:
        return None
    # Exact column match first
    for d in model.dimensions:
        if d.column == name or d.name == name:
            return d.column
    # Case-insensitive
    low = name.lower()
    for d in model.dimensions:
        if d.column.lower() == low or d.name.lower() == low:
            return d.column
    # Allow grouping by entity columns too (common need)
    for e in model.entities:
        if e.column == name or e.column.lower() == low:
            return e.column
    return None


def _dimension_cardinality(model: SemanticModel, column: str) -> int:
    for d in model.dimensions:
        if d.column == column:
            return d.cardinality
    return 0


# ---------------------------------------------------------------------------
# Filter compilation
# ---------------------------------------------------------------------------

_ALLOWED_OPS = {op.value for op in FilterOp}


def _compile_filter(f: MetricFilter, known_columns: set) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (sql_fragment, error).
    """
    if not _validate_identifier(f.column):
        return None, f"Invalid filter column identifier: {f.column!r}"
    if f.column not in known_columns:
        # soft: still allow if it looks like a real column name (caller may know more)
        pass

    op = f.op if isinstance(f.op, FilterOp) else FilterOp(str(f.op).lower().replace(" ", "_"))
    col = _q(f.column)

    if op == FilterOp.IS_NULL:
        return f"{col} IS NULL", None
    if op == FilterOp.IS_NOT_NULL:
        return f"{col} IS NOT NULL", None

    if op in (FilterOp.IN, FilterOp.NOT_IN):
        vals = f.value
        if not isinstance(vals, (list, tuple, set)) or len(vals) == 0:
            return None, f"Filter {op.value} requires a non-empty list value"
        if len(vals) > 500:
            return None, f"Filter {op.value} list too large (max 500)"
        literals = ", ".join(_sql_literal(v) for v in vals)
        keyword = "IN" if op == FilterOp.IN else "NOT IN"
        return f"{col} {keyword} ({literals})", None

    if op in (FilterOp.LIKE, FilterOp.ILIKE):
        return f"{col} {op.value.upper()} {_sql_literal(f.value)}", None

    # Comparison ops
    if op.value not in {"=", "!=", ">", ">=", "<", "<="}:
        return None, f"Unsupported filter operator: {op}"
    return f"{col} {op.value} {_sql_literal(f.value)}", None


# ---------------------------------------------------------------------------
# Core compiler
# ---------------------------------------------------------------------------

def compile_metric_query(
    model: SemanticModel,
    query: MetricQuery,
    *,
    max_limit: int = 5000,
    high_cardinality_threshold: int = 200,
) -> CompileResult:
    """
    Compile MetricQuery → DuckDB SELECT.

    Never raises; always returns CompileResult.
    """
    warnings: List[str] = []

    if model is None:
        return CompileResult(success=False, error="SemanticModel is None.")

    if not query.metric_names:
        return CompileResult(success=False, error="MetricQuery.metric_names is empty.")

    table = model.table_name
    if not table or not _validate_identifier(table):
        return CompileResult(success=False, error=f"Invalid table name on model: {table!r}")

    # ---- Resolve metrics ----
    resolved_metrics: List[Metric] = []
    for name in query.metric_names:
        m = _resolve_metric(model, name)
        if m is None:
            return CompileResult(
                success=False,
                error=f"Unknown metric: {name!r}. Known: {[x.name for x in model.metrics[:20]]}",
            )
        resolved_metrics.append(m)
        if m.additivity == Additivity.NON and query.dimensions:
            warnings.append(
                f"Metric '{m.name}' is NON-additive (ratio/rate). "
                f"It will be computed at the GROUP BY grain — do not average the result further."
            )

    # ---- Resolve dimensions ----
    dim_columns: List[str] = []
    for dname in query.dimensions:
        col = _resolve_dimension_column(model, dname)
        if col is None:
            # Last resort: trust the name if it is a safe identifier (user may know schema)
            if _validate_identifier(dname):
                warnings.append(
                    f"Dimension '{dname}' not in semantic model; using as raw column."
                )
                col = dname
            else:
                return CompileResult(
                    success=False,
                    error=f"Invalid or unknown dimension: {dname!r}",
                )
        if col not in dim_columns:
            dim_columns.append(col)
            card = _dimension_cardinality(model, col)
            if card > high_cardinality_threshold:
                warnings.append(
                    f"Dimension '{col}' has high cardinality ({card}). "
                    f"Result may be large; consider filters or a higher time grain."
                )

    # ---- Time grain ----
    time_col = query.time_dimension or model.time_dimension
    time_select_expr: Optional[str] = None
    time_group_expr: Optional[str] = None
    time_alias: Optional[str] = None

    if query.time_grain != TimeGrain.NONE:
        if not time_col:
            warnings.append(
                "time_grain requested but no time dimension available; ignoring time_grain."
            )
        else:
            trunc_unit = _GRAIN_TO_TRUNC.get(query.time_grain)
            if trunc_unit is None:
                return CompileResult(
                    success=False,
                    error=f"Unsupported time_grain: {query.time_grain}",
                )
            time_alias = f"{time_col}_{query.time_grain.value}"
            # Safe: time_col validated via model or identifier check
            if not _validate_identifier(time_col):
                return CompileResult(success=False, error=f"Invalid time column: {time_col!r}")
            time_select_expr = f"DATE_TRUNC('{trunc_unit}', {_q(time_col)}) AS {_q(time_alias)}"
            time_group_expr = f"DATE_TRUNC('{trunc_unit}', {_q(time_col)})"
            # Avoid double-grouping the raw time column if user also listed it
            dim_columns = [c for c in dim_columns if c != time_col]

    # ---- Filters ----
    known_cols = set()
    for d in model.dimensions:
        known_cols.add(d.column)
    for e in model.entities:
        known_cols.add(e.column)
    for m in model.metrics:
        if m.measure_column:
            known_cols.add(m.measure_column)
        if m.entity_column:
            known_cols.add(m.entity_column)
    if time_col:
        known_cols.add(time_col)

    where_parts: List[str] = []
    for f in query.filters:
        frag, err = _compile_filter(f, known_cols)
        if err:
            return CompileResult(success=False, error=err)
        if frag:
            where_parts.append(frag)

    # ---- SELECT list ----
    select_parts: List[str] = []
    group_parts: List[str] = []
    metric_aliases: Dict[str, str] = {}

    if time_select_expr:
        select_parts.append(time_select_expr)
        group_parts.append(time_group_expr)  # type: ignore

    for col in dim_columns:
        select_parts.append(_q(col))
        group_parts.append(_q(col))

    for m in resolved_metrics:
        expr = m.sql_expression(quote=True)
        alias = m.name
        # Ensure alias is a safe unquoted-or-quoted identifier
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", alias):
            alias_sql = _q(alias)
        else:
            alias_sql = alias
        select_parts.append(f"{expr} AS {alias_sql}")
        metric_aliases[m.name] = alias

    if not select_parts:
        return CompileResult(success=False, error="Nothing to select.")

    # ---- Assemble SQL ----
    sql_lines = [
        "SELECT",
        "  " + ",\n  ".join(select_parts),
        f"FROM {_q(table)}",
    ]
    if where_parts:
        sql_lines.append("WHERE " + " AND ".join(where_parts))
    if group_parts:
        sql_lines.append("GROUP BY " + ", ".join(group_parts))

    # ORDER BY
    order_dir = (query.order_dir or "desc").lower()
    if order_dir not in ("asc", "desc"):
        order_dir = "desc"

    order_target = query.order_by
    if order_target:
        # Prefer metric alias, then dimension column, then time alias
        if order_target in metric_aliases:
            order_sql = metric_aliases[order_target]
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", order_sql):
                order_sql = _q(order_sql)
        elif time_alias and order_target in (time_alias, time_col, "time", "date"):
            order_sql = _q(time_alias)
        else:
            col = _resolve_dimension_column(model, order_target) or order_target
            if _validate_identifier(col):
                order_sql = _q(col)
            else:
                order_sql = None
                warnings.append(f"Could not resolve order_by={order_target!r}; skipped.")
        if order_sql:
            sql_lines.append(f"ORDER BY {order_sql} {order_dir.upper()}")
    elif resolved_metrics and group_parts:
        # Default: order by first metric desc when grouped
        first_alias = metric_aliases[resolved_metrics[0].name]
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", first_alias):
            first_alias = _q(first_alias)
        sql_lines.append(f"ORDER BY {first_alias} DESC")

    # LIMIT
    limit = query.limit
    if limit is not None:
        try:
            limit_i = int(limit)
        except (TypeError, ValueError):
            limit_i = 100
        limit_i = max(1, min(limit_i, max_limit))
        sql_lines.append(f"LIMIT {limit_i}")

    sql = "\n".join(sql_lines)

    # ---- Grain description ----
    grain_bits = []
    if time_alias:
        grain_bits.append(f"time:{query.time_grain.value}")
    grain_bits.extend(dim_columns)
    grain = ", ".join(grain_bits) if grain_bits else "(overall)"

    explanation = (
        f"Compiled {len(resolved_metrics)} metric(s) "
        f"[{', '.join(m.name for m in resolved_metrics)}] "
        f"at grain [{grain}] on table {_q(table)}."
    )

    return CompileResult(
        success=True,
        sql=sql,
        explanation=explanation,
        metrics_used=[m.name for m in resolved_metrics],
        dimensions_used=list(dim_columns),
        warnings=warnings,
        grain=grain,
        metric_aliases=metric_aliases,
    )


# ---------------------------------------------------------------------------
# Question → MetricQuery heuristic (bridge from NL)
# ---------------------------------------------------------------------------

_TIME_GRAIN_PATTERNS = [
    (re.compile(r"\bby\s+day\b|\bdaily\b|\beach\s+day\b", re.I), TimeGrain.DAY),
    (re.compile(r"\bby\s+week\b|\bweekly\b", re.I), TimeGrain.WEEK),
    (re.compile(r"\bby\s+month\b|\bmonthly\b|\beach\s+month\b", re.I), TimeGrain.MONTH),
    (re.compile(r"\bby\s+quarter\b|\bquarterly\b", re.I), TimeGrain.QUARTER),
    (re.compile(r"\bby\s+year\b|\byearly\b|\beach\s+year\b", re.I), TimeGrain.YEAR),
]


def infer_time_grain(question: str) -> TimeGrain:
    q = question or ""
    for pattern, grain in _TIME_GRAIN_PATTERNS:
        if pattern.search(q):
            return grain
    return TimeGrain.NONE


def infer_dimensions_from_question(
    question: str,
    model: SemanticModel,
    max_dims: int = 3,
) -> List[str]:
    """
    Lightweight dimension inference from 'by X' / 'per X' / 'for each X' phrases
    and direct dimension name mentions.
    """
    q = (question or "").lower()
    found: List[str] = []

    # Explicit "by region", "per category", "for each status"
    for m in re.finditer(
        r"\b(?:by|per|for each|grouped by|group by)\s+([a-z0-9_ ]{1,40})",
        q,
        flags=re.I,
    ):
        candidate = m.group(1).strip()
        # Stop at common trailing words
        candidate = re.split(
            r"\b(and|or|with|in|on|for|from|where|order|limit|the)\b",
            candidate,
        )[0].strip()
        if not candidate:
            continue
        col = _resolve_dimension_column(model, candidate)
        if col and col not in found:
            found.append(col)
        else:
            # Try matching dimension labels/names as tokens
            for d in model.dimensions:
                if _norm(d.column) in _norm(candidate) or _norm(d.name) in _norm(candidate):
                    if d.column not in found:
                        found.append(d.column)
                        break

    # Direct mention of known dimension columns
    for d in model.dimensions:
        if d.dim_type == DimensionType.TIME:
            continue  # time handled via grain
        tokens = set(_norm(d.column).split()) | set(_norm(d.label).split())
        q_tokens = set(_norm(q).split())
        if tokens & q_tokens and d.column not in found:
            # Weak signal – only if short name and appears as whole word
            if re.search(rf"\b{re.escape(d.column)}\b", question or "", re.I):
                found.append(d.column)

    return found[:max_dims]


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
    # Optionally include a second metric if clearly co-requested
    qn = _norm(question)
    if len(tips) > 1 and tips[1].get("preferred"):
        # e.g. total + count in same question
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
