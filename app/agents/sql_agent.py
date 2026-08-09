"""SQL Agent – NL→SQL + Phase 2.7 result sanity checks."""
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
_sanity = _load("result_sanity", _CORE_DIR / "result_sanity.py")
check_result_df = _sanity.check_result_df

def run(state: AgentState) -> AgentState:
    state.steps.append("sql_agent:start")
    try:
        result = ask(workspace=state.workspace, table_name=state.table_name, question=state.question)
    except Exception as e:
        state.sql_success = False
        state.sql_error = f"NL→SQL pipeline crashed: {e}"
        state.steps.append("sql_agent:exception")
        return state
    state.sql = result.final_sql or result.generated_sql
    state.sql_attempts = result.attempts
    state.provider = result.provider
    state.model = result.model
    if result.warnings:
        state.warnings.extend(result.warnings)
    if not result.success:
        state.sql_success = False
        state.sql_error = result.error or "SQL generation/execution failed"
        state.result_df = result.result_df
        state.steps.append("sql_agent:fail")
        return state
    state.sql_success = True
    state.result_df = result.result_df
    state.steps.append(f"sql_agent:ok:rows={0 if result.result_df is None else len(result.result_df)}")
    try:
        report = check_result_df(result.result_df, question=state.question or "")
        if report.warnings:
            state.warnings.extend(report.warnings)
        if report.hard_error:
            state.warnings.append(report.hard_error)
    except Exception as e:
        state.warnings.append(f"Sanity check skipped: {e}")
    return state
