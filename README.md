# InsightForgeAI

AI-Powered Business Intelligence Assistant

## Status

Phase 1 ✅ · Phase 2.1–2.7 ✅ · **Phase 3.1 ✅ · 3.2 ✅ · 3.3 ✅**

## Phase 3 (Semantic Layer)

- **3.1** Semantic metrics (AOV-safe, governed definitions)
- **3.2** Deterministic metric → SQL compiler
- **3.3** Multi-table relationship detection, join paths, fan-out guards

See `docs/PHASE3_PLAN.md`.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env
streamlit run app/frontend/app.py
```

## Tests

```bash
pytest tests/test_semantic_layer.py tests/test_metric_compiler.py tests/test_relationships.py -q
```
