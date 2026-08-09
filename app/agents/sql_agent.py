"""
SQL Agent – thin, reliable wrapper around the Phase 2.2 NL→SQL engine.
Does not reinvent SQL generation; reuses battle-tested ask().
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

_nl = _load("nl_to_sql", _CORE_DIR / "nl_to_sql.py")
ask = _nl.ask


def run(state: AgentState) -> AgentState:
    """Execute NL→SQL pipeline and write results into state."""
    state.steps.append("sql_agent:start")

    if not state.workspace or not state.table_name:
        state.sql_success = False
        state.sql_error = "No dataset selected."
        state.error = state.sql_error
        state.steps.append("sql_agent:no_dataset")
        return state

    result = ask(
        workspace=state.workspace,
        table_name=state.table_name,
        question=state.question,
    )

    state.sql = result.final_sql or result.generated_sql
    state.sql_success = bool(result.success)
    state.result_df = result.result_df
    state.sql_error = result.error
    state.sql_attempts = result.attempts
    state.provider = result.provider or state.provider
    state.model = result.model or state.model

    if result.warnings:
        state.warnings.extend(result.warnings)

    if result.success:
        state.steps.append(f"sql_agent:ok:rows={0 if result.result_df is None else len(result.result_df)}")
    else:
        state.error = result.error
        state.steps.append("sql_agent:failed")

    return state
