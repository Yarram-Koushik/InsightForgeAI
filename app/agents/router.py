"""
Router Agent – classifies user intent before any heavy work.
Uses the shared LLM client. Falls back to DATA_QUERY on any failure
so the system never blocks the user.

Phase 4.6: knowledge (policy/SOP) + proactive scan intents.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Tuple

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
Intent = _state.Intent
AgentState = _state.AgentState

_llm = _load("llm_client", _CORE_DIR / "llm_client.py")
get_llm_client = _llm.get_llm_client


ROUTER_SYSTEM = """You are the intent router for InsightForgeAI, a business intelligence product.

Classify the user question into exactly one intent:

- data_query : User wants numbers, counts, lists, filters, rankings, aggregations from the table.
- insight    : User wants explanation, comparison meaning, "why", "what does this mean", summary insights (not a numeric forecast).
- forecast   : User wants a future forecast, projection, trend over time, or anomaly detection on a time series.
- knowledge  : User asks about company policy, SOP, process, refund rules, or content that lives in uploaded documents (not table numbers).
- proactive  : User asks "anything unusual", "what should I watch", "proactive insights", or a scan for anomalies vs recent baseline.
- clarify    : Question is too vague, missing key filters, or could mean multiple things. Needs clarification.
- meta       : Question is about the system itself (capabilities, how it works, what data is loaded).
- unsupported: Clearly cannot be answered from tabular company data or the knowledge base (e.g. weather, news, general knowledge, coding help).

Reply with ONLY this format (no markdown):
INTENT: <one of the eight>
REASON: <one short sentence>
"""


def _heuristic_intent(question: str) -> Tuple[str, str]:
    """Fast local fallback when LLM is unavailable."""
    q = question.lower().strip()

    meta_kw = ["what can you", "how do you", "who are you", "help", "capabilities", "what data"]
    if any(k in q for k in meta_kw) and len(q) < 80:
        return "meta", "Question appears to be about the system."

    proactive_kw = [
        "anything unusual", "what is unusual", "what's unusual", "proactive",
        "what should i watch", "any anomalies", "scan for anomalies",
        "unusual patterns", "anything odd", "red flags",
    ]
    if any(k in q for k in proactive_kw):
        return "proactive", "User requested a proactive / unusual-pattern scan."

    knowledge_kw = [
        "policy", "policies", "sop", "standard operating", "refund policy",
        "return policy", "our process", "procedure", "handbook", "guidelines",
        "what is our", "what's our", "company rule", "how do we handle",
        "knowledge base", "from the document", "according to the doc",
    ]
    if any(k in q for k in knowledge_kw):
        return "knowledge", "Question looks like a policy / process / document question."

    clarify_kw = ["something", "stuff", "anything", "performance", "analyse this", "analyze this", "tell me about"]
    if q in ("?", "data", "report", "analysis") or (any(k in q for k in clarify_kw) and len(q.split()) <= 4):
        if "unusual" not in q and "anomaly" not in q:
            return "clarify", "Question is too vague to answer precisely."

    forecast_kw = ["forecast", "predict", "projection", "next week", "next month", "next 30", "future", "anomaly", "anomalies", "time series", "over time"]
    if any(k in q for k in forecast_kw):
        return "forecast", "User is asking for forecast, trend-over-time, or anomalies."

    insight_kw = ["why", "insight", "explain", "meaning", "compare", "summary", "what does", "interpret"]
    if any(k in q for k in insight_kw):
        return "insight", "User is asking for interpretation or explanation."

    return "data_query", "Default: treat as a data retrieval question."


def classify(state: AgentState) -> AgentState:
    """Classify intent and write it into state. Never raises."""
    state.steps.append("router:start")
    question = (state.question or "").strip()

    if not question:
        state.intent = Intent.CLARIFY
        state.intent_reason = "Empty question."
        state.steps.append("router:empty")
        return state

    client = get_llm_client()
    if not client.is_configured():
        intent_str, reason = _heuristic_intent(question)
        state.intent = Intent(intent_str)
        state.intent_reason = reason + " (heuristic – no API key)"
        state.warnings.append("Router used local heuristics because no LLM API key is configured.")
        state.steps.append(f"router:heuristic:{intent_str}")
        return state

    schema_hint = ""
    try:
        if state.workspace and state.table_name:
            schema_df = state.workspace.get_table_schema(state.table_name)
            if schema_df is not None and "column_name" in schema_df.columns:
                cols = ", ".join(schema_df["column_name"].astype(str).tolist()[:20])
                schema_hint = f"\nAvailable columns in current table: {cols}"
    except Exception:
        pass

    user_prompt = f"QUESTION: {question}{schema_hint}"

    resp = client.chat(
        system_prompt=ROUTER_SYSTEM,
        user_prompt=user_prompt,
        temperature=0.0,
        max_tokens=120,
    )

    if not resp.success:
        intent_str, reason = _heuristic_intent(question)
        state.intent = Intent(intent_str)
        state.intent_reason = reason + f" (LLM router failed: {resp.error})"
        state.warnings.append("Router fell back to heuristics after LLM error.")
        state.steps.append(f"router:fallback:{intent_str}")
        return state

    text = (resp.content or "").strip()
    intent_match = re.search(r"INTENT:\s*(\w+)", text, re.IGNORECASE)
    reason_match = re.search(r"REASON:\s*(.+)", text, re.IGNORECASE)

    raw_intent = (intent_match.group(1).lower() if intent_match else "data_query").strip()
    reason = reason_match.group(1).strip() if reason_match else "Classified by router."

    mapping = {
        "data_query": Intent.DATA_QUERY,
        "data": Intent.DATA_QUERY,
        "query": Intent.DATA_QUERY,
        "insight": Intent.INSIGHT,
        "insights": Intent.INSIGHT,
        "forecast": Intent.FORECAST,
        "prediction": Intent.FORECAST,
        "knowledge": Intent.KNOWLEDGE,
        "policy": Intent.KNOWLEDGE,
        "document": Intent.KNOWLEDGE,
        "rag": Intent.KNOWLEDGE,
        "proactive": Intent.PROACTIVE,
        "scan": Intent.PROACTIVE,
        "clarify": Intent.CLARIFY,
        "clarification": Intent.CLARIFY,
        "meta": Intent.META,
        "unsupported": Intent.UNSUPPORTED,
    }
    state.intent = mapping.get(raw_intent, Intent.DATA_QUERY)
    state.intent_reason = reason
    state.provider = resp.provider
    state.model = resp.model
    state.steps.append(f"router:done:{state.intent.value}")
    return state
