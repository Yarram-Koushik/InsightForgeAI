"""
InsightForgeAI – One-click EDA Pack (Phase 4.3)

Produces a durable analyst artifact from any loaded dataset:
  - shape + quality score
  - column profile
  - key correlations
  - recommended charts (Plotly when available)
  - short narrative summary

Deterministic first; LLM narrative is optional and never required.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import plotly.express as px
    import plotly.graph_objects as go
    PLOTLY = True
except Exception:
    px = None  # type: ignore
    go = None  # type: ignore
    PLOTLY = False


@dataclass
class EDAPack:
    success: bool
    table_name: str
    rows: int = 0
    columns: int = 0
    quality_score: Optional[float] = None
    quality: Dict[str, Any] = field(default_factory=dict)
    column_profile: Optional[pd.DataFrame] = None
    correlations: List[Dict[str, Any]] = field(default_factory=list)
    numeric_summary: Optional[pd.DataFrame] = None
    categorical_summary: List[Dict[str, Any]] = field(default_factory=list)
    charts: List[Dict[str, Any]] = field(default_factory=list)  # {title, fig, reason}
    narrative: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_public_dict(self) -> Dict[str, Any]:
        d = {
            "success": self.success,
            "table_name": self.table_name,
            "rows": self.rows,
            "columns": self.columns,
            "quality_score": self.quality_score,
            "quality": self.quality,
            "correlations": self.correlations,
            "categorical_summary": self.categorical_summary,
            "narrative": self.narrative,
            "warnings": self.warnings,
            "error": self.error,
            "chart_titles": [c.get("title") for c in self.charts],
        }
        return d


def _numeric_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _cat_cols(df: pd.DataFrame, max_card: int = 50) -> List[str]:
    out = []
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            continue
        nuniq = df[c].nunique(dropna=True)
        if 1 < nuniq <= max_card:
            out.append(c)
    return out


def _datetime_cols(df: pd.DataFrame) -> List[str]:
    cols = []
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            cols.append(c)
            continue
        if df[c].dtype == object:
            try:
                sample = df[c].dropna().head(30)
                if len(sample) and pd.to_datetime(sample, errors="coerce").notna().mean() > 0.8:
                    cols.append(c)
            except Exception:
                pass
    return cols


def _top_correlations(df: pd.DataFrame, top_n: int = 8) -> List[Dict[str, Any]]:
    nums = _numeric_cols(df)
    if len(nums) < 2:
        return []
    corr = df[nums].corr(numeric_only=True)
    pairs = []
    for i, a in enumerate(nums):
        for b in nums[i + 1 :]:
            v = corr.loc[a, b]
            if pd.isna(v):
                continue
            pairs.append({"col_a": a, "col_b": b, "r": round(float(v), 4), "abs_r": abs(float(v))})
    pairs.sort(key=lambda x: x["abs_r"], reverse=True)
    return pairs[:top_n]


def _build_charts(df: pd.DataFrame, max_charts: int = 4) -> List[Dict[str, Any]]:
    if not PLOTLY or df is None or df.empty:
        return []
    charts: List[Dict[str, Any]] = []
    nums = _numeric_cols(df)
    cats = _cat_cols(df, max_card=20)
    times = _datetime_cols(df)

    # 1) Distribution of primary numeric
    if nums:
        col = nums[0]
        try:
            fig = px.histogram(df, x=col, nbins=30, title=f"Distribution of {col}", template="plotly_dark")
            fig.update_layout(height=360, margin=dict(l=40, r=20, t=50, b=40))
            charts.append({"title": f"Distribution · {col}", "fig": fig, "reason": "Primary numeric distribution"})
        except Exception:
            pass

    # 2) Top categories by count
    if cats:
        col = cats[0]
        try:
            vc = df[col].astype(str).value_counts().head(12).reset_index()
            vc.columns = [col, "count"]
            fig = px.bar(vc, x=col, y="count", title=f"Top values of {col}", template="plotly_dark")
            fig.update_layout(height=360, margin=dict(l=40, r=20, t=50, b=40))
            charts.append({"title": f"Categories · {col}", "fig": fig, "reason": "Highest-cardinality useful categorical"})
        except Exception:
            pass

    # 3) Numeric by category
    if nums and cats:
        try:
            fig = px.box(df, x=cats[0], y=nums[0], title=f"{nums[0]} by {cats[0]}", template="plotly_dark")
            fig.update_layout(height=360, margin=dict(l=40, r=20, t=50, b=40))
            charts.append({"title": f"{nums[0]} by {cats[0]}", "fig": fig, "reason": "Segment comparison"})
        except Exception:
            pass

    # 4) Time series if available
    if times and nums:
        try:
            tcol, vcol = times[0], nums[0]
            work = df[[tcol, vcol]].copy()
            work[tcol] = pd.to_datetime(work[tcol], errors="coerce")
            work = work.dropna().sort_values(tcol)
            work = work.groupby(tcol, as_index=False)[vcol].sum()
            if len(work) >= 3:
                fig = px.line(work, x=tcol, y=vcol, title=f"{vcol} over time", template="plotly_dark")
                fig.update_layout(height=360, margin=dict(l=40, r=20, t=50, b=40))
                charts.append({"title": f"Trend · {vcol}", "fig": fig, "reason": "Time series view"})
        except Exception:
            pass

    # 5) Correlation heatmap if enough numerics
    if len(nums) >= 3 and len(charts) < max_charts:
        try:
            corr = df[nums[:12]].corr(numeric_only=True)
            fig = px.imshow(corr, text_auto=".2f", aspect="auto", title="Correlation heatmap", template="plotly_dark", color_continuous_scale="RdBu_r")
            fig.update_layout(height=420, margin=dict(l=40, r=20, t=50, b=40))
            charts.append({"title": "Correlation heatmap", "fig": fig, "reason": "Numeric relationships"})
        except Exception:
            pass

    return charts[:max_charts]


def _narrative(df: pd.DataFrame, quality: Dict[str, Any], corr: List[Dict[str, Any]]) -> List[str]:
    lines = []
    lines.append(f"Dataset has **{len(df):,}** rows × **{len(df.columns)}** columns.")
    if quality:
        score = quality.get("overall_score")
        if score is not None:
            band = "strong" if score >= 85 else ("acceptable" if score >= 70 else "weak")
            lines.append(f"Data quality score is **{score}/100** ({band}).")
    nums = _numeric_cols(df)
    cats = _cat_cols(df)
    if nums:
        lines.append(f"Numeric measures detected: {', '.join(f'`{c}`' for c in nums[:6])}{'…' if len(nums) > 6 else ''}.")
    if cats:
        lines.append(f"Useful categorical dimensions: {', '.join(f'`{c}`' for c in cats[:5])}{'…' if len(cats) > 5 else ''}.")
    if corr:
        top = corr[0]
        lines.append(
            f"Strongest correlation: `{top['col_a']}` vs `{top['col_b']}` (r={top['r']})."
        )
    miss = df.isna().mean()
    high_miss = [c for c, v in miss.items() if v > 0.3]
    if high_miss:
        lines.append(f"High missingness (>30%): {', '.join(f'`{c}`' for c in high_miss[:5])}.")
    return lines


def build_eda_pack(df: pd.DataFrame, table_name: str = "dataset") -> EDAPack:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return EDAPack(success=False, table_name=table_name, error="No data available for EDA.")

    pack = EDAPack(success=True, table_name=table_name, rows=len(df), columns=len(df.columns))

    # Quality
    try:
        from app.core.profiling import generate_quality_report, column_level_profile  # type: ignore

        pack.quality = generate_quality_report(df)
        pack.quality_score = pack.quality.get("overall_score")
        pack.column_profile = column_level_profile(df)
    except Exception as e:
        pack.warnings.append(f"Quality module unavailable: {e}")
        try:
            # Fallback minimal profile
            pack.column_profile = pd.DataFrame(
                [
                    {
                        "Column": c,
                        "Data Type": str(df[c].dtype),
                        "Missing %": round(float(df[c].isna().mean() * 100), 2),
                        "Unique Values": int(df[c].nunique()),
                        "Sample Values": str(df[c].dropna().head(3).tolist())[:60],
                    }
                    for c in df.columns
                ]
            )
        except Exception:
            pass

    # Numeric describe
    try:
        nums = _numeric_cols(df)
        if nums:
            pack.numeric_summary = df[nums].describe().T.reset_index().rename(columns={"index": "column"})
    except Exception as e:
        pack.warnings.append(f"Numeric summary skipped: {e}")

    # Categorical tops
    try:
        for c in _cat_cols(df, max_card=30)[:6]:
            vc = df[c].astype(str).value_counts().head(8)
            pack.categorical_summary.append(
                {"column": c, "top_values": [{"value": str(i), "count": int(v)} for i, v in vc.items()]}
            )
    except Exception as e:
        pack.warnings.append(f"Categorical summary skipped: {e}")

    # Correlations
    try:
        pack.correlations = _top_correlations(df)
    except Exception as e:
        pack.warnings.append(f"Correlations skipped: {e}")

    # Charts
    try:
        pack.charts = _build_charts(df)
        if not pack.charts:
            pack.warnings.append("No charts generated (need numeric/categorical columns or Plotly).")
    except Exception as e:
        pack.warnings.append(f"Charts skipped: {e}")

    pack.narrative = _narrative(df, pack.quality, pack.correlations)
    return pack


__all__ = ["EDAPack", "build_eda_pack"]
