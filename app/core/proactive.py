"""
InsightForgeAI – Proactive Insights (Phase 4.6)

Scan a loaded dataset for unusual patterns vs a recent baseline and surface
compact cards the UI / orchestrator can show.

Deterministic first: 7-period sum change + residual z-score outliers.
Never raises; always returns structured cards (may be empty).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class ProactiveCard:
    id: str
    title: str
    severity: str  # info | watch | alert
    metric: Optional[str] = None
    table_name: Optional[str] = None
    summary: str = ""
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    suggested_question: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _is_datetime_series(s: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    if s.dtype == object or str(s.dtype).startswith("string"):
        try:
            converted = pd.to_datetime(s.dropna().head(40), errors="coerce")
            return float(converted.notna().mean()) > 0.8
        except Exception:
            return False
    return False


def _is_numeric_series(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s)


def _detect_time_value(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    if df is None or df.empty:
        return None, None
    time_cols, num_cols = [], []
    for c in df.columns:
        if _is_datetime_series(df[c]):
            time_cols.append(c)
        elif _is_numeric_series(df[c]):
            num_cols.append(c)
    if not time_cols or not num_cols:
        return None, None
    time_col = max(time_cols, key=lambda c: df[c].nunique(dropna=True))
    priority = ("amount", "revenue", "sales", "value", "total", "qty", "quantity", "count")
    value_col = num_cols[0]
    for p in priority:
        for c in num_cols:
            if p in str(c).lower():
                return time_col, c
    return time_col, value_col


def _prepare_daily(df: pd.DataFrame, time_col: str, value_col: str) -> pd.DataFrame:
    work = df[[time_col, value_col]].copy()
    work.columns = ["ds", "y"]
    work["ds"] = pd.to_datetime(work["ds"], errors="coerce")
    work["y"] = pd.to_numeric(work["y"], errors="coerce")
    work = work.dropna(subset=["ds", "y"]).sort_values("ds")
    work["ds"] = work["ds"].dt.floor("D")
    return work.groupby("ds", as_index=False)["y"].sum()


def _rel_change(recent: float, baseline: float) -> Optional[float]:
    if baseline is None or (isinstance(baseline, float) and math.isnan(baseline)):
        return None
    if abs(baseline) < 1e-9:
        return 0.0 if abs(recent) < 1e-9 else None
    return (recent - baseline) / abs(baseline)


def _z_outliers(series: pd.Series, z_thresh: float = 2.5) -> List[Dict[str, Any]]:
    y = series.astype(float).values
    if len(y) < 8:
        return []
    x = np.arange(len(y), dtype=float)
    coef = np.polyfit(x, y, 1)
    resid = y - (coef[0] * x + coef[1])
    std = float(np.std(resid))
    if std < 1e-12:
        return []
    flags = []
    for i, zi in enumerate(resid / std):
        if abs(zi) >= z_thresh:
            flags.append({"index": int(i), "y": float(y[i]), "z_score": round(float(zi), 2)})
    return flags[:8]


def scan_dataframe(
    df: Optional[pd.DataFrame],
    *,
    table_name: Optional[str] = None,
    metric_name: Optional[str] = None,
    window: int = 7,
    change_alert_pct: float = 0.25,
    change_watch_pct: float = 0.12,
) -> List[ProactiveCard]:
    cards: List[ProactiveCard] = []
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return cards

    window = max(2, min(int(window), 60))
    time_col, value_col = _detect_time_value(df)
    metric = metric_name or value_col or "value"

    if not time_col or not value_col:
        nums = [c for c in df.columns if _is_numeric_series(df[c])]
        if not nums:
            return cards
        col = nums[0]
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(s) < 5:
            return cards
        mean, std = float(s.mean()), float(s.std())
        if std > 0 and (s.max() - mean) / std >= 3.0:
            cards.append(
                ProactiveCard(
                    id=f"stat_{col}",
                    title=f"High outlier in {col}",
                    severity="watch",
                    metric=col,
                    table_name=table_name,
                    summary=f"Max {s.max():.4g} is >=3 sigma above mean ({mean:.4g}).",
                    detail=f"n={len(s)}, mean={mean:.4g}, std={std:.4g}, max={s.max():.4g}",
                    evidence={"column": col, "mean": mean, "std": std, "max": float(s.max())},
                    suggested_question=f"show top 10 rows by {col}",
                )
            )
        return cards

    try:
        series = _prepare_daily(df, time_col, value_col)
    except Exception:
        return cards

    if len(series) < window * 2:
        if len(series) >= 4:
            recent = float(series["y"].iloc[-1])
            prior = float(series["y"].iloc[:-1].mean())
            rc = _rel_change(recent, prior)
            if rc is not None and abs(rc) >= change_watch_pct:
                sev = "alert" if abs(rc) >= change_alert_pct else "watch"
                direction = "up" if rc > 0 else "down"
                cards.append(
                    ProactiveCard(
                        id=f"trend_{value_col}",
                        title=f"{metric} moved {direction} vs prior average",
                        severity=sev,
                        metric=metric,
                        table_name=table_name,
                        summary=f"Latest {recent:.4g} vs prior avg {prior:.4g} ({rc*100:+.1f}%). Limited history.",
                        detail=f"time_col={time_col}, value_col={value_col}, points={len(series)}",
                        evidence={"latest": recent, "baseline_mean": prior, "rel_change": round(rc, 4), "points": len(series)},
                        suggested_question=f"why did {metric} change over time",
                    )
                )
        return cards

    recent_df = series.iloc[-window:]
    baseline_df = series.iloc[-2 * window : -window]
    recent_sum = float(recent_df["y"].sum())
    baseline_sum = float(baseline_df["y"].sum())
    recent_mean = float(recent_df["y"].mean())
    baseline_mean = float(baseline_df["y"].mean())
    rc = _rel_change(recent_sum, baseline_sum)

    if rc is not None and abs(rc) >= change_watch_pct:
        sev = "alert" if abs(rc) >= change_alert_pct else "watch"
        direction = "up" if rc > 0 else "down"
        cards.append(
            ProactiveCard(
                id=f"pop_{value_col}",
                title=f"{metric} {direction} vs prior {window}-period baseline",
                severity=sev,
                metric=metric,
                table_name=table_name,
                summary=(
                    f"Last {window} periods sum={recent_sum:.4g} vs prior {window} sum={baseline_sum:.4g} "
                    f"({rc*100:+.1f}%)."
                ),
                detail=f"Recent mean={recent_mean:.4g}, baseline mean={baseline_mean:.4g}. time={time_col}, measure={value_col}.",
                evidence={
                    "window": window,
                    "recent_sum": recent_sum,
                    "baseline_sum": baseline_sum,
                    "rel_change": round(rc, 4),
                    "time_col": time_col,
                    "value_col": value_col,
                    "recent_start": str(recent_df["ds"].iloc[0]),
                    "recent_end": str(recent_df["ds"].iloc[-1]),
                },
                suggested_question=f"why did {metric} change by region",
            )
        )

    flags = _z_outliers(series["y"], z_thresh=2.5)
    if flags:
        for f in flags:
            f["ds"] = str(series.iloc[f["index"]]["ds"])
        top = flags[0]
        cards.append(
            ProactiveCard(
                id=f"anom_{value_col}",
                title=f"Anomaly points on {metric}",
                severity="watch" if len(flags) < 3 else "alert",
                metric=metric,
                table_name=table_name,
                summary=f"Detected {len(flags)} residual outlier(s) (z >= 2.5). Strongest at {top.get('ds')} (z={top['z_score']}).",
                detail=f"measure={value_col}, time={time_col}",
                evidence={"anomalies": flags, "value_col": value_col, "time_col": time_col},
                suggested_question=f"show anomalies for {metric} over time",
            )
        )
    return cards


def scan_workspace_table(
    workspace: Any,
    table_name: str,
    *,
    metric_name: Optional[str] = None,
    window: int = 7,
) -> List[ProactiveCard]:
    try:
        if workspace is None or not table_name:
            return []
        rec = workspace.get(table_name) if hasattr(workspace, "get") else None
        if rec is None:
            return []
        df = getattr(rec, "cleaned_df", None) or getattr(rec, "raw_df", None)
        return scan_dataframe(df, table_name=table_name, metric_name=metric_name, window=window)
    except Exception:
        return []


def cards_to_message(cards: List[ProactiveCard]) -> str:
    if not cards:
        return "No unusual patterns detected against the recent baseline."
    lines = ["**Proactive insights**"]
    for c in cards:
        badge = {"alert": "🔴", "watch": "🟡", "info": "🔵"}.get(c.severity, "•")
        lines.append(f"{badge} **{c.title}** — {c.summary}")
        if c.suggested_question:
            lines.append(f"   ↳ Try: _{c.suggested_question}_")
    return "\n".join(lines)


__all__ = ["ProactiveCard", "scan_dataframe", "scan_workspace_table", "cards_to_message"]
