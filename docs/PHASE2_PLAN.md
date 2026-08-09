# Phase 2 – Intelligent Analysis Layer

## Status

| Sub-Phase | Name | Status |
|-----------|------|--------|
| 2.1 | DuckDB Query Engine | ✅ Complete |
| 2.2 | Natural Language → SQL | ✅ Complete |
| 2.3 | Multi-Agent Orchestration | ✅ Complete |
| 2.4 | Automated Visualizations | Planned |
| 2.5 | Forecasting & Advanced Analytics | Planned |
| 2.6 | Full Chat + Evidence + Export | Planned |

---

## 2.1 DuckDB Query Engine
- Auto-register cleaned DataFrames as DuckDB tables
- Read-only SQL guard (SELECT / WITH / DESCRIBE / SHOW / EXPLAIN)
- Schema inspection + SQL Query tab

## 2.2 Natural Language → SQL
- Groq (primary) + Gemini (fallback) via shared `LLMClient`
- Schema-aware prompting (physical + semantic types + sample rows)
- Self-correction loop (max 2 retries on DuckDB errors)
- Transparent SQL always shown

## 2.3 Multi-Agent Orchestration
LangGraph-style pipeline (pure Python, no hard framework dependency):

```
User Question
    → RouterAgent          (intent classification)
         ├─ META           → system capability response
         ├─ UNSUPPORTED    → clear refusal
         ├─ CLARIFY        → ClarifyAgent (suggested questions)
         ├─ DATA_QUERY     → SQLAgent → results
         └─ INSIGHT        → SQLAgent → InsightAgent → results + narrative
```

### Edge cases handled
- Empty / missing question
- No dataset selected
- No API keys configured (heuristic router + local summaries)
- Router LLM failure (safe fallback to data_query)
- SQL failure after self-correction
- Empty result sets
- Agent exceptions isolated so UI never crashes

### Files
- `app/agents/state.py` – AgentState, AgentResult, Intent
- `app/agents/router.py`
- `app/agents/sql_agent.py`
- `app/agents/insight_agent.py`
- `app/agents/clarify_agent.py`
- `app/agents/orchestrator.py` – public `run_agent()`

## Next: 2.4 Automated Visualizations
Plotly chart recommendation + generation from agent results.
