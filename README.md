# InsightForgeAI

**AI-Powered Business Intelligence Assistant**

InsightForgeAI turns company data into actionable insights using natural language.

Upload sales, finance, marketing or customer data → Ask questions in plain English → Get charts, root-cause analysis, forecasts and dashboards automatically.

## Vision

ChatGPT for company data — production-grade, free-stack, multi-agent system.

## Tech Stack (100% Free)

- Python 3.11+ / 3.12
- FastAPI + Streamlit
- DuckDB + Pandas + Polars
- LangGraph-style multi-agent orchestration
- Groq / Gemini / Ollama
- Plotly
- Prophet
- Docker

## Current Status

| Phase | Status | Description |
|-------|--------|-------------|
| **Phase 0** | ✅ Complete | Project foundation & structure |
| **Phase 1** | ✅ Complete | Industry-level data foundation |
| **Phase 2** | 🚧 In Progress | Intelligent Analysis Layer |
| → Sub-Phase 2.1 | ✅ Complete | DuckDB Query Engine |
| → Sub-Phase 2.2 | ✅ Complete | NL → SQL (self-correcting) |
| → Sub-Phase 2.3 | ✅ Complete | Multi-agent orchestration |
| → Sub-Phase 2.4 | ✅ Complete | Automated Plotly visualizations |
| → Sub-Phase 2.5 | Planned | Forecasting (Prophet) & advanced analytics |
| → Sub-Phase 2.6 | Planned | Full chat UI + evidence grounding + export |

## Phase 2.4 Highlights

- Rule-based chart recommendation (bar / line / pie / scatter / histogram / KPI)
- Auto Plotly charts after every successful data answer
- Top-N handling for high-cardinality categories
- Edge cases: empty results, single value KPI, too-wide tables, missing plotly
- Chart reason shown for transparency

## Phase 2.3 Highlights

- Router / SQL / Insight / Clarify agents
- Full pipeline transparency (route, SQL evidence, agent steps)
- Graceful degradation when API keys are missing

## How to Run

```bash
pip install -r requirements.txt
cp .env.example .env   # add GROQ_API_KEY
streamlit run app/frontend/app.py
```

## Project Structure

- `app/core/` → ingestion, schema, cleaning, profiling, data_manager, llm_client, nl_to_sql, **visualization**
- `app/agents/` → multi-agent orchestration + **viz_agent**
- `app/frontend/` → Streamlit UI
- `docs/` → phase plans
