"""
InsightForgeAI – Root-Cause Style Breakdown (Phase 4.3)

Answers "why did X drop/rise?" with a deterministic dimension drill:
  - pick measure + dimensions from schema heuristics
  - compute contribution by dimension (share of change or share of total)
  - rank top drivers
  - optional LLM narrative is left to the insight agent

Never invents columns. Clear "cannot compute" when inputs are missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class DimensionBreakdown:
    dimension: str
    rows: List[Dict[str, Any]] = field(default_factory=list)  # value, metric, share_pct, delta?
    top_driver: Optional[str] = None
    top_share_pct: Optional[float] = None


@dataclass
class RootCauseResult:
    success: bool
    measure: Optional[str] = None
    measure_agg: str = "sum"
    breakdowns: List[DimensionBreakdown] = field(default_factory=list)
    narrative_bullets: List[str] = field(default_factory=list)
    sql_evidence: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    cannot_compute_reason: Optional[str] = None


def _numeric_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _dim_cols(df: pd.DataFrame, max_card: int = 40) -> List[str]:
    out = []
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]) and df[c].nunique(dropna=True) > max_card:
            continue
        nuniq = df[c].nunique(dropna=True)
        if 1 < nuniq <= max_card:
            # prefer object / low-card numeric codes
            out.append(c)
    # Prefer name-like dimensions
    preferred = []
    other = []
    for c in out:
        cl = c.lower()
        if any(k in cl for k in ("region", "segment", "category", "channel", "product", "country", "state", "city", "type", "status", "brand")):
            preferred.append(c)
        else:
            other.append(c)
    return preferred + other


def _pick_measure(df: pd.DataFrame, question: str = "") -> Tuple[Optional[str], str]:
    q = (question or "").lower()
    nums = _numeric_cols(df)
    if not nums:
        return None, "sum"
    # Prefer amount/revenue/sales/price/qty from question or column names
    keywords = ["revenue", "sales", "amount", "gmv", "price", "qty", "quantity", "orders", "profit", "cost"]
    for kw in keywords:
        if kw in q:
            for c in nums:
                if kw in c.lower():
                    return c, "sum"
    for kw in keywords:
        for c in nums:
            if kw in c.lower():
                return c, "sum"
    # Avoid pure id-like columns
    for c in nums:
        cl = c.lower()
        if any(x in cl for x in ("id", "uuid", "key", "code", "index")):
            continue
        return c, "sum"
    return nums[0], "sum"


def _agg(series: pd.Series, how: str) -> float:
    s = pd.to_numeric(series, errors="coerce")
    if how == "mean":
        return float(s.mean()) if len(s) else 0.0
    if how == "count":
        return float(s.count())
    return float(s.sum()) if len(s) else 0.0


def breakdown_by_dimension(
    df: pd.DataFrame,
    measure: str,
    dimension: str,
    agg: str = "sum",
    top_n: int = 10,
) -> DimensionBreakdown:
    work = df[[dimension, measure]].copy()
    work[measure] = pd.to_numeric(work[measure], errors="coerce")
    work[dimension] = work[dimension].astype(str)
    grouped = work.groupby(dimension, dropna=False)[measure]
    if agg == "mean":
        totals = grouped.mean()
    elif agg == "count":
        totals = grouped.count()
    else:
        totals = grouped.sum()
    totals = totals.sort_values(ascending=False)
    grand = float(totals.sum()) if len(totals) else 0.0
    rows = []
    for val, metric in totals.head(top_n).items():
        m = float(metric)
        share = (m / grand * 100.0) if grand else 0.0
        rows.append({"value": str(val), "metric": round(m, 4), "share_pct": round(share, 2)})
    top = rows[0] if rows else None
    return DimensionBreakdown(
        dimension=dimension,
        rows=rows,
        top_driver=top["value"] if top else None,
        top_share_pct=top["share_pct"] if top else None,
    )


def run_root_cause(
    df: pd.DataFrame,
    question: str = "",
    measure: Optional[str] = None,
    dimensions: Optional[List[str]] = None,
    max_dimensions: int = 3,
) -> RootCauseResult:
    if df is None or df.empty:
        return RootCauseResult(success=False, error="No data for root-cause analysis.")

    agg = "sum"
    if not measure:
        measure, agg = _pick_measure(df, question)
    if not measure or measure not in df.columns:
        return RootCauseResult(
            success=False,
            cannot_compute_reason="No numeric measure column found for breakdown.",
            error="Cannot compute root-cause: need a numeric measure (e.g. amount, revenue).",
        )

    dims = dimensions or _dim_cols(df)
    dims = [d for d in dims if d != measure][:max_dimensions]
    if not dims:
        return RootCauseResult(
            success=False,
            measure=measure,
            cannot_compute_reason="No categorical dimensions with usable cardinality.",
            error="Cannot compute root-cause: need dimensions like region, segment, category.",
        )

    result = RootCauseResult(success=True, measure=measure, measure_agg=agg)
    for d in dims:
        try:
            bd = breakdown_by_dimension(df, measure=measure, dimension=d, agg=agg)
            result.breakdowns.append(bd)
            # SQL evidence (DuckDB-style)
            result.sql_evidence.append(
                f'SELECT "{d}", SUM("{measure}") AS total '\n                f'FROM current_table GROUP BY "{d}" ORDER BY total DESC LIMIT 10'
            )
            if bd.top_driver is not None:
                result.narrative_bullets.append(
                    f"By **{d}**, top driver is **{bd.top_driver}** "
                    f"({bd.top_share_pct:.1f}% of {measure})."
                )
        except Exception as e:
            result.warnings.append(f"Dimension `{d}` skipped: {e}")

    if not result.breakdowns:
        result.success = False
        result.error = "All dimension breakdowns failed."
        return result

    # Overall headline
    total = float(pd.to_numeric(df[measure], errors="coerce").sum())
    result.narrative_bullets.insert(
        0, f"Total **{measure}** across the loaded slice is **{total:,.2f}**."
    )
    return result


def looks_like_root_cause_question(question: str) -> bool:
    q = (question or "").lower()
    markers = [
        "why did", "why has", "what caused", "root cause", "driver", "drivers",
        "breakdown", "break down", "drill", "contribution", "what drove",
        "which region", "which segment", "drop", "decline", "fell", "increase",
    ]
    return any(m in q for m in markers)


__all__ = [
    "RootCauseResult",
    "DimensionBreakdown",
    "run_root_cause",
    "looks_like_root_cause_question",
    "breakdown_by_dimension",
]
