# InsightForgeAI

AI-Powered Business Intelligence Assistant

## Status

Phase 1 ✅ · Phase 2.1–2.7 ✅ (incl. production hardening) · **Phase 3.1 Semantic Metric Layer 🚧**

## Vision

ChatGPT for company data using a free stack (Streamlit / FastAPI, DuckDB, LangGraph-style agents, Groq/Gemini, Prophet, Plotly).

## Phase 3.1 – Semantic Metric Layer

First-class governed metrics on top of the Phase-1 cleaned tables:

- Auto-discovered **SemanticModel** (entities, dimensions, metrics)
- Metric kinds: SUM, COUNT, COUNT DISTINCT, AVG, MIN, MAX, **RATIO**
- Explicit **additivity** (full / semi / non) – ratios are never naïvely averaged
- Question → metric resolution with confidence + reason
- NL→SQL prompt enrichment so the LLM cannot invent wrong aggregations (especially AOV)
- Null-safe division (`NULLIF`) and ID-column exclusion

See `docs/PHASE3_PLAN.md` for the full Phase 3 roadmap.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env
streamlit run app/frontend/app.py
```

## Tests

```bash
pytest tests/test_semantic_layer.py -q
```
