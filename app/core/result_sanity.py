"""Result sanity checks (Phase 2.7)"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List
import pandas as pd

@dataclass
class SanityReport:
    ok: bool = True
    warnings: List[str] = field(default_factory=list)
    hard_error: Optional[str] = None

def check_result_df(df: Optional[pd.DataFrame], question: str = "") -> SanityReport:
    report = SanityReport()
    if df is None:
        report.ok = False
        report.hard_error = "Result is None."
        return report
    if not isinstance(df, pd.DataFrame):
        report.ok = False
        report.hard_error = "Result is not a DataFrame."
        return report
    if df.empty:
        report.warnings.append("Query returned 0 rows.")
        return report
    q = (question or "").lower()
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_numeric_dtype(s):
            vals = pd.to_numeric(s, errors="coerce")
            if vals.notna().any():
                mn, mx = float(vals.min()), float(vals.max())
                if any(k in col.lower() for k in ("revenue", "amount", "sales", "price", "total")) and mn < 0:
                    report.warnings.append(f"Column '{col}' has negative values (min={mn}).")
                if abs(mx) > 1e15:
                    report.warnings.append(f"Column '{col}' has extreme magnitude (max={mx}).")
                if vals.fillna(0).eq(0).all():
                    report.warnings.append(f"Column '{col}' is all zeros.")
    for col in df.columns:
        cl = col.lower()
        if any(k in cl for k in ("pct", "percent", "rate", "ratio")):
            s = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(s) and (s.max() > 100.5 or s.min() < -0.01):
                report.warnings.append(f"'{col}' looks like a rate/percent but has values outside 0–100.")
    if any(w in q for w in ("average", "total", "how many")) and len(df) > 500:
        report.warnings.append("Question looks aggregate-style but result has many rows — check GROUP BY / filters.")
    return report
