"""Orchestrator – multi-agent pipeline (matches AgentState / AgentResult)."""
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


_state_mod = _load(
    "agent_state",
    _AGENTS_DIR / "state.py",
    required_attrs=("AgentState", "AgentResult", "Intent"),
)
AgentState = _state_mod.AgentState
AgentResult = _state_mod.AgentResult
Intent = _state_mod.Intent

_router = _load("router_agent", _AGENTS_DIR / "router.py")
_sql = _load("sql_agent", _AGENTS_DIR / "sql_agent.py")
_insight = _load("insight_agent", _AGENTS_DIR / "insight_agent.py")
_clarify = _load("clarify_agent", _AGENTS_DIR / "clarify_agent.py")
_viz = _load("viz_agent", _AGENTS_DIR / "viz_agent.py")
_forecast = _load("forecast_agent", _AGENTS_DIR / "forecast_agent.py")

try:
    _ctx = _load("context_memory", _AGENTS_DIR.parent / "core" / "context_memory.py")
except Exception:
    _ctx = None


def _intent_str(intent) -> str:
    if intent is None:
        return "unknown"
    return intent.value if hasattr(intent, "value") else str(intent)


def _from_state(state: AgentState, success: bool, message: Optional[str] = None) -> AgentResult:
    insight = getattr(state, "insight_text", None) or getattr(state, "insight", None)
    clarify = list(getattr(state, "clarify_questions", None) or [])
    return AgentResult(
        success=success,
        question=state.question or "",
        intent=_intent_str(state.intent),
        intent_reason=state.intent_reason,
        sql=state.sql,
        result_df=state.result_df,
        insight=insight,
        clarify_questions=clarify,
        chart_fig=getattr(state, "chart_fig", None),
        chart_type=getattr(state, "chart_type", None),
        chart_reason=getattr(state, "chart_reason", None),
        forecast_df=getattr(state, "forecast_df", None),
        forecast_method=getattr(state, "forecast_method", None),
        forecast_horizon=getattr(state, "forecast_horizon", None),
        trend_summary=getattr(state, "trend_summary", None),
        anomalies=list(getattr(state, "anomalies", None) or []),
        steps=list(getattr(state, "steps", None) or []),
        warnings=list(getattr(state, "warnings", None) or []),
        error=state.error or getattr(state, "sql_error", None),
        provider=getattr(state, "provider", None),
        model=getattr(state, "model", None),
        message=message or insight or (clarify[0] if clarify else None) or ("Done." if success else "Failed."),
    )


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
    return AgentResult(
        success=True,
        question=state.question or "",
        intent=_intent_str(Intent.META),
        intent_reason=state.intent_reason or "meta about system",
        message=msg,
        steps=list(state.steps or []),
    )


def _unsupported_response(state: AgentState) -> AgentResult:
    return AgentResult(
        success=False,
        question=state.question or "",
        intent=_intent_str(Intent.UNSUPPORTED),
        intent_reason=state.intent_reason or "unsupported",
        message=(
            "I cannot answer that from the current dataset. "
            "Try a question about the loaded columns, or upload more data."
        ),
        steps=list(state.steps or []),
    )


def run_agent(
    question: str = "",
    table_name: str = "",
    workspace: Any = None,
    **kwargs,
) -> AgentResult:
    """
    Entry point used by Streamlit UI and API.

    Accepts:
      run_agent(question, table_name, workspace)
      run_agent(workspace=..., table_name=..., question=...)
    """
    if kwargs:
        question = kwargs.get("question", question) or question
        table_name = kwargs.get("table_name", table_name) or table_name
        workspace = kwargs.get("workspace", workspace)

    state = AgentState(
        question=(question or "").strip(),
        table_name=table_name or "",
        workspace=workspace,
    )
    state.steps = ["orchestrator:start"]

    try:
        if _ctx is not None and hasattr(_ctx, "attach_context"):
            try:
                _ctx.attach_context(state)
            except Exception:
                pass

        state.steps.append("router:start")
        if hasattr(_router, "classify"):
            _router.classify(state)
        elif hasattr(_router, "route"):
            _router.route(state)
        else:
            state.intent = Intent.DATA_QUERY
            state.intent_reason = "router missing; default data_query"
        state.steps.append(f"router:done:{_intent_str(state.intent)}")

        if state.intent == Intent.META:
            return _meta_response(state)
        if state.intent == Intent.UNSUPPORTED:
            return _unsupported_response(state)
        if state.intent == Intent.CLARIFY:
            state.steps.append("clarify:start")
            _clarify.run(state)
            state.steps.append("clarify:done")
            msg = None
            if state.clarify_questions:
                msg = "I need a bit more detail:\n- " + "\n- ".join(state.clarify_questions[:5])
            return _from_state(state, True, msg)

        if state.intent in (Intent.DATA_QUERY, Intent.INSIGHT, Intent.FORECAST):
            state.steps.append("sql_agent:start")
            _sql.run(state)
            if not state.sql_success:
                state.steps.append("sql_agent:fail")
                state.error = getattr(state, "sql_error", None) or "SQL failed."
                return _from_state(state, False, state.error)

            if state.intent == Intent.INSIGHT:
                state.steps.append("insight:start")
                _insight.run(state)
                state.steps.append("insight:done")

            if state.intent == Intent.FORECAST:
                state.steps.append("forecast:start")
                _forecast.run(state)
                state.steps.append("forecast:done")

            try:
                state.steps.append("viz_agent:start")
                _viz.run(state)
                state.steps.append("viz_agent:done")
            except Exception:
                state.steps.append("viz_agent:skip")

            if _ctx is not None and hasattr(_ctx, "remember"):
                try:
                    _ctx.remember(state)
                except Exception:
                    pass

            msg = getattr(state, "insight_text", None) or "Here is what I found."
            return _from_state(state, True, msg)

        return _unsupported_response(state)

    except Exception as e:
        state.steps.append(f"orchestrator:error:{e}")
        return AgentResult(
            success=False,
            question=state.question or (question or ""),
            intent=_intent_str(getattr(state, "intent", None)) or "unknown",
            message=f"Agent pipeline error: {e}",
            error=str(e),
            steps=list(state.steps or []),
        )
