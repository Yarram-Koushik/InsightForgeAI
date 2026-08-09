# Phase 2 – Intelligent Analysis Layer

| Sub-Phase | Status |
|-----------|--------|
| 2.1 DuckDB Query Engine | ✅ |
| 2.2 Natural Language → SQL | ✅ |
| 2.3 Multi-Agent Orchestration | ✅ |
| 2.4 Automated Visualizations | ✅ |
| 2.5 Forecasting & Advanced Analytics | ✅ |
| 2.6 Full Chat + Evidence + Export | Planned |

## 2.5 Forecasting

- `app/core/analytics.py` – forecast, trend, anomalies, correlation
- `app/agents/forecast_agent.py`
- Intent: `forecast`
- Prophet optional; baseline always works
- Edge cases: no time column, <5 points, constant series, missing Prophet

## Next: 2.6
Conversational history, evidence packs, CSV/PNG/PDF export.
