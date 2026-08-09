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
- LangGraph + LangChain
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
| → Sub-Phase 2.1 | 🚧 Active | DuckDB Query Engine – register cleaned tables, full SQL interface, schema inspection |
| → Sub-Phase 2.2 | Planned | LLM setup (Groq/Gemini) + Natural Language → SQL |
| → Sub-Phase 2.3 | Planned | LangGraph multi-agent orchestration |
| → Sub-Phase 2.4 | Planned | Automated Plotly visualizations |
| → Sub-Phase 2.5 | Planned | Forecasting (Prophet) & advanced analytics |
| → Sub-Phase 2.6 | Planned | Full chat UI + evidence grounding + export |

## Phase 1 Highlights

- Multi-file / multi-sheet Excel support
- Semantic type detection (Email, URL, Phone, Currency, DateTime, Identifier, Categorical, Free Text…)
- Confidence-scored safe auto-cleaning with full change lineage
- Multi-dimensional quality score (Completeness 50% + Uniqueness 30% + Validity 20%)
- Session Workspace with raw + cleaned versions of every dataset

## How to Run

```bash
# Install
uv sync   # or pip install -r requirements.txt + duckdb pandas streamlit openpyxl

# Launch
streamlit run app/frontend/app.py
```

## Project Structure

- `app/core/` → ingestion, schema, cleaning, profiling, data_manager (DuckDB)
- `app/frontend/` → Streamlit UI
- `app/agents/` → (Phase 2+) LangGraph agents
- `data/` → Sample datasets
- `docs/` → Documentation & phase plans
- `tests/` → Unit tests
