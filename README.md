# InsightForgeAI

AI-Powered Business Intelligence Assistant

## Status

Phase 1 ✅ · Phase 2.1–2.7 ✅ · **Phase 3.1 Semantic Metric Layer ✅** · **Phase 3.2 Metric Compiler ✅**

## Vision

ChatGPT for company data using a free stack (Streamlit / FastAPI, DuckDB, LangGraph-style agents, Groq/Gemini, Prophet, Plotly).

## Phase 3 (Semantic Layer)

Governed metrics so business numbers stay consistent.

- **3.1** – Auto-discovered SemanticModel (entities, dimensions, metrics), ratio safety, prompt enrichment
- **3.2** – Deterministic MetricQuery → SQL compiler; preferred path for clear metric questions; NL→SQL fallback

See `docs/PHASE3_PLAN.md`.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env
streamlit run app/frontend/app.py
```

## Tests

```bash
pytest tests/test_semantic_layer.py tests/test_metric_compiler.py -q
```
