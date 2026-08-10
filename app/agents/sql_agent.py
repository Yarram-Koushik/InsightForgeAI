"""SQL Agent – NL→SQL + Phase 2.7 result sanity checks + Phase 4.2 citations."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any

_AGENTS_DIR = Path(__file__).resolve().parent
_CORE_DIR = _AGENTS_DIR.parent / "core"
_ROOT = _AGENTS_DIR.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import importlib.util


def _load(name: str, path: Path, required_attrs: tuple = ()) -> Any:
    """Safe dynamic loader – discard incomplete modules, prefer package import."""
    if name in sys.modules:
        mod = sys.modules[name]
        if required_attrs and not all(hasattr(mod, a) for a in required_attrs):
            del sys.modules[name]
        else:
            return mod
    # Prefer package import for nl_to_sql when available
    if name == "nl_to_sql":
        try:
            from app.core import nl_to_sql as mod  # type: ignore
            if hasattr(mod, "ask"):
                sys.modules[name] = mod
                return mod
        except Exception:
            pass
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    if required_attrs and not all(hasattr(mod, a) for a in required_attrs):
        sys.modules.pop(name, None)
        raise AttributeError(f"Module '{name}' missing required attributes: {required_attrs}")
    return mod


_state = _load("agent_state", _AGENTS_DIR / "state.py", required_attrs=("AgentState",))
AgentState = _state.AgentState

_nl = _load("nl_to_sql", _CORE_DIR / "nl_to_sql.py", required_attrs=("ask",))
ask = _nl.ask

_sanity = _load("result_sanity", _CORE_DIR / "result_sanity.py")
check_result_df = getattr(_sanity, "check_result_df", None)


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

    # Phase 4.2 – surface citations / grounding on the agent state
    if getattr(result, "citations", None):
        state.citations = list(result.citations)
    if getattr(result, "grounding_line", None):
        state.grounding_line = result.grounding_line

    if not result.success:
        state.sql_success = False
        state.sql_error = result.error or "SQL generation/execution failed"
        state.result_df = result.result_df
        state.steps.append("sql_agent:fail")
        return state

    state.sql_success = True
    state.result_df = result.result_df
    state.steps.append(f"sql_agent:ok:rows={0 if result.result_df is None else len(result.result_df)}")

    if check_result_df is not None:
        try:
            report = check_result_df(result.result_df, question=state.question or "")
            if report.warnings:
                state.warnings.extend(report.warnings)
            if getattr(report, "hard_error", None):
                state.warnings.append(report.hard_error)
        except Exception as e:
            state.warnings.append(f"Sanity check skipped: {e}")
    return state
