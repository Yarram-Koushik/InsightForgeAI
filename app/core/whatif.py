"""
InsightForgeAI – What-If / Scenario Analysis (Phase 4.3)

Example: "+10% price on North" → recompute measure under a simple scalar shock
optionally scoped to a dimension value.

Deterministic. No ML. Clear cannot-compute when measure/dimension missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class WhatIfResult:
    success: bool
    measure: Optional[str] = None
    dimension: Optional[str] = None
    dimension_value: Optional[str] = None
    pct_change: Optional[float] = None
    baseline_total: Optional[float] = None
    scenario_total: Optional[float] = None
    delta: Optional[float] = None
    delta_pct: Optional[float] = None
    baseline_scoped: Optional[float] = None
    scenario_scoped: Optional[float] = None
    narrative: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    cannot_compute_reason: Optional[str] = None


def parse_whatif_intent(question: str) -> Optional[Dict[str, Any]]:
    """
    Parse patterns like:
      +10% price on North
      what if revenue increases by 15%
      simulate 5% drop in amount for region South
    """
    q = (question or "").strip()
    if not q:
        return None
    ql = q.lower()

    # Must look like a scenario
    if not any(k in ql for k in ("what if", "whatif", "scenario", "simulate", "+", "-", "%", "percent", "increase by", "decrease by", "drop by")):
        # still allow "+10% on North"
        if not re.search(r"[+-]?\d+(\.\d+)?\s*%", q):
            return None

    pct = None
    m = re.search(r"([+-]?)\s*(\d+(?:\.\d+)?)\s*%", q)
    if m:
        sign, num = m.group(1), float(m.group(2))
        pct = num
        if sign == "-" or any(w in ql for w in ("decrease", "drop", "reduce", "fall")):
            pct = -abs(pct)
        elif sign == "+" or any(w in ql for w in ("increase", "rise", "grow", "up")):
            pct = abs(pct)
        elif sign == "":
            # bare "10%" → treat as + if increase words else as stated later
            if any(w in ql for w in ("decrease", "drop", "reduce")):
                pct = -abs(pct)
            else:
                pct = abs(pct)
    if pct is None:
        return None

    # Dimension value: "on North", "for South", "in Premium"
    dim_val = None
    m2 = re.search(r"\b(?:on|for|in)\s+([A-Za-z0-9_\- ]{2,40})$", q.strip(), re.I)
    if m2:
        dim_val = m2.group(1).strip().strip("'\"")

    return {"pct_change": pct, "dimension_value": dim_val, "raw": q}


def _pick_measure(df: pd.DataFrame, question: str = "") -> Optional[str]:
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not nums:
        return None
    ql = (question or "").lower()
    for kw in ("price", "amount", "revenue", "sales", "gmv", "profit", "cost", "qty", "quantity"):
        if kw in ql:
            for c in nums:
                if kw in c.lower():
                    return c
    for kw in ("price", "amount", "revenue", "sales", "gmv", "profit"):
        for c in nums:
            if kw in c.lower():
                return c
    for c in nums:
        if not any(x in c.lower() for x in ("id", "uuid", "key", "code")):
            return c
    return nums[0]


def _find_dimension_for_value(df: pd.DataFrame, value: str) -> Optional[str]:
    if not value:
        return None
    target = str(value).strip().lower()
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]) and df[c].nunique() > 50:
            continue
        try:
            vals = set(df[c].dropna().astype(str).str.lower().unique()[:500])
            if target in vals:
                return c
            # partial match
            for v in vals:
                if target == v or target in v or v in target:
                    return c
        except Exception:
            continue
    return None


def run_whatif(
    df: pd.DataFrame,
    question: str = "",
    pct_change: Optional[float] = None,
    measure: Optional[str] = None,
    dimension: Optional[str] = None,
    dimension_value: Optional[str] = None,
) -> WhatIfResult:
    if df is None or df.empty:
        return WhatIfResult(success=False, error="No data for what-if analysis.")

    parsed = parse_whatif_intent(question) if pct_change is None else None
    if parsed:
        pct_change = pct_change if pct_change is not None else parsed.get("pct_change")
        dimension_value = dimension_value or parsed.get("dimension_value")

    if pct_change is None:
        return WhatIfResult(
            success=False,
            cannot_compute_reason="Could not parse percentage change from question.",
            error="Specify a scenario like '+10% amount on North' or 'what if revenue drops 5%'.",
        )

    measure = measure or _pick_measure(df, question)
    if not measure or measure not in df.columns:
        return WhatIfResult(
            success=False,
            cannot_compute_reason="No numeric measure found.",
            error="Need a numeric measure column (amount, revenue, price, …).",
        )

    work = df.copy()
    work[measure] = pd.to_numeric(work[measure], errors="coerce").fillna(0.0)
    baseline_total = float(work[measure].sum())

    mask = pd.Series([True] * len(work))
    if dimension_value:
        dimension = dimension or _find_dimension_for_value(work, dimension_value)
        if not dimension:
            return WhatIfResult(
                success=False,
                measure=measure,
                pct_change=pct_change,
                cannot_compute_reason=f"Could not map '{dimension_value}' to a dimension column.",
                error=f"No dimension contains value '{dimension_value}'.",
            )
        mask = work[dimension].astype(str).str.lower() == str(dimension_value).lower()
        if not mask.any():
            # try contains
            mask = work[dimension].astype(str).str.lower().str.contains(str(dimension_value).lower(), na=False)
        if not mask.any():
            return WhatIfResult(
                success=False,
                measure=measure,
                dimension=dimension,
                dimension_value=dimension_value,
                error=f"No rows matched {dimension}={dimension_value}.",
            )

    factor = 1.0 + (float(pct_change) / 100.0)
    scenario = work[measure].copy()
    scenario.loc[mask] = scenario.loc[mask] * factor
    scenario_total = float(scenario.sum())
    baseline_scoped = float(work.loc[mask, measure].sum())
    scenario_scoped = float(scenario.loc[mask].sum())
    delta = scenario_total - baseline_total
    delta_pct = (delta / baseline_total * 100.0) if baseline_total else None

    result = WhatIfResult(
        success=True,
        measure=measure,
        dimension=dimension,
        dimension_value=dimension_value,
        pct_change=float(pct_change),
        baseline_total=round(baseline_total, 4),
        scenario_total=round(scenario_total, 4),
        delta=round(delta, 4),
        delta_pct=round(delta_pct, 4) if delta_pct is not None else None,
        baseline_scoped=round(baseline_scoped, 4),
        scenario_scoped=round(scenario_scoped, 4),
    )

    scope = f" on **{dimension}={dimension_value}**" if dimension and dimension_value else " (all rows)"
    result.narrative.append(
        f"Scenario: **{pct_change:+.1f}%** applied to **{measure}**{scope}."
    )
    result.narrative.append(
        f"Baseline total {measure}: **{baseline_total:,.2f}** → scenario **{scenario_total:,.2f}** "
        f"(Δ **{delta:+,.2f}**, **{delta_pct:+.2f}%** overall)."
        if delta_pct is not None
        else f"Baseline total {measure}: **{baseline_total:,.2f}** → scenario **{scenario_total:,.2f}**."
    )
    if dimension and dimension_value:
        result.narrative.append(
            f"Scoped slice moved from **{baseline_scoped:,.2f}** to **{scenario_scoped:,.2f}**."
        )
    return result


def looks_like_whatif_question(question: str) -> bool:
    return parse_whatif_intent(question) is not None


__all__ = ["WhatIfResult", "run_whatif", "parse_whatif_intent", "looks_like_whatif_question"]
