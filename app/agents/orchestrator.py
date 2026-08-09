"""
Orchestrator – LangGraph-style multi-agent pipeline for InsightForgeAI Phase 2.3/2.4.

Pipeline:
  1. Validate inputs
  2. Router  → intent
  3. Branch:
       META / UNSUPPORTED → direct response
       CLARIFY            → ClarifyAgent
       DATA_QUERY         → SQLAgent → VizAgent
       INSIGHT            → SQLAgent → InsightAgent → VizAgent
  4. Package transparent AgentResult for the UI

Design principles (industry):
  - Never crash the UI
  - Prefer "I don't know" / clarify over hallucination
  - Always expose SQL + steps for auditability
  - Graceful degradation when LLM keys are missing
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

_AGENTS_DIR = Path(__file__).resolve().parent
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

_state_mod = _load("agent_state", _AGENTS_DIR / "state.py")
AgentState = _state_mod.AgentState
AgentResult = _state_mod.AgentResult
Intent = _state_mod.Intent

_router = _load("router_agent", _AGENTS_DIR / "router.py")
_sql = _load("sql_agent", _AGENTS_DIR / "sql_agent.py")
_insight = _load("insight_agent", _AGENTS_DIR / "insight_agent.py")
_clarify = _load("clarify_agent", _AGENTS_DIR / "clarify_agent.py")
_viz = _load("viz_agent", _AGENTS_DIR / "viz_agent.py")


def _meta_response(state: AgentState) -> AgentResult:
    tables = []
    try:
        if state.workspace:
            tables = state.workspace.list_datasets()
    except Exception:
        pass
    table_list = ", ".join(f"`{t}`" for t in tables) if tables else "(none loaded)"
    msg = (
        "I am InsightForgeAI — an AI business intelligence assistant.\n\n"
        "I can:\n"
        "• Answer questions about your uploaded datasets in plain English\n"
        "• Generate and run safe SQL (DuckDB)\n"
        "• Explain results in business language\n\n"
        f"Currently loaded datasets: {table_list}\n\n"
        "Try asking: How many rows per category? or Show the top 10 values."
    )
    return AgentResult(
        success=True,
        question=state.question,
        intent=Intent.META.value,
        intent_reason=state.intent_reason,
        message=msg,
        steps=state.steps + ["meta:done"],
        warnings=state.warnings,
        provider=state.provider,
        model=state.model,
    )


def _unsupported_response(state: AgentState) -> AgentResult:
    msg = (
        "This question cannot be answered from the currently loaded tabular data.\n"
        "I only analyse the datasets you upload (CSV / Excel / Parquet / JSON).\n"
        "Try a question about columns in the selected dataset."
    )
    return AgentResult(
        success=False,
        question=state.question,
        intent=Intent.UNSUPPORTED.value,
        intent_reason=state.intent_reason,
        message=msg,
        error="Unsupported question type for current data.",
        steps=state.steps + ["unsupported:done"],
        warnings=state.warnings,
        provider=state.provider,
        model=state.model,
    )


def _from_state(state: AgentState, success: bool, message: Optional[str] = None) -> AgentResult:
    return AgentResult(
        success=success,
        question=state.question,
        intent=(state.intent.value if state.intent else "unknown"),
        intent_reason=state.intent_reason,
        sql=state.sql,
        result_df=state.result_df,
        insight=state.insight_text,
        clarify_questions=list(state.clarify_questions),
        chart_fig=getattr(state, "chart_fig", None),
        chart_type=getattr(state, "chart_type", None),
        chart_reason=getattr(state, "chart_reason", None),
        steps=list(state.steps),
        warnings=list(state.warnings),
        error=state.error,
        provider=state.provider,
        model=state.model,
        message=message,
    )


def run_agent(
    workspace: Any,
    table_name: str,
    question: str,
) -> AgentResult:
    """Main entry point used by the Streamlit UI."""
    state = AgentState(
        question=(question or "").strip(),
        table_name=table_name,
        workspace=workspace,
    )
    state.steps.append("orchestrator:start")

    if not state.question:
        state.intent = Intent.CLARIFY
        state.intent_reason = "Empty question"
        state.clarify_questions = [
            "How many rows are in this dataset?",
            "What columns are available?",
            "Show me the first 10 rows",
        ]
        return _from_state(state, success=False, message="Please type a question about your data.")

    if not table_name:
        return AgentResult(
            success=False,
            question=state.question,
            intent=Intent.CLARIFY.value,
            message="Select a dataset from the sidebar before asking a question.",
            error="No dataset selected.",
            steps=["orchestrator:no_table"],
        )

    if workspace is None:
        return AgentResult(
            success=False,
            question=state.question,
            intent="error",
            message="Workspace is not initialised.",
            error="Workspace is None",
            steps=["orchestrator:no_workspace"],
        )

    try:
        record = workspace.get(table_name)
        if record and not record.metadata.get("duckdb_registered"):
            workspace.register_in_duckdb(table_name)
            state.steps.append("orchestrator:registered_table")
    except Exception as e:
        state.warnings.append(f"DuckDB registration check failed: {e}")

    try:
        state = _router.classify(state)
    except Exception as e:
        state.intent = Intent.DATA_QUERY
        state.intent_reason = f"Router crashed; defaulting to data_query ({e})"
        state.warnings.append("Router raised an exception; continued with data_query.")
        state.steps.append("router:exception")

    intent = state.intent or Intent.DATA_QUERY

    if intent == Intent.META:
        return _meta_response(state)

    if intent == Intent.UNSUPPORTED:
        return _unsupported_response(state)

    if intent == Intent.CLARIFY:
        try:
            state = _clarify.run(state)
        except Exception as e:
            state.clarify_questions = [
                "How many rows are in this dataset?",
                "Show distinct values for the main category column",
            ]
            state.warnings.append(f"Clarify agent failed: {e}")
        return _from_state(
            state,
            success=True,
            message="Your question is a bit broad. Try one of these more specific questions:",
        )

    try:
        state = _sql.run(state)
    except Exception as e:
        state.sql_success = False
        state.error = f"SQL agent failed: {e}"
        state.steps.append("sql_agent:exception")
        return _from_state(state, success=False, message=state.error)

    if not state.sql_success:
        return _from_state(
            state,
            success=False,
            message=state.sql_error or "Could not answer this question from the data.",
        )

    if intent == Intent.INSIGHT:
        try:
            state = _insight.run(state)
        except Exception as e:
            state.warnings.append(f"Insight agent failed: {e}")
            state.steps.append("insight_agent:exception")

    if not state.insight_text and state.result_df is not None:
        n = len(state.result_df)
        state.insight_text = f"Returned {n:,} row(s)."

    # Visualization stage (Phase 2.4) – best-effort, never blocks the answer
    try:
        state = _viz.run(state)
    except Exception as e:
        state.warnings.append(f"Visualization agent failed: {e}")
        state.steps.append("viz_agent:exception")

    msg = "Here is what I found."
    if state.insight_text and intent == Intent.INSIGHT:
        msg = "Here is the data and a short business interpretation."

    state.steps.append("orchestrator:done")
    return _from_state(state, success=True, message=msg)


run = run_agent
