"""Orchestrator – Phase 2.3–2.7 multi-agent pipeline."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any, Optional

_AGENTS_DIR = Path(__file__).resolve().parent
_ROOT = _AGENTS_DIR.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import importlib.util

def _load(name: str, path: Path, required_attrs: tuple = ()):
    """Load a module by path. Reload if a stale/empty module is already cached."""
    if name in sys.modules:
        mod = sys.modules[name]
        if required_attrs and not all(hasattr(mod, a) for a in required_attrs):
            del sys.modules[name]
        else:
            return mod
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod

_state_mod = _load("agent_state", _AGENTS_DIR / "state.py", required_attrs=("AgentState", "AgentResult", "Intent"))
AgentState = _state_mod.AgentState
AgentResult = _state_mod.AgentResult
Intent = _state_mod.Intent

_router = _load("router_agent", _AGENTS_DIR / "router.py")
_sql = _load("sql_agent", _AGENTS_DIR / "sql_agent.py")
_insight = _load("insight_agent", _AGENTS_DIR / "insight_agent.py")
_clarify = _load("clarify_agent", _AGENTS_DIR / "clarify_agent.py")
_viz = _load("viz_agent", _AGENTS_DIR / "viz_agent.py")
_forecast = _load("forecast_agent", _AGENTS_DIR / "forecast_agent.py")
_ctx = _load("context_memory", _AGENTS_DIR.parent / "core" / "context_memory.py")

def _meta_response(state: AgentState) -> AgentResult:
    tables = []
    try:
        if state.workspace:
            tables = state.workspace.list_datasets()
    except Exception:
        tables = []
    msg = (
        "I am InsightForgeAI — an AI BI assistant for your uploaded data. "
        "I can run SQL analytics, explain insights, forecast trends, and clarify vague questions."
    )
    if tables:
        msg += f" Loaded tables: {', '.join(tables)}."
    return AgentResult(success=True, message=msg, intent=Intent.META, intent_reason="meta about system")

def _unsupported_response(state: AgentState) -> AgentResult:
    return AgentResult(
        success=False,
        message="I cannot answer that from the current dataset. Try a question about the loaded columns, or upload more data.",
        intent=Intent.UNSUPPORTED,
        intent_reason=state.intent_reason or "unsupported",
    )

def _from_state(state: AgentState, success: bool, message: Optional[str] = None) -> AgentResult:
    return AgentResult(
        success=success,
        message=message or state.insight or state.clarify_message or ("Done." if success else "Failed."),
        intent=state.intent,
        intent_reason=state.intent_reason,
        sql=state.sql,
        result_df=state.result_df,
        chart_html=getattr(state, "chart_html", None),
        forecast_df=getattr(state, "forecast_df", None),
        pipeline_steps=list(getattr(state, "pipeline_steps", []) or []),
        provider=getattr(state, "provider", None),
        model=getattr(state, "model", None),
        error=state.error,
    )

def run_agent(question: str, table_name: str, workspace: Any = None) -> AgentResult:
    state = AgentState(question=(question or "").strip(), table_name=table_name, workspace=workspace)
    state.pipeline_steps = ["orchestrator:start"]
    try:
        # Optional conversation context
        try:
            _ctx.attach_context(state)
        except Exception:
            pass

        state.pipeline_steps.append("router:start")
        _router.route(state)
        state.pipeline_steps.append(f"router:done:{(state.intent.value if state.intent else 'unknown')}")

        if state.intent == Intent.META:
            return _meta_response(state)
        if state.intent == Intent.UNSUPPORTED:
            return _unsupported_response(state)
        if state.intent == Intent.CLARIFY:
            state.pipeline_steps.append("clarify:start")
            _clarify.run(state)
            state.pipeline_steps.append("clarify:done")
            return _from_state(state, True, state.clarify_message or state.message)

        if state.intent in (Intent.DATA_QUERY, Intent.INSIGHT, Intent.FORECAST):
            state.pipeline_steps.append("sql_agent:start")
            _sql.run(state)
            if state.sql_success:
                state.pipeline_steps.append(f"sql_agent:ok:rows={0 if state.result_df is None else len(state.result_df)}")
            else:
                state.pipeline_steps.append("sql_agent:fail")
                return _from_state(state, False, state.error or "SQL failed.")

            if state.intent == Intent.INSIGHT:
                state.pipeline_steps.append("insight:start")
                _insight.run(state)
                state.pipeline_steps.append("insight:done")

            if state.intent == Intent.FORECAST:
                state.pipeline_steps.append("forecast:start")
                _forecast.run(state)
                state.pipeline_steps.append("forecast:done")

            try:
                state.pipeline_steps.append("viz_agent:start")
                _viz.run(state)
                state.pipeline_steps.append("viz_agent:done")
            except Exception:
                state.pipeline_steps.append("viz_agent:skip")

            try:
                _ctx.remember(state)
            except Exception:
                pass

            msg = state.insight or "Here is what I found."
            return _from_state(state, True, msg)

        return _unsupported_response(state)
    except Exception as e:
        state.pipeline_steps.append(f"orchestrator:error:{e}")
        return AgentResult(success=False, message=f"Agent pipeline error: {e}", error=str(e))
