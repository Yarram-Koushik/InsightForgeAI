"""Lightweight multi-turn context resolution (Phase 2.7)"""
from __future__ import annotations
from typing import Optional, List, Dict, Any

FOLLOWUP_MARKERS = ["only for", "what about", "same for", "now show", "now only", "filter to", "just the", "make it", "change to", "instead", "those", "that", "same", "again", "also", "and for"]

def looks_like_followup(question: str) -> bool:
    q = (question or "").strip().lower()
    if len(q.split()) <= 6 and any(m in q for m in FOLLOWUP_MARKERS):
        return True
    if q.startswith(("only ", "and ", "what about", "how about", "now ")):
        return True
    return False

def expand_question_with_history(question: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
    q = (question or "").strip()
    if not history or not looks_like_followup(q):
        return q
    prev_q = None
    prev_sql = None
    for turn in reversed(history):
        pq = (turn.get("question") or "").strip()
        if pq and not looks_like_followup(pq):
            prev_q = pq
            prev_sql = turn.get("sql")
            break
    if not prev_q:
        prev_q = (history[-1].get("question") or "").strip()
        prev_sql = history[-1].get("sql")
    if not prev_q:
        return q
    return (
        f"Previous question: {prev_q}\n"
        f"Follow-up refinement: {q}\n"
        f"Answer the follow-up in the context of the previous question"
        + (f" (previous SQL was: {prev_sql})" if prev_sql else "")
        + "."
    )
