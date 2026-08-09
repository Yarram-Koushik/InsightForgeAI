"""
Clarify Agent – produces concrete follow-up questions when intent is unclear.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

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

_llm = _load("llm_client", _CORE_DIR / "llm_client.py")
get_llm_client = _llm.get_llm_client


CLARIFY_SYSTEM = """You help users refine vague analytics questions.

Given the user question and the available columns, suggest 2–4 short, concrete
follow-up questions the user could ask next. Each question should be answerable
from the table.

Rules:
- One question per line
- No numbering required, but you may use - 
- Do not invent columns that are not listed
- Keep each question under 15 words
"""


def _default_questions(columns: List[str]) -> List[str]:
    qs = [
        "How many rows are in this dataset?",
        "What are the distinct values in the main category column?",
    ]
    if columns:
        col = columns[0]
        qs.append(f"Show the top values for {col}")
        if len(columns) > 1:
            qs.append(f"Count records by {columns[1]}")
    return qs[:4]


def run(state: AgentState) -> AgentState:
    state.steps.append("clarify_agent:start")

    columns: List[str] = []
    try:
        if state.workspace and state.table_name:
            schema_df = state.workspace.get_table_schema(state.table_name)
            if schema_df is not None and "column_name" in schema_df.columns:
                columns = schema_df["column_name"].astype(str).tolist()
    except Exception:
        pass

    client = get_llm_client()
    if not client.is_configured():
        state.clarify_questions = _default_questions(columns)
        state.steps.append("clarify_agent:default")
        return state

    col_text = ", ".join(columns[:25]) if columns else "(unknown)"
    user_prompt = (
        f"USER QUESTION: {state.question}\n"
        f"AVAILABLE COLUMNS: {col_text}\n"
    )

    resp = client.chat(
        system_prompt=CLARIFY_SYSTEM,
        user_prompt=user_prompt,
        temperature=0.3,
        max_tokens=200,
    )

    if not resp.success:
        state.clarify_questions = _default_questions(columns)
        state.warnings.append("Clarify agent fell back to defaults.")
        state.steps.append("clarify_agent:fallback")
        return state

    lines = []
    for line in (resp.content or "").splitlines():
        line = line.strip().lstrip("-•*0123456789. ").strip()
        if line and len(line) > 5:
            lines.append(line)
    state.clarify_questions = lines[:4] or _default_questions(columns)
    state.provider = resp.provider or state.provider
    state.model = resp.model or state.model
    state.steps.append("clarify_agent:done")
    return state
