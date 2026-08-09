"""
Visualization Agent – builds a Plotly chart from successful SQL results.
Wraps app/core/visualization.py. Never crashes the pipeline.
"""

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

_viz = _load("visualization_engine", _CORE_DIR / "visualization.py")
recommend_and_build = _viz.recommend_and_build


def run(state: AgentState, preferred_type: str | None = None) -> AgentState:
    """Attach chart recommendation + figure to state when SQL succeeded."""
    state.steps.append("viz_agent:start")

    if not state.sql_success or state.result_df is None:
        state.steps.append("viz_agent:skipped_no_data")
        return state

    result = recommend_and_build(
        df=state.result_df,
        question=state.question,
        preferred_type=preferred_type,
    )

    if result.warnings:
        state.warnings.extend(result.warnings)

    if result.success and result.fig is not None:
        state.chart_fig = result.fig
        state.chart_type = result.spec.chart_type if result.spec else None
        state.chart_reason = result.spec.reason if result.spec else None
        state.steps.append(f"viz_agent:ok:{state.chart_type}")
    else:
        state.chart_fig = None
        state.chart_type = result.spec.chart_type if result.spec else None
        state.chart_reason = result.skipped_reason or result.error
        if result.error and result.skipped_reason not in ("table_only", "empty"):
            state.warnings.append(result.error)
        state.steps.append(f"viz_agent:skip:{result.skipped_reason or 'error'}")

    return state
