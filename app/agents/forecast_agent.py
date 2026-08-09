"""Forecast Agent – Phase 2.7 metric series + backtest."""
from __future__ import annotations
import sys
from pathlib import Path
_AGENTS_DIR = Path(__file__).resolve().parent
_CORE_DIR = _AGENTS_DIR.parent / "core"
_ROOT = _AGENTS_DIR.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import importlib.util

def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_state = _load("agent_state", _AGENTS_DIR / "state.py")
AgentState = _state.AgentState
_analytics = _load("analytics_engine", _CORE_DIR / "analytics.py")
run_forecast = _analytics.run_forecast
parse_horizon_from_question = _analytics.parse_horizon_from_question

def run(state: AgentState) -> AgentState:
    state.steps.append("forecast_agent:start")
    df = state.result_df
    try:
        if state.workspace and state.table_name:
            rec = state.workspace.get(state.table_name)
            if rec is not None and getattr(rec, "cleaned_df", None) is not None:
                full = rec.cleaned_df
                from_result = df is not None and not getattr(df, "empty", True)
                need_full = True
                if from_result:
                    try:
                        import pandas as pd
                        has_dt = any(
                            pd.api.types.is_datetime64_any_dtype(df[c]) or
                            (df[c].dtype == object and pd.to_datetime(df[c].dropna().head(20), errors="coerce").notna().mean() > 0.8)
                            for c in df.columns
                        )
                        need_full = (not has_dt) or len(df) < 5
                    except Exception:
                        need_full = True
                if need_full:
                    df = full
                    state.warnings.append("Forecast series built from full cleaned table (aggregated by date).")
    except Exception:
        pass
    if df is None or (hasattr(df, "empty") and df.empty):
        state.forecast_success = False
        state.forecast_error = "No data available for forecasting."
        state.steps.append("forecast_agent:no_data")
        return state
    periods, _unit = parse_horizon_from_question(state.question or "", default=14)
    if isinstance(periods, tuple):
        periods, _unit = periods
    result = run_forecast(df, periods=periods if isinstance(periods, int) else 14, question=state.question or "")
    if result.warnings:
        state.warnings.extend(result.warnings)
    if not result.success:
        state.forecast_success = False
        state.forecast_error = result.error
        state.steps.append(f"forecast_agent:skip:{result.skipped_reason or 'error'}")
        return state
    state.forecast_success = True
    state.forecast_df = result.forecast_df
    state.forecast_fig = result.fig
    state.forecast_method = result.method
    state.forecast_horizon = result.horizon
    state.trend_summary = result.trend_summary
    state.anomalies = result.anomalies
    if result.fig is not None:
        state.chart_fig = result.fig
        state.chart_type = "forecast"
        state.chart_reason = f"{result.method} · metric={result.metric} · horizon={result.horizon} · freq={result.freq}"
    lines = [result.trend_summary or ""]
    if result.backtest:
        bt = result.backtest
        lines.append(f"Holdout backtest (baseline): MAE={bt.get('mae')}, RMSE={bt.get('rmse')}, MAPE={bt.get('mape_pct')}% on {bt.get('holdout_points')} points.")
    if result.anomalies:
        lines.append(f"Detected {len(result.anomalies)} potential anomaly point(s) (robust z ≥ 2.5).")
    lines.append(f"Method: **{result.method}** · metric **{result.metric}** · next **{result.horizon}** period(s).")
    state.insight_text = "\n\n".join([x for x in lines if x])
    state.steps.append(f"forecast_agent:ok:{result.method}:m={result.metric}:h={result.horizon}")
    return state
