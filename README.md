# InsightForgeAI

**AI-Powered Business Intelligence Assistant**

Upload company data → Ask in plain English → Get SQL, charts, forecasts, evidence packs and exports.

## Current Status

| Phase | Status |
|-------|--------|
| Phase 0–1 | ✅ Complete |
| Phase 2.1 DuckDB | ✅ Complete |
| Phase 2.2 NL→SQL | ✅ Complete |
| Phase 2.3 Multi-agent | ✅ Complete |
| Phase 2.4 Visualizations | ✅ Complete |
| Phase 2.5 Forecasting & Analytics | ✅ Complete |
| Phase 2.6 Full Chat · Evidence · Export | ✅ Complete |

## Phase 2.6 Highlights

- Conversational chat history (session-scoped, per dataset)
- Full evidence pack per answer (JSON + Markdown download)
- Export result CSV, forecast CSV, chart HTML (PNG optional via kaleido)
- Clear chat · history capped at 30 turns for memory safety

## How to Run

```bash
pip install -r requirements.txt
# optional: pip install prophet kaleido
cp .env.example .env
streamlit run app/frontend/app.py
```

## Project Structure

- `app/core/` – ingestion, cleaning, DuckDB, NL→SQL, visualization, analytics, **export**
- `app/agents/` – router, SQL, insight, clarify, viz, forecast, orchestrator
- `app/frontend/` – Streamlit UI
