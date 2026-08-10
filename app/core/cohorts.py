"""
InsightForgeAI – Cohort / RFM helpers (Phase 4.3)

Only computes when required columns are present; otherwise returns a clear
cannot_compute reason (never guesses customer_id / order_date).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass
class RFMResult:
    success: bool
    rfm_df: Optional[pd.DataFrame] = None
    segment_counts: Dict[str, int] = field(default_factory=dict)
    narrative: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    cannot_compute_reason: Optional[str] = None
    columns_used: Dict[str, str] = field(default_factory=dict)


def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    for c in df.columns:
        cl = c.lower()
        for cand in candidates:
            if cand in cl:
                return c
    return None


def detect_rfm_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    customer = _find_col(df, ["customer_id", "user_id", "client_id", "cust_id", "account_id"])
    order_date = _find_col(df, ["order_date", "purchase_date", "invoice_date", "date", "created_at", "timestamp"])
    amount = _find_col(df, ["amount", "revenue", "sales", "gmv", "total", "order_total", "price"])
    return {"customer_id": customer, "order_date": order_date, "amount": amount}


def run_rfm(df: pd.DataFrame, as_of: Optional[pd.Timestamp] = None) -> RFMResult:
    if df is None or df.empty:
        return RFMResult(success=False, error="No data for RFM.")

    cols = detect_rfm_columns(df)
    missing = [k for k, v in cols.items() if not v]
    if missing:
        return RFMResult(
            success=False,
            cannot_compute_reason=f"Missing required columns for RFM: {', '.join(missing)}",
            error=(
                "Cannot compute RFM/cohorts without customer id, order date, and amount columns. "
                f"Detected: {cols}"
            ),
            columns_used={k: v for k, v in cols.items() if v},
        )

    cust, date_c, amt = cols["customer_id"], cols["order_date"], cols["amount"]
    work = df[[cust, date_c, amt]].copy()
    work[date_c] = pd.to_datetime(work[date_c], errors="coerce")
    work[amt] = pd.to_numeric(work[amt], errors="coerce")
    work = work.dropna(subset=[cust, date_c, amt])
    if work.empty:
        return RFMResult(success=False, error="RFM columns present but no valid rows after cleaning.")

    as_of = as_of or work[date_c].max()
    grouped = work.groupby(cust).agg(
        recency_days=(date_c, lambda s: (as_of - s.max()).days),
        frequency=(date_c, "count"),
        monetary=(amt, "sum"),
    )
    # Quintile scores 1–5 (5 = best). Recency inverted.
    try:
        grouped["R"] = pd.qcut(grouped["recency_days"], 5, labels=[5, 4, 3, 2, 1], duplicates="drop")
    except Exception:
        grouped["R"] = 3
    try:
        grouped["F"] = pd.qcut(grouped["frequency"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
    except Exception:
        grouped["F"] = 3
    try:
        grouped["M"] = pd.qcut(grouped["monetary"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
    except Exception:
        grouped["M"] = 3

    def _seg(row) -> str:
        try:
            r, f = int(row["R"]), int(row["F"])
        except Exception:
            return "Other"
        if r >= 4 and f >= 4:
            return "Champions"
        if r >= 4 and f <= 2:
            return "New / Promising"
        if r <= 2 and f >= 4:
            return "At Risk"
        if r <= 2 and f <= 2:
            return "Hibernating"
        return "Need Attention"

    grouped["segment"] = grouped.apply(_seg, axis=1)
    rfm_df = grouped.reset_index()
    counts = rfm_df["segment"].value_counts().to_dict()
    narrative = [
        f"RFM computed for **{len(rfm_df):,}** customers "
        f"(as of {pd.Timestamp(as_of).date()}).",
        "Segments: " + ", ".join(f"{k}: {v}" for k, v in list(counts.items())[:6]),
    ]
    return RFMResult(
        success=True,
        rfm_df=rfm_df,
        segment_counts={str(k): int(v) for k, v in counts.items()},
        narrative=narrative,
        columns_used={"customer_id": cust, "order_date": date_c, "amount": amt},
    )


def looks_like_cohort_question(question: str) -> bool:
    q = (question or "").lower()
    return any(k in q for k in ("rfm", "cohort", "retention", "customer segment", "recency", "frequency", "monetary"))


__all__ = ["RFMResult", "run_rfm", "detect_rfm_columns", "looks_like_cohort_question"]
