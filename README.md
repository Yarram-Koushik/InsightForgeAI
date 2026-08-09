# InsightForgeAI

AI-Powered Business Intelligence Assistant

## Status

Phase 1 ✅ · Phase 2 ✅ · **Phase 3.1–3.6 ✅** · 3.7 planned

## Phase 3 (Option B – Industry)

| Sub-phase | Status |
|-----------|--------|
| 3.1 Semantic Metric Layer | Partial |
| 3.2 Multi-Dataset Joins | ✅ |
| 3.3 Durable Workspace | ✅ |
| 3.4 FastAPI Boundary | ✅ |
| 3.5 Security, Auth, Audit | ✅ |
| 3.6 Evaluation & Quality Gates | ✅ |
| 3.7 Deployment & Ops | Planned |

See `docs/PHASE3_PLAN.md`.

## Quick start

```bash
pip install -r requirements.txt

# API
uvicorn app.backend.main:app --reload --port 8000

# UI
streamlit run app/frontend/app.py
```

## Eval (Phase 3.6)

```bash
pytest tests/test_eval_harness.py -q
python -m app.core.eval_harness --mode offline --fail-under 80
```

## Auth (Phase 3.5)

When `INSIGHTFORGE_API_KEYS` is set, send `X-API-Key: sk-change-me`.
Roles: `viewer` · `analyst` · `admin`. `/health` stays public.
