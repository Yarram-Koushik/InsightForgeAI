"""
Insight Agent – turns raw query results into short business-readable insights.
Only runs when we already have successful SQL results.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

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


INSIGHT_SYSTEM = """You are a senior business analyst working inside InsightForgeAI.

Given a user question, the SQL that was run, and a preview of the result rows,
write 2–5 short bullet points of clear business insight.

Rules:
- Be factual. Only use numbers that appear in the data preview.
- No fluff, no marketing language.
- If the result is empty, say so clearly.
- If the data is insufficient for deep insight, say what is missing.
- Keep total length under 120 words.
- Do not invent columns or values.
"""


def _preview_df(df, max_rows: int = 12) -> str:
    if df is None or df.empty:
        return "(no rows)"
    try:
        return df.head(max_rows).to_string(index=False, max_cols=10)
    except Exception:
        return str(df.head(max_rows))


def run(state: AgentState) -> AgentState:
    """Generate a business insight from successful SQL results."""
    state.steps.append("insight_agent:start")

    if not state.sql_success:
        state.steps.append("insight_agent:skipped_no_sql")
        return state

    client = get_llm_client()
    if not client.is_configured():
        n = 0 if state.result_df is None else len(state.result_df)
        state.insight_text = f"Query returned {n} row(s). Configure an LLM API key for richer business insights."
        state.warnings.append("Insight agent used a minimal local summary (no API key).")
        state.steps.append("insight_agent:local")
        return state

    user_prompt = (
        f"QUESTION: {state.question}\n\n"
        f"SQL:\n{state.sql or '(none)'}\n\n"
        f"RESULT PREVIEW:\n{_preview_df(state.result_df)}\n"
    )

    resp = client.chat(
        system_prompt=INSIGHT_SYSTEM,
        user_prompt=user_prompt,
        temperature=0.2,
        max_tokens=300,
    )

    if not resp.success:
        n = 0 if state.result_df is None else len(state.result_df)
        state.insight_text = f"Query returned {n} row(s). Insight generation failed: {resp.error}"
        state.warnings.append("Insight agent LLM call failed.")
        state.steps.append("insight_agent:llm_failed")
        return state

    state.insight_text = (resp.content or "").strip()
    state.provider = resp.provider or state.provider
    state.model = resp.model or state.model
    state.steps.append("insight_agent:done")
    return state
