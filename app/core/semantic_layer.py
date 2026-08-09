"""
InsightForgeAI – Semantic Metric Layer (Phase 3.1)

Industry-grade semantic model for a single cleaned table:
  - Entities (primary-key candidates)
  - Dimensions (categorical / time / boolean)
  - Metrics (simple + ratio + expression) with explicit additivity

The layer is used to:
  1. Auto-discover a SemanticModel from Phase-1 schema detection
  2. Resolve which metrics answer a natural-language question
  3. Enrich the NL→SQL prompt so the LLM cannot invent wrong aggregations
  4. Provide safe, null-aware SQL fragments for known metrics

Design notes
------------
- Pure Python + dataclasses. No external metric server.
- Ratios are NON-additive; never emit AVG(ratio).
- Identifiers are never proposed as SUM/AVG measures.
- Backward compatible with Phase 2.7 metrics.py (thin façade still exists).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple
import re
import sys
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AggType(str, Enum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    RATIO = "ratio"
    EXPRESSION = "expression"


class DimensionType(str, Enum):
    CATEGORICAL = "categorical"
    TIME = "time"
    BOOLEAN = "boolean"
    IDENTIFIER = "identifier"
    NUMERIC = "numeric"  # low-cardinality numeric used as dim


class Additivity(str, Enum):
    FULL = "full"          # SUM, COUNT – safe to re-aggregate
    SEMI = "semi"          # AVG, MIN, MAX – careful across grains
    NON = "non"            # ratios, rates – never average or re-sum


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    name: str
    column: str
    role: str = "primary"          # primary | foreign | natural
    confidence: float = 0.8
    reason: str = ""


@dataclass
class Dimension:
    name: str
    column: str
    dim_type: DimensionType
    label: str
    cardinality: int = 0
    confidence: float = 0.7
    is_time_grain_candidate: bool = False
    reason: str = ""


@dataclass
class Metric:
    """
    A governed business metric.

    For simple metrics: measure_column + agg.
    For ratios: numerator_metric / denominator_metric (or columns).
    For expressions: expr is a DuckDB-compatible fragment with {col} placeholders.
    """
    name: str
    label: str
    description: str
    agg: AggType
    additivity: Additivity = Additivity.FULL

    # Simple / distinct
    measure_column: Optional[str] = None
    entity_column: Optional[str] = None          # for COUNT DISTINCT / ratio denom

    # Derived / ratio
    numerator: Optional[str] = None              # metric name or column
    denominator: Optional[str] = None
    expr: Optional[str] = None                   # final SQL expression template

    filters: List[str] = field(default_factory=list)
    preferred: bool = False
    confidence: float = 0.75
    tags: List[str] = field(default_factory=list)
    reason: str = ""

    def sql_expression(self, quote: bool = True) -> str:
        """Return a safe DuckDB expression for this metric."""
        q = _q if quote else (lambda x: x)

        if self.expr:
            return self.expr

        if self.agg == AggType.RATIO:
            num = self.numerator or self.measure_column
            den = self.denominator or self.entity_column
            if not num or not den:
                return "NULL"
            # Prefer COUNT(DISTINCT) for entity denominators when it looks like an id
            den_expr = f"COUNT(DISTINCT {q(den)})" if self.entity_column else f"COUNT({q(den)})"
            return f"SUM({q(num)}) / NULLIF({den_expr}, 0)"

        if self.agg == AggType.COUNT:
            return "COUNT(*)"

        if self.agg == AggType.COUNT_DISTINCT:
            col = self.entity_column or self.measure_column
            if not col:
                return "COUNT(*)"
            return f"COUNT(DISTINCT {q(col)})"

        col = self.measure_column
        if not col:
            return "NULL"

        mapping = {
            AggType.SUM: f"SUM({q(col)})",
            AggType.AVG: f"AVG({q(col)})",
            AggType.MIN: f"MIN({q(col)})",
            AggType.MAX: f"MAX({q(col)})",
        }
        return mapping.get(self.agg, f"SUM({q(col)})")


@dataclass
class SemanticModel:
    table_name: str
    entities: List[Entity] = field(default_factory=list)
    dimensions: List[Dimension] = field(default_factory=list)
    metrics: List[Metric] = field(default_factory=list)
    time_dimension: Optional[str] = None
    row_count: int = 0
    warnings: List[str] = field(default_factory=list)
    source: str = "auto"                         # auto | catalog | user

    def metric_by_name(self, name: str) -> Optional[Metric]:
        for m in self.metrics:
            if m.name == name:
                return m
        return None

    def preferred_metrics(self) -> List[Metric]:
        return [m for m in self.metrics if m.preferred]

    def summary_dict(self) -> Dict[str, Any]:
        return {
            "table_name": self.table_name,
            "row_count": self.row_count,
            "entities": [asdict(e) for e in self.entities],
            "dimensions": [
                {**asdict(d), "dim_type": d.dim_type.value} for d in self.dimensions
            ],
            "metrics": [
                {
                    **{k: v for k, v in asdict(m).items() if k != "agg" and k != "additivity"},
                    "agg": m.agg.value,
                    "additivity": m.additivity.value,
                    "sql": m.sql_expression(),
                }
                for m in self.metrics
            ],
            "time_dimension": self.time_dimension,
            "warnings": self.warnings,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(name: str) -> str:
    """DuckDB-safe double-quote identifier."""
    return '"' + str(name).replace('"', '""') + '"'


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _token_set(s: str) -> set:
    return set(_norm(s).split())


# Name heuristics (industry common patterns)
_REVENUE_HINTS = {"revenue", "amount", "sales", "total", "price", "value", "gmv", "net", "gross", "mrr", "arr"}
_ORDER_HINTS = {"order", "orders", "transaction", "txn", "invoice", "booking", "purchase"}
_CUSTOMER_HINTS = {"customer", "user", "client", "buyer", "student", "account", "member", "subscriber"}
_ID_HINTS = {"id", "uuid", "guid", "key", "code", "pk", "fk", "number", "no"}
_TIME_HINTS = {"date", "time", "timestamp", "created", "updated", "order_date", "event", "day", "month", "year"}
_RATING_HINTS = {"rating", "score", "stars", "nps", "csat"}


def _looks_like_id(col: str, semantic_type: str, unique_ratio: float, physical_type: str = "") -> bool:
    """
    Conservative ID detection.
    Name-based measure hints always win over a high unique_ratio on small samples
    (Phase-1 detector can mark float measures as Identifier when n is tiny).
    """
    cl = _norm(col)
    # Strong measure name → never treat as ID
    if any(h in cl for h in _REVENUE_HINTS | _RATING_HINTS):
        return False
    if any(h in cl.split() or h == cl for h in _ID_HINTS):
        return True
    if semantic_type == "Identifier":
        # Only trust Identifier label when name also looks like an id OR ratio is extreme
        # and physical type is integer-like
        pt = (physical_type or "").lower()
        if "float" in pt or "double" in pt or "decimal" in pt:
            return False
        return True
    if unique_ratio >= 0.98 and semantic_type in ("Continuous Numerical", "Unknown"):
        # Very high uniqueness on integer columns → likely surrogate key
        pt = (physical_type or "").lower()
        if "int" in pt:
            return True
    return False


def _looks_like_measure(col: str, semantic_type: str, unique_ratio: float, physical_type: str = "") -> bool:
    if _looks_like_id(col, semantic_type, unique_ratio, physical_type):
        return False
    if semantic_type in ("Continuous Numerical", "Currency", "Percentage"):
        return True
    cl = _norm(col)
    if any(h in cl for h in _REVENUE_HINTS | _RATING_HINTS):
        return True
    # Float columns that survived ID filter are good measure candidates
    pt = (physical_type or "").lower()
    if ("float" in pt or "double" in pt or "decimal" in pt) and unique_ratio < 1.0:
        return True
    if ("float" in pt or "double" in pt) and any(h in cl for h in ("amt", "qty", "quantity", "cost", "fee", "price")):
        return True
    return False


# ---------------------------------------------------------------------------
# Schema helpers (lazy load Phase-1 detector)
# ---------------------------------------------------------------------------

def _semantic_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return Phase-1 semantic detection DataFrame; never raises."""
    try:
        core = Path(__file__).resolve().parent
        if str(core.parent.parent) not in sys.path:
            sys.path.insert(0, str(core.parent.parent))
        import importlib.util
        path = core / "schema.py"
        spec = importlib.util.spec_from_file_location("_schema_sem", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_schema_sem"] = mod
        spec.loader.exec_module(mod)
        return mod.detect_schema_semantic(df)
    except Exception:
        # Minimal fallback
        rows = []
        for c in df.columns:
            s = df[c]
            rows.append({
                "column": c,
                "physical_type": str(s.dtype),
                "semantic_type": "Unknown",
                "confidence": 0.0,
                "unique_count": int(s.nunique(dropna=True)),
                "unique_ratio": float(s.nunique(dropna=True) / max(len(s), 1)),
                "missing_pct": float(s.isna().mean() * 100),
                "recommendation": "",
            })
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Auto builder
# ---------------------------------------------------------------------------

def build_semantic_model(
    workspace: Any,
    table_name: str,
    max_dim_cardinality: int = 200,
) -> SemanticModel:
    """
    Build a SemanticModel for a registered / cleaned dataset.

    Industry behaviour:
    - Prefer Phase-1 semantic types over raw dtypes
    - Never promote high-uniqueness numeric columns to measures
    - Always propose at least a row-count metric
    - Prefer currency/revenue-like columns for SUM metrics
    - Propose COUNT DISTINCT on natural entity columns
    - Propose AOV-style ratio only when both value + entity columns exist
    """
    model = SemanticModel(table_name=table_name, source="auto")

    if workspace is None:
        model.warnings.append("Workspace is None.")
        return model

    record = None
    try:
        record = workspace.get(table_name)
    except Exception as e:
        model.warnings.append(f"Could not load dataset record: {e}")
        return model

    if record is None:
        model.warnings.append(f"Dataset `{table_name}` not found.")
        return model

    df = getattr(record, "cleaned_df", None)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        model.warnings.append("Cleaned DataFrame is empty or missing.")
        # Still return a minimal model
        model.metrics.append(
            Metric(
                name="row_count",
                label="Row count",
                description="Number of rows",
                agg=AggType.COUNT,
                additivity=Additivity.FULL,
                preferred=True,
                confidence=0.5,
                reason="Fallback metric on empty frame",
            )
        )
        return model

    model.row_count = len(df)
    sem = _semantic_frame(df)
    sem_map = {str(r["column"]): r for _, r in sem.iterrows()}

    # ---- Entities ----
    for col in df.columns:
        info = sem_map.get(col, {})
        stype = str(info.get("semantic_type", "Unknown"))
        ur = float(info.get("unique_ratio", 0) or 0)
        ptype = str(info.get("physical_type", "") or "")
        if _looks_like_id(col, stype, ur, ptype):
            role = "primary" if ur >= 0.98 else "natural"
            model.entities.append(
                Entity(
                    name=_norm(col).replace(" ", "_") or col,
                    column=col,
                    role=role,
                    confidence=float(info.get("confidence", 0.7) or 0.7),
                    reason=f"semantic={stype}, unique_ratio={ur:.2f}",
                )
            )

    # ---- Dimensions ----
    time_candidates: List[Tuple[float, str]] = []
    for col in df.columns:
        info = sem_map.get(col, {})
        stype = str(info.get("semantic_type", "Unknown"))
        ur = float(info.get("unique_ratio", 0) or 0)
        card = int(info.get("unique_count", 0) or 0)
        conf = float(info.get("confidence", 0.5) or 0.5)

        if stype in ("DateTime", "DateTime (Text)"):
            model.dimensions.append(
                Dimension(
                    name=_norm(col).replace(" ", "_") or col,
                    column=col,
                    dim_type=DimensionType.TIME,
                    label=col,
                    cardinality=card,
                    confidence=max(conf, 0.85),
                    is_time_grain_candidate=True,
                    reason="Detected as datetime",
                )
            )
            time_candidates.append((conf, col))
            continue

        if stype == "Boolean":
            model.dimensions.append(
                Dimension(
                    name=_norm(col).replace(" ", "_") or col,
                    column=col,
                    dim_type=DimensionType.BOOLEAN,
                    label=col,
                    cardinality=card,
                    confidence=conf,
                    reason="Boolean column",
                )
            )
            continue

        if stype in ("Categorical", "Categorical (Numeric)") and card <= max_dim_cardinality:
            model.dimensions.append(
                Dimension(
                    name=_norm(col).replace(" ", "_") or col,
                    column=col,
                    dim_type=DimensionType.CATEGORICAL,
                    label=col,
                    cardinality=card,
                    confidence=conf,
                    reason=f"Low-cardinality categorical (n={card})",
                )
            )
            continue

        # Name-based time fallback
        if any(h in _norm(col) for h in _TIME_HINTS) and card > 1:
            model.dimensions.append(
                Dimension(
                    name=_norm(col).replace(" ", "_") or col,
                    column=col,
                    dim_type=DimensionType.TIME,
                    label=col,
                    cardinality=card,
                    confidence=0.55,
                    is_time_grain_candidate=True,
                    reason="Name heuristic for time",
                )
            )
            time_candidates.append((0.55, col))

    if time_candidates:
        time_candidates.sort(key=lambda x: (-x[0], x[1]))
        model.time_dimension = time_candidates[0][1]

    # ---- Measures / Metrics ----
    measure_cols: List[Tuple[float, str, str]] = []  # (score, col, stype)
    entity_cols: List[str] = [e.column for e in model.entities]
    people_cols = [
        c for c in df.columns
        if any(h in _norm(c) for h in _CUSTOMER_HINTS)
        and not _looks_like_id(
            c,
            str(sem_map.get(c, {}).get("semantic_type", "")),
            float(sem_map.get(c, {}).get("unique_ratio", 0) or 0),
            str(sem_map.get(c, {}).get("physical_type", "") or ""),
        )
    ]

    for col in df.columns:
        info = sem_map.get(col, {})
        stype = str(info.get("semantic_type", "Unknown"))
        ur = float(info.get("unique_ratio", 0) or 0)
        conf = float(info.get("confidence", 0.5) or 0.5)
        ptype = str(info.get("physical_type", "") or "")
        if not _looks_like_measure(col, stype, ur, ptype):
            continue
        score = conf
        cl = _norm(col)
        if any(h in cl for h in _REVENUE_HINTS):
            score += 0.25
        if stype == "Currency":
            score += 0.2
        if any(h in cl for h in _RATING_HINTS):
            score += 0.1
        measure_cols.append((score, col, stype))

    measure_cols.sort(key=lambda x: -x[0])

    # Always: row count
    model.metrics.append(
        Metric(
            name="row_count",
            label="Row count",
            description="Total number of records",
            agg=AggType.COUNT,
            additivity=Additivity.FULL,
            preferred=True,
            confidence=0.99,
            tags=["volume"],
            reason="Universal metric",
        )
    )

    # SUM / AVG for top measures
    for score, col, stype in measure_cols[:6]:
        safe_name = re.sub(r"[^a-z0-9]+", "_", _norm(col)).strip("_") or "value"
        is_revenue = any(h in _norm(col) for h in _REVENUE_HINTS) or stype == "Currency"
        is_rating = any(h in _norm(col) for h in _RATING_HINTS)

        model.metrics.append(
            Metric(
                name=f"sum_{safe_name}",
                label=f"Total {col}",
                description=f"Sum of {col}",
                agg=AggType.SUM,
                additivity=Additivity.FULL,
                measure_column=col,
                preferred=is_revenue,
                confidence=min(0.95, 0.6 + score * 0.3),
                tags=["revenue"] if is_revenue else ["measure"],
                reason=f"Continuous measure (semantic={stype})",
            )
        )
        model.metrics.append(
            Metric(
                name=f"avg_{safe_name}",
                label=f"Average {col}",
                description=f"Arithmetic mean of {col}",
                agg=AggType.AVG,
                additivity=Additivity.SEMI,
                measure_column=col,
                preferred=is_rating,
                confidence=min(0.9, 0.55 + score * 0.3),
                tags=["rating"] if is_rating else ["measure"],
                reason="Simple average – semi-additive across groups",
            )
        )

    # COUNT DISTINCT on entities / people
    seen_entities = set()
    for col in (people_cols + entity_cols):
        if col in seen_entities:
            continue
        seen_entities.add(col)
        safe = re.sub(r"[^a-z0-9]+", "_", _norm(col)).strip("_") or "entity"
        model.metrics.append(
            Metric(
                name=f"unique_{safe}",
                label=f"Unique {col}",
                description=f"Count of distinct values in {col}",
                agg=AggType.COUNT_DISTINCT,
                additivity=Additivity.FULL,
                entity_column=col,
                preferred=any(h in _norm(col) for h in _CUSTOMER_HINTS),
                confidence=0.88,
                tags=["volume", "customers"],
                reason="Distinct entity count",
            )
        )

    # Ratio: AOV-style when we have a value measure + order/entity column
    value_col = None
    for _, col, stype in measure_cols:
        if any(h in _norm(col) for h in _REVENUE_HINTS) or stype == "Currency":
            value_col = col
            break
    if value_col is None and measure_cols:
        value_col = measure_cols[0][1]

    order_like = None
    for c in df.columns:
        if any(h in _norm(c) for h in _ORDER_HINTS):
            order_like = c
            break
    if order_like is None and entity_cols:
        order_like = entity_cols[0]

    if value_col and order_like and value_col != order_like:
        model.metrics.append(
            Metric(
                name="aov",
                label="Average order value (AOV)",
                description="SUM(amount) / COUNT(DISTINCT order) – not AVG(amount)",
                agg=AggType.RATIO,
                additivity=Additivity.NON,
                measure_column=value_col,
                entity_column=order_like,
                numerator=value_col,
                denominator=order_like,
                preferred=True,
                confidence=0.9,
                tags=["revenue", "ratio"],
                reason="Classic ratio metric; non-additive",
                expr=f"SUM({_q(value_col)}) / NULLIF(COUNT(DISTINCT {_q(order_like)}), 0)",
            )
        )

    if not model.metrics:
        model.warnings.append("No metrics could be inferred.")
    if not model.dimensions:
        model.warnings.append("No dimensions inferred – grouping will be limited.")

    return model


# ---------------------------------------------------------------------------
# Question → Metric resolution
# ---------------------------------------------------------------------------

def resolve_metrics_for_question(
    question: str,
    model: SemanticModel,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Rank metrics that best answer the question.
    Returns list of dicts with key, label, sql, reason, confidence, additivity.
    """
    q = _norm(question)
    if not q or not model.metrics:
        return []

    wants_unique = any(w in q for w in ("unique", "distinct", "how many different", "number of customers", "number of students", "number of users"))
    wants_avg = any(w in q for w in ("average", "avg", "mean"))
    wants_sum = any(w in q for w in ("total", "sum", "revenue", "sales", "gmv"))
    wants_count = any(w in q for w in ("how many", "count", "number of", "volume"))
    wants_aov = any(w in q for w in ("aov", "average order", "avg order", "order value"))
    wants_min = "min" in q or "lowest" in q or "smallest" in q
    wants_max = "max" in q or "highest" in q or "largest" in q or "top" in q

    scored: List[Tuple[float, Metric, str]] = []

    for m in model.metrics:
        score = 0.0
        reason_bits = []

        # Strong signals
        if wants_aov and m.name == "aov":
            score += 10
            reason_bits.append("Question asks for AOV / average order value")
        if wants_unique and m.agg == AggType.COUNT_DISTINCT:
            score += 6
            reason_bits.append("Question asks for unique / distinct count")
        if wants_sum and m.agg == AggType.SUM:
            score += 5
            reason_bits.append("Question asks for total / sum")
        if wants_avg and m.agg == AggType.AVG:
            score += 5
            reason_bits.append("Question asks for average")
        if wants_count and m.agg == AggType.COUNT and not wants_unique:
            score += 4
            reason_bits.append("Question asks for a count")
        if wants_min and m.agg == AggType.MIN:
            score += 4
        if wants_max and m.agg == AggType.MAX:
            score += 4

        # Name / label overlap
        tokens = _token_set(m.label) | _token_set(m.name) | set(m.tags)
        overlap = tokens & _token_set(q)
        if overlap:
            score += 1.5 * len(overlap)
            reason_bits.append(f"Name overlap: {', '.join(sorted(overlap)[:3])}")

        if m.preferred:
            score += 0.8

        score += m.confidence * 0.5

        # Penalty: do not recommend AVG when user wants AOV
        if wants_aov and m.agg == AggType.AVG:
            score -= 4
            reason_bits.append("Penalised: AVG is wrong for AOV")

        # Penalty: SUM on non-preferred when unique is asked
        if wants_unique and m.agg == AggType.SUM:
            score -= 2

        if score > 0.5:
            scored.append((score, m, "; ".join(reason_bits) or m.reason))

    scored.sort(key=lambda x: -x[0])
    out = []
    seen = set()
    for score, m, reason in scored:
        if m.name in seen:
            continue
        seen.add(m.name)
        out.append({
            "key": m.name,
            "label": m.label,
            "sql_hint": m.sql_expression(),
            "agg": m.agg.value,
            "additivity": m.additivity.value,
            "confidence": round(min(0.99, score / 12), 3),
            "reason": reason,
            "preferred": m.preferred,
        })
        if len(out) >= top_k:
            break
    return out


def metric_prompt_block(question: str, model: SemanticModel) -> str:
    """
    Produce a compact, high-signal block for the NL→SQL system / user prompt.
    Industry rule: explicit SQL expressions + hard rules for ratios and IDs.
    """
    tips = resolve_metrics_for_question(question, model, top_k=6)
    lines = [
        "SEMANTIC METRIC RULES (must follow):",
        "1. Prefer the exact SQL expressions below when the question matches a metric.",
        "2. NEVER use AVG() for Average Order Value or any ratio metric – use SUM / NULLIF(COUNT(DISTINCT …), 0).",
        "3. NEVER SUM or AVG columns that are identifiers (id, uuid, key, code).",
        "4. For unique customers/users/orders always use COUNT(DISTINCT col).",
        "5. Ratio metrics are NON-additive – do not average them across groups; compute ratio after aggregation.",
        "6. Always NULLIF denominators to avoid divide-by-zero.",
    ]

    if model.time_dimension:
        lines.append(f"7. Preferred time dimension: {_q(model.time_dimension)}")

    if tips:
        lines.append("")
        lines.append("Resolved metrics for this question:")
        for t in tips:
            lines.append(
                f"- {t['label']} [{t['agg']}, {t['additivity']}]: {t['sql_hint']}"
                f"  // {t['reason']}"
            )
    else:
        # Fallback catalogue summary
        preferred = model.preferred_metrics() or model.metrics[:5]
        if preferred:
            lines.append("")
            lines.append("Available preferred metrics:")
            for m in preferred:
                lines.append(f"- {m.label}: {m.sql_expression()}")

    if model.warnings:
        lines.append("")
        lines.append("Model warnings: " + "; ".join(model.warnings[:3]))

    return "\n".join(lines)


def model_prompt_summary(model: SemanticModel, max_metrics: int = 12) -> str:
    """Full metric catalogue summary for schema context (compact)."""
    lines = [f"SEMANTIC MODEL for table {_q(model.table_name)} (rows={model.row_count:,})"]
    if model.entities:
        ents = ", ".join(f"{e.column}({e.role})" for e in model.entities[:6])
        lines.append(f"Entities: {ents}")
    if model.dimensions:
        dims = ", ".join(
            f"{d.column}[{d.dim_type.value}]" for d in model.dimensions[:10]
        )
        lines.append(f"Dimensions: {dims}")
    if model.time_dimension:
        lines.append(f"Time dimension: {_q(model.time_dimension)}")
    lines.append("Metrics:")
    for m in model.metrics[:max_metrics]:
        lines.append(
            f"  - {m.name} ({m.agg.value}, {m.additivity.value}): {m.sql_expression()}  // {m.label}"
        )
    if len(model.metrics) > max_metrics:
        lines.append(f"  … +{len(model.metrics) - max_metrics} more")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience for callers that only have columns (legacy / tests)
# ---------------------------------------------------------------------------

def build_model_from_dataframe(
    df: pd.DataFrame,
    table_name: str = "data",
) -> SemanticModel:
    """Build a model without a full Workspace (useful for unit tests)."""

    class _FakeRecord:
        def __init__(self, frame: pd.DataFrame):
            self.cleaned_df = frame
            self.metadata = {}

    class _FakeWS:
        def get(self, name: str):
            return _FakeRecord(df)

    return build_semantic_model(_FakeWS(), table_name)


# ---------------------------------------------------------------------------
# Public API surface used by other modules
# ---------------------------------------------------------------------------

__all__ = [
    "AggType",
    "DimensionType",
    "Additivity",
    "Entity",
    "Dimension",
    "Metric",
    "SemanticModel",
    "build_semantic_model",
    "build_model_from_dataframe",
    "resolve_metrics_for_question",
    "metric_prompt_block",
    "model_prompt_summary",
]
