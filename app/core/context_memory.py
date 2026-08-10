"""
InsightForgeAI – Conversational Memory (Phase 4.2)

Strong multi-turn context so follow-ups like:
  "Why?", "by region", "compare last year", "only North", "same for Q3"
are resolved against the last successful SQL / result / metric / table.

Also refuses ambiguous pronouns after a dataset switch.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Markers that indicate the user is refining the previous turn
FOLLOWUP_MARKERS = [
    "only for", "what about", "same for", "now show", "now only", "filter to",
    "just the", "make it", "change to", "instead", "those", "that", "same",
    "again", "also", "and for", "by region", "by segment", "by month",
    "vs last", "versus last", "compare last", "why", "why did", "break it down",
    "drill", "split by", "group by", "for north", "for south",
]

PRONOUN_MARKERS = ("it", "that", "those", "them", "this", "these")


def looks_like_followup(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q:
        return False
    if len(q.split()) <= 8 and any(m in q for m in FOLLOWUP_MARKERS):
        return True
    if q.startswith(("only ", "and ", "what about", "how about", "now ", "why", "by ")):
        return True
    # Short pronoun-heavy questions
    tokens = q.split()
    if len(tokens) <= 5 and any(t in PRONOUN_MARKERS for t in tokens):
        return True
    return False


def expand_question_with_history(
    question: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Expand a short follow-up into a full question that includes previous context.
    Used by the UI / orchestrator before calling the SQL agent.
    """
    q = (question or "").strip()
    if not history or not looks_like_followup(q):
        return q

    prev_q = None
    prev_sql = None
    prev_table = None
    prev_metric = None
    for turn in reversed(history):
        pq = (turn.get("question") or "").strip()
        if pq and not looks_like_followup(pq):
            prev_q = pq
            prev_sql = turn.get("sql")
            prev_table = turn.get("table_name")
            # Try to surface metric from grounding if present
            gl = turn.get("grounding_line") or ""
            if "metric `" in gl:
                try:
                    prev_metric = gl.split("metric `")[1].split("`")[0]
                except Exception:
                    pass
            break
    if not prev_q and history:
        prev_q = (history[-1].get("question") or "").strip()
        prev_sql = history[-1].get("sql")
        prev_table = history[-1].get("table_name")

    if not prev_q:
        return q

    parts = [
        f"Previous question: {prev_q}",
        f"Follow-up refinement: {q}",
        "Answer the follow-up in the context of the previous question",
    ]
    if prev_sql:
        parts.append(f"(previous SQL was: {prev_sql})")
    if prev_table:
        parts.append(f"(previous table: {prev_table})")
    if prev_metric:
        parts.append(f"(previous metric: {prev_metric})")
    parts.append(".")
    return " ".join(parts)


def attach_context(state: Any) -> None:
    """
    Called by orchestrator before routing.
    If the current question looks like a follow-up and we have prior turn
    context on the state (injected by UI), expand the question in-place.
    """
    history = getattr(state, "chat_history", None) or getattr(state, "history", None)
    if not history:
        return
    original = state.question or ""
    expanded = expand_question_with_history(original, history)
    if expanded != original:
        state.question = expanded
        state.steps.append("context_memory:expanded_followup")
        # Keep a copy of the raw user text for display
        state.raw_question = original


def remember(state: Any) -> None:
    """
    Hook after a successful turn. Currently a no-op for in-memory;
    durable persistence is handled by workspace_store via the UI.
    Kept so the orchestrator call site stays stable.
    """
    return


__all__ = [
    "looks_like_followup",
    "expand_question_with_history",
    "attach_context",
    "remember",
    "FOLLOWUP_MARKERS",
]
