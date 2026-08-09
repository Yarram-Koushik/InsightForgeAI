# Phase 2 – Intelligent Analysis Layer

## Status

| Sub-Phase | Name | Status |
|-----------|------|--------|
| 2.1 | DuckDB Query Engine | ✅ Complete |
| 2.2 | Natural Language → SQL | ✅ Complete |
| 2.3 | Multi-Agent Orchestration | ✅ Complete |
| 2.4 | Automated Visualizations | ✅ Complete |
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
LangGraph-style pipeline (pure Python):

```
User Question
    → RouterAgent
         ├─ META / UNSUPPORTED → direct response
         ├─ CLARIFY → ClarifyAgent
         ├─ DATA_QUERY → SQLAgent → VizAgent
         └─ INSIGHT → SQLAgent → InsightAgent → VizAgent
```

## 2.4 Automated Visualizations

Rule-based (deterministic) chart recommendation + Plotly generation.

| Result shape | Chart |
|---|---|
| Category + measure | Bar (Top-N if needed) |
| Datetime + measure | Line |
| Few categories + share language | Pie |
| Two numerics | Scatter |
| Single numeric series | Histogram |
| Single scalar | KPI |

### Files
- `app/core/visualization.py` – recommendation + Plotly builder
- `app/agents/viz_agent.py` – pipeline integration
- Orchestrator runs VizAgent after successful SQL
- Streamlit renders with `st.plotly_chart`

## Next: 2.5 Forecasting & Advanced Analytics
Prophet forecasting, trend detection, basic anomaly signals.
