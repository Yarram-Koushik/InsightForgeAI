# InsightForgeAI

AI-Powered Business Intelligence Assistant

## Status

Phase 1 ✅ · Phase 2 ✅ · **Phase 3.1–3.4 ✅** · 3.5 planned

## Phase 3

- **3.1** Semantic metrics  
- **3.2** Metric → SQL compiler  
- **3.3** Multi-table relationships + fan-out guards  
- **3.4** Time intelligence (YoY / MoM / YTD / rolling)

See `docs/PHASE3_PLAN.md`.

## Tests

```bash
pytest tests/test_semantic_layer.py tests/test_metric_compiler.py tests/test_relationships.py tests/test_time_intelligence.py -q
```
