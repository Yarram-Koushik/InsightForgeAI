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
- ChromaDB
- Prophet + Plotly
- Docker

## Current Status

| Phase | Status | Description |
|-------|--------|-------------|
| **Phase 0** | ✅ Complete | Project foundation & structure |
| **Phase 1** | ✅ Complete | Industry-level data foundation: multi-format ingestion, semantic schema detection, safe auto-cleaning + lineage, transparent quality scoring, Workspace management |
| **Phase 2** | 🚧 In Progress | Intelligent Analysis Layer |
| → Sub-Phase 2.1 | ✅ Complete | DuckDB Query Engine – auto-register tables, safe SQL, schema inspection |
| → Sub-Phase 2.2 | ✅ Complete | LLM client (Groq/Gemini) + self-correcting Natural Language → SQL |
| → Sub-Phase 2.3 | ✅ Complete | Multi-agent orchestration (Router · SQL · Insight · Clarify) |
| → Sub-Phase 2.4 | Planned | Automated Plotly visualizations |
| → Sub-Phase 2.5 | Planned | Forecasting (Prophet) & advanced analytics |
| → Sub-Phase 2.6 | Planned | Full chat UI + evidence grounding + export |

## Phase 2.3 Highlights

- **Router Agent** – classifies every question (data / insight / clarify / meta / unsupported)
- **SQL Agent** – reuses Phase 2.2 self-correcting NL→SQL
- **Insight Agent** – short business interpretation of query results
- **Clarify Agent** – concrete follow-up questions when the ask is vague
- Full pipeline transparency (route, SQL evidence, agent steps)
- Graceful degradation when API keys are missing

## Phase 1 Highlights

- Multi-file / multi-sheet Excel support
- Semantic type detection (Email, URL, Phone, Currency, DateTime, Identifier, Categorical, Free Text…)
- Confidence-scored safe auto-cleaning with full change lineage
- Multi-dimensional quality score (Completeness 50% + Uniqueness 30% + Validity 20%)
- Session Workspace with raw + cleaned versions of every dataset

## How to Run

```bash
# Install
uv sync   # or pip install -r requirements.txt

# Add API keys
cp .env.example .env
# Edit .env → GROQ_API_KEY=...

# Launch
streamlit run app/frontend/app.py
```

## Project Structure

- `app/core/` → ingestion, schema, cleaning, profiling, data_manager (DuckDB), llm_client, nl_to_sql
- `app/agents/` → multi-agent orchestration (Phase 2.3)
- `app/frontend/` → Streamlit UI
- `data/` → Sample datasets
- `docs/` → Documentation & phase plans
- `tests/` → Unit tests
