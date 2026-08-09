# InsightForgeAI

**AI-Powered Business Intelligence Assistant**

Upload company data → Ask in plain English → Get SQL, charts, forecasts and insights.

## Current Status

| Phase | Status |
|-------|--------|
| Phase 0–1 | ✅ Complete |
| Phase 2.1 DuckDB | ✅ Complete |
| Phase 2.2 NL→SQL | ✅ Complete |
| Phase 2.3 Multi-agent | ✅ Complete |
| Phase 2.4 Visualizations | ✅ Complete |
| Phase 2.5 Forecasting & Analytics | ✅ Complete |
| Phase 2.6 Full Chat + Export | Planned |

## Phase 2.5 Highlights

- Time-series forecast (Prophet optional, baseline always available)
- Trend direction + relative change summary
- Anomaly flags (z-score vs trend)
- Horizon parsing (“next 30 days”, “next month”)
- Clear skip when no time column / too few points

## How to Run

```bash
pip install -r requirements.txt
# optional: pip install prophet
cp .env.example .env
streamlit run app/frontend/app.py
```

## Project Structure

- `app/core/` – ingestion, cleaning, DuckDB, NL→SQL, visualization, **analytics**
- `app/agents/` – router, SQL, insight, clarify, viz, **forecast**, orchestrator
- `app/frontend/` – Streamlit UI
