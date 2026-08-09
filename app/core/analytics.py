"""
InsightForgeAI – Forecasting & Advanced Analytics (Phase 2.5)

- Time-series forecast (Prophet if installed, else robust baseline)
- Trend summary, anomaly flags, correlation helper
- Never crashes if Prophet is missing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Any, Dict, Tuple
import warnings
import re

import numpy as np
import pandas as pd

try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except Exception:
    Prophet = None  # type: ignore
    PROPHET_AVAILABLE = False

try:
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except Exception:
    go = None  # type: ignore
    PLOTLY_AVAILABLE = False


@dataclass
class ForecastResult:
    success: bool
    method: str = ""
    history_df: Optional[pd.DataFrame] = None
    forecast_df: Optional[pd.DataFrame] = None
    combined_df: Optional[pd.DataFrame] = None
    fig: Any = None
    horizon: int = 0
    freq: Optional[str] = None
    trend_direction: Optional[str] = None
    trend_summary: Optional[str] = None
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    skipped_reason: Optional[str] = None


@dataclass
class CorrelationResult:
    success: bool
    col_a: Optional[str] = None
    col_b: Optional[str] = None
    coefficient: Optional[float] = None
    n_pairs: int = 0
    interpretation: Optional[str] = None
    error: Optional[str] = None


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


def detect_time_value_columns(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
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
    value_col = num_cols[0]
    return time_col, value_col


def _infer_freq(ds: pd.Series) -> str:
    s = pd.to_datetime(ds, errors="coerce").dropna().sort_values()
    if len(s) < 3:
        return "D"
    try:
        inferred = pd.infer_freq(s)
        if inferred:
            return inferred
    except Exception:
        pass
    deltas = s.diff().dropna().dt.total_seconds()
    if deltas.empty:
        return "D"
    med = float(deltas.median())
    if med >= 28 * 86400:
        return "MS"
    if med >= 6 * 86400:
        return "W"
    if med >= 0.9 * 86400:
        return "D"
    return "D"


def _prepare_history(df: pd.DataFrame, time_col: str, value_col: str) -> pd.DataFrame:
    work = df[[time_col, value_col]].copy()
    work.columns = ["ds", "y"]
    work["ds"] = pd.to_datetime(work["ds"], errors="coerce")
    work["y"] = pd.to_numeric(work["y"], errors="coerce")
    work = work.dropna(subset=["ds", "y"]).sort_values("ds")
    work = work.groupby("ds", as_index=False)["y"].sum()
    return work


def _trend_summary(history: pd.DataFrame) -> Tuple[str, str]:
    if history is None or len(history) < 3:
        return "unknown", "Not enough points to estimate trend."
    x = np.arange(len(history), dtype=float)
    y = history["y"].astype(float).values
    if np.allclose(y, y[0]):
        return "flat", "Series is essentially flat (little variation)."
    coef = np.polyfit(x, y, 1)
    slope = float(coef[0])
    scale = max(abs(float(np.mean(y))), 1e-9)
    rel = slope / scale * len(history)
    if abs(rel) < 0.05:
        direction = "flat"
    elif slope > 0:
        direction = "up"
    else:
        direction = "down"
    summary = f"Trend is **{direction}** (approx. relative change over the window: {rel*100:.1f}%)."
    return direction, summary


def _detect_anomalies(history: pd.DataFrame, z_thresh: float = 2.5) -> List[Dict[str, Any]]:
    if history is None or len(history) < 8:
        return []
    x = np.arange(len(history), dtype=float)
    y = history["y"].astype(float).values
    coef = np.polyfit(x, y, 1)
    resid = y - (coef[0] * x + coef[1])
    std = float(np.std(resid))
    if std < 1e-12:
        return []
    z = resid / std
    flags = []
    for i, zi in enumerate(z):
        if abs(zi) >= z_thresh:
            flags.append({"ds": str(history.iloc[i]["ds"]), "y": float(y[i]), "z_score": round(float(zi), 2)})
    return flags[:10]


def _forecast_prophet(history: pd.DataFrame, periods: int, freq: str) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = Prophet(yearly_seasonality="auto", weekly_seasonality="auto", daily_seasonality=False, interval_width=0.8)
        m.fit(history)
        future = m.make_future_dataframe(periods=periods, freq=freq)
        fc = m.predict(future)
    return fc[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()


def _forecast_baseline(history: pd.DataFrame, periods: int, freq: str) -> pd.DataFrame:
    hist = history.copy()
    hist["ds"] = pd.to_datetime(hist["ds"])
    hist = hist.sort_values("ds")
    x = np.arange(len(hist), dtype=float)
    y = hist["y"].astype(float).values
    coef = np.polyfit(x, y, 1)
    resid = y - (coef[0] * x + coef[1])
    last_ds = hist["ds"].iloc[-1]
    try:
        future_ds = pd.date_range(start=last_ds, periods=periods + 1, freq=freq)[1:]
    except Exception:
        future_ds = pd.date_range(start=last_ds, periods=periods + 1, freq="D")[1:]
    hist_yhat = coef[0] * x + coef[1]
    std = float(np.std(resid)) if len(resid) > 1 else 0.0
    std = max(std, abs(float(np.mean(y))) * 0.05, 1e-6)
    rows = []
    for i, ds in enumerate(hist["ds"]):
        yh = float(hist_yhat[i])
        rows.append({"ds": ds, "yhat": yh, "yhat_lower": yh - 1.28 * std, "yhat_upper": yh + 1.28 * std})
    for j, ds in enumerate(future_ds):
        xi = len(hist) + j
        yh = float(coef[0] * xi + coef[1])
        widen = 1.0 + 0.05 * (j + 1)
        rows.append({"ds": ds, "yhat": yh, "yhat_lower": yh - 1.28 * std * widen, "yhat_upper": yh + 1.28 * std * widen})
    return pd.DataFrame(rows)


def _build_forecast_fig(history: pd.DataFrame, forecast: pd.DataFrame, title: str) -> Any:
    if not PLOTLY_AVAILABLE:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=history["ds"], y=history["y"], mode="lines+markers", name="Actual", line=dict(color="#636EFA")))
    fig.add_trace(go.Scatter(x=forecast["ds"], y=forecast["yhat"], mode="lines", name="Forecast", line=dict(color="#EF553B", dash="dash")))
    last_hist = history["ds"].max()
    fut = forecast[forecast["ds"] > last_hist]
    if not fut.empty and "yhat_lower" in fut.columns:
        fig.add_trace(go.Scatter(
            x=list(fut["ds"]) + list(fut["ds"][::-1]),
            y=list(fut["yhat_upper"]) + list(fut["yhat_lower"][::-1]),
            fill="toself", fillcolor="rgba(239, 85, 59, 0.15)",
            line=dict(color="rgba(255,255,255,0)"), name="80% interval", hoverinfo="skip",
        ))
    fig.update_layout(title=title, template="plotly_dark", height=440, margin=dict(l=40, r=20, t=50, b=40),
                      xaxis_title="Date", yaxis_title="Value",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig


def run_forecast(
    df: Optional[pd.DataFrame],
    periods: int = 14,
    time_col: Optional[str] = None,
    value_col: Optional[str] = None,
    prefer_prophet: bool = True,
) -> ForecastResult:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return ForecastResult(success=False, skipped_reason="empty", error="No data available for forecasting.")

    periods = int(max(1, min(periods, 365)))

    if not time_col or not value_col:
        time_col, value_col = detect_time_value_columns(df)
    if not time_col or not value_col:
        return ForecastResult(
            success=False,
            skipped_reason="no_time_series",
            error=(
                "Forecasting needs a date/time column and a numeric measure. "
                "Try asking for counts over time first, then forecast."
            ),
        )

    try:
        history = _prepare_history(df, time_col, value_col)
    except Exception as e:
        return ForecastResult(success=False, error=f"Failed to prepare time series: {e}")

    if len(history) < 5:
        return ForecastResult(
            success=False,
            skipped_reason="insufficient_history",
            error=f"Need at least 5 time points to forecast (found {len(history)}).",
            history_df=history,
        )

    freq = _infer_freq(history["ds"])
    direction, trend_txt = _trend_summary(history)
    anomalies = _detect_anomalies(history)

    method = "baseline"
    warn: List[str] = []
    try:
        if prefer_prophet and PROPHET_AVAILABLE and len(history) >= 10:
            forecast = _forecast_prophet(history, periods=periods, freq=freq)
            method = "prophet"
        else:
            if prefer_prophet and not PROPHET_AVAILABLE:
                warn.append("Prophet not installed – using baseline forecast (pip install prophet for richer seasonality).")
            forecast = _forecast_baseline(history, periods=periods, freq=freq)
            method = "baseline"
    except Exception as e:
        try:
            forecast = _forecast_baseline(history, periods=periods, freq=freq)
            method = "baseline"
            warn.append(f"Primary forecast failed ({e}); used baseline.")
        except Exception as e2:
            return ForecastResult(success=False, error=f"Forecast failed: {e2}", history_df=history)

    last_ds = history["ds"].max()
    future_only = forecast[forecast["ds"] > last_ds].copy()
    combined = history.merge(forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]], on="ds", how="outer").sort_values("ds")
    title = f"Forecast of {value_col} ({method}, next {periods} periods)"
    fig = _build_forecast_fig(history, forecast, title=title)

    return ForecastResult(
        success=True,
        method=method,
        history_df=history,
        forecast_df=future_only.reset_index(drop=True),
        combined_df=combined.reset_index(drop=True),
        fig=fig,
        horizon=periods,
        freq=freq,
        trend_direction=direction,
        trend_summary=trend_txt,
        anomalies=anomalies,
        warnings=warn,
    )


def run_correlation(df: Optional[pd.DataFrame], col_a: Optional[str] = None, col_b: Optional[str] = None) -> CorrelationResult:
    if df is None or df.empty:
        return CorrelationResult(success=False, error="No data for correlation.")
    nums = [c for c in df.columns if _is_numeric_series(df[c])]
    if col_a and col_b:
        a, b = col_a, col_b
    elif len(nums) >= 2:
        a, b = nums[0], nums[1]
    else:
        return CorrelationResult(success=False, error="Need at least two numeric columns for correlation.")
    pair = df[[a, b]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(pair) < 5:
        return CorrelationResult(success=False, error="Not enough paired numeric rows for correlation.", col_a=a, col_b=b, n_pairs=len(pair))
    coef = float(pair[a].corr(pair[b]))
    if abs(coef) >= 0.7:
        interp = "strong"
    elif abs(coef) >= 0.4:
        interp = "moderate"
    elif abs(coef) >= 0.2:
        interp = "weak"
    else:
        interp = "little to no"
    direction = "positive" if coef >= 0 else "negative"
    return CorrelationResult(
        success=True, col_a=a, col_b=b, coefficient=round(coef, 4), n_pairs=len(pair),
        interpretation=f"{interp} {direction} correlation between {a} and {b} (r={coef:.3f}, n={len(pair)}).",
    )


def parse_horizon_from_question(question: str, default: int = 14) -> int:
    q = (question or "").lower()
    m = re.search(r"next\s+(\d+)\s*(day|days|week|weeks|month|months)", q)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("week"):
            return max(1, min(n * 7, 365))
        if unit.startswith("month"):
            return max(1, min(n * 30, 365))
        return max(1, min(n, 365))
    if "next month" in q:
        return 30
    if "next week" in q:
        return 7
    return default
