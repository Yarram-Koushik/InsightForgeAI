"""
Analytics Agent (Phase 4.3)

Dispatches specialized analyst paths:
  - EDA pack
  - Root-cause breakdown
  - What-if / scenario
  - RFM / cohorts

Does not replace SQL agent; used when the question clearly asks for these paths.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

_AGENTS_DIR = Path(__file__).resolve().parent
_CORE_DIR = _AGENTS_DIR.parent / "core"
_ROOT = _AGENTS_DIR.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import importlib.util


def _load(name: str, path: Path, required_attrs: tuple = ()) -> Any:
    if name in sys.modules:
        mod = sys.modules[name]
        if required_attrs and not all(hasattr(mod, a) for a in required_attrs):
            del sys.modules[name]
        else:
            return mod
    # Prefer package import
    try:
        if name.startswith("app.core."):
            mod = __import__(name, fromlist=["*"])
            sys.modules[name] = mod
            return mod
    except Exception:
        pass
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(name)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


_state = _load("agent_state", _AGENTS_DIR / "state.py", required_attrs=("AgentState",))
AgentState = _state.AgentState


def _df_from_state(state: AgentState):
    if state.workspace is None or not state.table_name:
        return None
    rec = state.workspace.get(state.table_name)
    if rec is None:
        return None
    return getattr(rec, "cleaned_df", None) or getattr(rec, "raw_df", None)


def run_eda(state: AgentState) -> AgentState:
    state.steps.append("analytics_agent:eda:start")
    df = _df_from_state(state)
    try:
        eda = _load("eda_pack", _CORE_DIR / "eda_pack.py", required_attrs=("build_eda_pack",))
        pack = eda.build_eda_pack(df, table_name=state.table_name or "dataset")
    except Exception as e:
        state.sql_success = False
        state.error = f"EDA pack failed: {e}"
        state.steps.append("analytics_agent:eda:fail")
        return state

    state.sql_success = bool(pack.success)
    state.eda_pack = pack
    state.insight_text = "\n".join(f"- {b}" for b in (pack.narrative or [])) or "EDA complete."
    if pack.charts:
        # Primary chart for the UI
        state.chart_fig = pack.charts[0].get("fig")
        state.chart_type = "eda"
        state.chart_reason = pack.charts[0].get("reason")
    state.extra_charts = pack.charts[1:] if len(pack.charts) > 1 else []
    if pack.warnings:
        state.warnings.extend(pack.warnings)
    if not pack.success:
        state.error = pack.error
    state.grounding_line = f"Used: EDA pack on table `{state.table_name}`"
    state.citations = [{"type": "eda", "table": state.table_name, "quality_score": pack.quality_score}]
    state.steps.append("analytics_agent:eda:done")
    return state


def run_root_cause(state: AgentState) -> AgentState:
    state.steps.append("analytics_agent:rca:start")
    df = _df_from_state(state)
    try:
        rca_mod = _load("root_cause", _CORE_DIR / "root_cause.py", required_attrs=("run_root_cause",))
        result = rca_mod.run_root_cause(df, question=state.question or "")
    except Exception as e:
        state.sql_success = False
        state.error = f"Root-cause analysis failed: {e}"
        state.steps.append("analytics_agent:rca:fail")
        return state

    state.root_cause = result
    state.sql_success = bool(result.success)
    if result.success:
        bullets = list(result.narrative_bullets or [])
        for bd in result.breakdowns:
            if bd.rows:
                top3 = ", ".join(f"{r['value']} ({r['share_pct']}%)" for r in bd.rows[:3])
                bullets.append(f"**{bd.dimension}** top: {top3}")
        state.insight_text = "\n".join(f"- {b}" for b in bullets)
        # Surface first breakdown as a small table
        if result.breakdowns and result.breakdowns[0].rows:
            import pandas as pd

            state.result_df = pd.DataFrame(result.breakdowns[0].rows)
        if result.sql_evidence:
            state.sql = result.sql_evidence[0]
        state.grounding_line = f"Used: root-cause on `{result.measure}` across {len(result.breakdowns)} dimension(s)"
        state.citations = [{"type": "root_cause", "measure": result.measure, "dimensions": [b.dimension for b in result.breakdowns]}]
    else:
        state.error = result.error or result.cannot_compute_reason
        state.insight_text = result.cannot_compute_reason or result.error
    if result.warnings:
        state.warnings.extend(result.warnings)
    state.steps.append("analytics_agent:rca:done")
    return state


def run_whatif(state: AgentState) -> AgentState:
    state.steps.append("analytics_agent:whatif:start")
    df = _df_from_state(state)
    try:
        wi = _load("whatif", _CORE_DIR / "whatif.py", required_attrs=("run_whatif",))
        result = wi.run_whatif(df, question=state.question or "")
    except Exception as e:
        state.sql_success = False
        state.error = f"What-if failed: {e}"
        state.steps.append("analytics_agent:whatif:fail")
        return state

    state.whatif = result
    state.sql_success = bool(result.success)
    if result.success:
        state.insight_text = "\n".join(f"- {b}" for b in (result.narrative or []))
        import pandas as pd

        state.result_df = pd.DataFrame(
            [
                {"metric": "baseline_total", "value": result.baseline_total},
                {"metric": "scenario_total", "value": result.scenario_total},
                {"metric": "delta", "value": result.delta},
                {"metric": "delta_pct", "value": result.delta_pct},
            ]
        )
        state.grounding_line = (
            f"Used: what-if {result.pct_change:+.1f}% on `{result.measure}`"
            + (f" / {result.dimension}={result.dimension_value}" if result.dimension_value else "")
        )
        state.citations = [{"type": "whatif", "measure": result.measure, "pct_change": result.pct_change}]
    else:
        state.error = result.error or result.cannot_compute_reason
        state.insight_text = result.cannot_compute_reason or result.error
    state.steps.append("analytics_agent:whatif:done")
    return state


def run_rfm(state: AgentState) -> AgentState:
    state.steps.append("analytics_agent:rfm:start")
    df = _df_from_state(state)
    try:
        coh = _load("cohorts", _CORE_DIR / "cohorts.py", required_attrs=("run_rfm",))
        result = coh.run_rfm(df)
    except Exception as e:
        state.sql_success = False
        state.error = f"RFM failed: {e}"
        state.steps.append("analytics_agent:rfm:fail")
        return state

    state.rfm = result
    state.sql_success = bool(result.success)
    if result.success:
        state.insight_text = "\n".join(f"- {b}" for b in (result.narrative or []))
        state.result_df = result.rfm_df.head(50) if result.rfm_df is not None else None
        state.grounding_line = "Used: RFM segmentation (customer_id, order_date, amount)"
        state.citations = [{"type": "rfm", "columns": result.columns_used}]
    else:
        state.error = result.error or result.cannot_compute_reason
        state.insight_text = result.cannot_compute_reason or result.error
    state.steps.append("analytics_agent:rfm:done")
    return state


def detect_analytics_path(question: str) -> Optional[str]:
    """Return eda|rca|whatif|rfm|None."""
    q = (question or "").lower().strip()
    if not q:
        return None
    if any(k in q for k in ("eda", "profile the data", "data profile", "explore the data", "one-click eda", "run eda", "exploratory")):
        return "eda"
    try:
        rca = _load("root_cause", _CORE_DIR / "root_cause.py")
        if getattr(rca, "looks_like_root_cause_question", lambda x: False)(q):
            return "rca"
    except Exception:
        if any(k in q for k in ("why did", "root cause", "breakdown", "what drove")):
            return "rca"
    try:
        wi = _load("whatif", _CORE_DIR / "whatif.py")
        if getattr(wi, "looks_like_whatif_question", lambda x: False)(q):
            return "whatif"
    except Exception:
        pass
    try:
        coh = _load("cohorts", _CORE_DIR / "cohorts.py")
        if getattr(coh, "looks_like_cohort_question", lambda x: False)(q):
            return "rfm"
    except Exception:
        if any(k in q for k in ("rfm", "cohort", "retention")):
            return "rfm"
    return None


def run(state: AgentState, path: Optional[str] = None) -> AgentState:
    path = path or detect_analytics_path(state.question or "")
    if path == "eda":
        return run_eda(state)
    if path == "rca":
        return run_root_cause(state)
    if path == "whatif":
        return run_whatif(state)
    if path == "rfm":
        return run_rfm(state)
    state.steps.append("analytics_agent:no_path")
    return state
