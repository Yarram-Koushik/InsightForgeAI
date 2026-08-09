"""
InsightForgeAI – Automated Visualization Engine (Phase 2.4)

Industry-grade, rule-based chart recommendation + Plotly generation.
No LLM required for chart selection (deterministic, free, fast, auditable).

Public API:
  recommend_and_build(df, question=None, preferred_type=None) -> VizResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Any, Dict, Tuple
import re

import pandas as pd
import numpy as np

try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    px = None  # type: ignore
    go = None  # type: ignore
    PLOTLY_AVAILABLE = False


SUPPORTED_CHARTS = ("bar", "line", "pie", "scatter", "histogram", "kpi", "table")


@dataclass
class ChartSpec:
    chart_type: str
    x: Optional[str] = None
    y: Optional[str] = None
    color: Optional[str] = None
    title: str = ""
    reason: str = ""
    top_n: Optional[int] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class VizResult:
    success: bool
    spec: Optional[ChartSpec] = None
    fig: Any = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None


def _is_datetime_series(s: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    if s.dtype == object:
        try:
            converted = pd.to_datetime(s.dropna().head(30), errors="coerce")
            return converted.notna().mean() > 0.8
        except Exception:
            return False
    return False


def _is_numeric_series(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s)


def _nunique_safe(s: pd.Series) -> int:
    try:
        return int(s.nunique(dropna=True))
    except Exception:
        return 0


def _classify_columns(df: pd.DataFrame) -> Dict[str, List[str]]:
    numeric, categorical, datetime_cols = [], [], []
    for col in df.columns:
        s = df[col]
        if _is_datetime_series(s):
            datetime_cols.append(col)
        elif _is_numeric_series(s):
            numeric.append(col)
        else:
            categorical.append(col)
    return {"numeric": numeric, "categorical": categorical, "datetime": datetime_cols}


_MAX_PIE_SLICES = 8
_MAX_BAR_CATEGORIES = 20
_TOP_N_DEFAULT = 15


def recommend_chart(
    df: pd.DataFrame,
    question: Optional[str] = None,
    preferred_type: Optional[str] = None,
) -> ChartSpec:
    q = (question or "").lower()

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return ChartSpec(chart_type="table", title="No data", reason="Empty result set")

    n_rows, n_cols = df.shape
    cols = _classify_columns(df)
    num, cat, dt = cols["numeric"], cols["categorical"], cols["datetime"]

    if n_rows == 1 and n_cols == 1:
        col = df.columns[0]
        return ChartSpec(chart_type="kpi", y=col, title=str(col), reason="Single value result – shown as KPI")

    forced = None
    if preferred_type and preferred_type in SUPPORTED_CHARTS:
        forced = preferred_type
    elif any(w in q for w in ("pie", "share", "proportion", "percentage breakdown")):
        forced = "pie"
    elif any(w in q for w in ("trend", "over time", "timeline", "line chart")):
        forced = "line"
    elif any(w in q for w in ("scatter", "correlation", "vs ")):
        forced = "scatter"
    elif any(w in q for w in ("histogram", "distribution")):
        forced = "histogram"
    elif any(w in q for w in ("bar chart", "bar graph")):
        forced = "bar"

    notes: List[str] = []

    if forced == "kpi":
        y = num[0] if num else df.columns[0]
        return ChartSpec(chart_type="kpi", y=y, title=str(y), reason="Forced KPI")

    if forced == "pie" and cat and num:
        x, y = cat[0], num[0]
        nunq = _nunique_safe(df[x])
        top_n = _MAX_PIE_SLICES if nunq > _MAX_PIE_SLICES else None
        if top_n:
            notes.append(f"Pie limited to top {top_n} categories")
        return ChartSpec(chart_type="pie", x=x, y=y, title=f"{y} by {x}", reason="Pie chart requested", top_n=top_n, notes=notes)

    if forced == "line" and (dt or cat) and num:
        x = dt[0] if dt else cat[0]
        y = num[0]
        return ChartSpec(chart_type="line", x=x, y=y, title=f"{y} over {x}", reason="Line chart requested", notes=notes)

    if forced == "scatter" and len(num) >= 2:
        return ChartSpec(chart_type="scatter", x=num[0], y=num[1], color=cat[0] if cat else None, title=f"{num[1]} vs {num[0]}", reason="Scatter requested")

    if forced == "histogram" and num:
        return ChartSpec(chart_type="histogram", x=num[0], title=f"Distribution of {num[0]}", reason="Histogram requested")

    if forced == "bar" and (cat or dt) and num:
        x = cat[0] if cat else dt[0]
        y = num[0]
        nunq = _nunique_safe(df[x])
        top_n = _TOP_N_DEFAULT if nunq > _MAX_BAR_CATEGORIES else None
        if top_n:
            notes.append(f"Showing top {top_n} of {nunq} categories")
        return ChartSpec(chart_type="bar", x=x, y=y, title=f"{y} by {x}", reason="Bar chart requested", top_n=top_n, notes=notes)

    if dt and num:
        return ChartSpec(chart_type="line", x=dt[0], y=num[0], title=f"{num[0]} over {dt[0]}", reason="Datetime + numeric → line chart")

    if cat and num:
        x, y = cat[0], num[0]
        nunq = _nunique_safe(df[x])
        if nunq <= _MAX_PIE_SLICES and any(w in q for w in ("share", "breakdown", "proportion", "percent", "%")):
            return ChartSpec(chart_type="pie", x=x, y=y, title=f"{y} by {x}", reason="Few categories + share language → pie")
        top_n = _TOP_N_DEFAULT if nunq > _MAX_BAR_CATEGORIES else None
        if top_n:
            notes.append(f"Showing top {top_n} of {nunq} categories by {y}")
        return ChartSpec(chart_type="bar", x=x, y=y, title=f"{y} by {x}", reason="Categorical + numeric → bar chart", top_n=top_n, notes=notes)

    if len(num) >= 2:
        return ChartSpec(chart_type="scatter", x=num[0], y=num[1], color=cat[0] if cat else None, title=f"{num[1]} vs {num[0]}", reason="Two numeric columns → scatter")

    if len(num) == 1 and n_rows >= 5 and not cat:
        return ChartSpec(chart_type="histogram", x=num[0], title=f"Distribution of {num[0]}", reason="Single numeric series → histogram")

    if cat and not num:
        x = cat[0]
        nunq = _nunique_safe(df[x])
        top_n = _TOP_N_DEFAULT if nunq > _MAX_BAR_CATEGORIES else None
        return ChartSpec(chart_type="bar", x=x, y="__count__", title=f"Count by {x}", reason="Categorical only → frequency bar", top_n=top_n, notes=notes)

    return ChartSpec(chart_type="table", title="Results", reason="No clear chart mapping – table is safest")


_TEMPLATE = "plotly_dark"


def _prepare_xy(df: pd.DataFrame, spec: ChartSpec) -> Tuple[pd.DataFrame, ChartSpec]:
    work = df.copy()
    spec = ChartSpec(**{**spec.__dict__})

    if spec.y == "__count__" and spec.x:
        vc = work[spec.x].value_counts(dropna=False).reset_index()
        vc.columns = [spec.x, "__count__"]
        work = vc
        spec.y = "__count__"

    if spec.top_n and spec.x and spec.y and spec.y in work.columns:
        work = work.sort_values(spec.y, ascending=False).head(spec.top_n)

    return work, spec


def build_figure(df: pd.DataFrame, spec: ChartSpec) -> Any:
    if not PLOTLY_AVAILABLE:
        raise RuntimeError("plotly is not installed. Run: pip install plotly")

    if spec.chart_type == "table":
        return None

    if spec.chart_type == "kpi":
        col = spec.y or df.columns[0]
        val = df.iloc[0][col]
        try:
            if isinstance(val, (int, float, np.integer, np.floating)) and not isinstance(val, bool):
                text = f"{val:,.2f}" if isinstance(val, float) else f"{int(val):,}"
            else:
                text = str(val)
        except Exception:
            text = str(val)
        fig = go.Figure(
            go.Indicator(
                mode="number",
                value=float(val) if isinstance(val, (int, float, np.integer, np.floating)) and not isinstance(val, bool) else 0,
                title={"text": spec.title or str(col)},
                number={"valueformat": ","} if isinstance(val, (int, float, np.integer, np.floating)) else {},
            )
        )
        if not isinstance(val, (int, float, np.integer, np.floating)) or isinstance(val, bool):
            fig = go.Figure()
            fig.add_annotation(
                text=f"<b>{text}</b>",
                x=0.5, y=0.5, showarrow=False,
                font=dict(size=36),
                xref="paper", yref="paper",
            )
            fig.update_layout(title=spec.title or str(col))
        fig.update_layout(template=_TEMPLATE, height=280, margin=dict(l=40, r=40, t=60, b=40))
        return fig

    work, spec = _prepare_xy(df, spec)

    if spec.chart_type == "bar":
        fig = px.bar(work, x=spec.x, y=spec.y, color=spec.color, title=spec.title, template=_TEMPLATE)
        fig.update_layout(xaxis_tickangle=-35, height=420, margin=dict(l=40, r=20, t=50, b=80))
        return fig

    if spec.chart_type == "line":
        if spec.x and spec.x in work.columns:
            try:
                work = work.copy()
                work[spec.x] = pd.to_datetime(work[spec.x], errors="ignore")
                work = work.sort_values(spec.x)
            except Exception:
                pass
        fig = px.line(work, x=spec.x, y=spec.y, color=spec.color, title=spec.title, template=_TEMPLATE, markers=True)
        fig.update_layout(height=420, margin=dict(l=40, r=20, t=50, b=60))
        return fig

    if spec.chart_type == "pie":
        fig = px.pie(work, names=spec.x, values=spec.y, title=spec.title, template=_TEMPLATE)
        fig.update_layout(height=420, margin=dict(l=20, r=20, t=50, b=20))
        return fig

    if spec.chart_type == "scatter":
        fig = px.scatter(work, x=spec.x, y=spec.y, color=spec.color, title=spec.title, template=_TEMPLATE)
        fig.update_layout(height=420, margin=dict(l=40, r=20, t=50, b=60))
        return fig

    if spec.chart_type == "histogram":
        fig = px.histogram(work, x=spec.x, title=spec.title, template=_TEMPLATE)
        fig.update_layout(height=420, margin=dict(l=40, r=20, t=50, b=60))
        return fig

    return None


def recommend_and_build(
    df: Optional[pd.DataFrame],
    question: Optional[str] = None,
    preferred_type: Optional[str] = None,
) -> VizResult:
    if not PLOTLY_AVAILABLE:
        return VizResult(success=False, error="plotly is not installed. Run: pip install plotly", skipped_reason="missing_plotly")

    if df is None or not isinstance(df, pd.DataFrame):
        return VizResult(success=False, skipped_reason="no_dataframe", error="No data to visualize")

    if df.empty:
        return VizResult(success=False, skipped_reason="empty", error="Result has 0 rows – nothing to chart")

    if df.shape[1] > 30:
        return VizResult(success=False, skipped_reason="too_wide", error="Result has too many columns for automatic charting. Use the table view.", warnings=["Skipped chart: >30 columns"])

    try:
        spec = recommend_chart(df, question=question, preferred_type=preferred_type)
    except Exception as e:
        return VizResult(success=False, error=f"Chart recommendation failed: {e}")

    if spec.chart_type == "table":
        return VizResult(success=False, spec=spec, skipped_reason="table_only", warnings=spec.notes + ["No suitable chart – showing table only"])

    try:
        fig = build_figure(df, spec)
        return VizResult(success=True, spec=spec, fig=fig, warnings=list(spec.notes))
    except Exception as e:
        return VizResult(success=False, spec=spec, error=f"Chart build failed: {e}", warnings=list(spec.notes))
