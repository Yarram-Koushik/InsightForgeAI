# InsightForgeAI

AI-Powered Business Intelligence Assistant

## Status

Phase 1 ✅ · Phase 2 ✅ · **Phase 3.1–3.5 ✅** · 3.6–3.7 planned

## Phase 3 (Option B – Industry)

| Sub-phase | Status |
|-----------|--------|
| 3.1 Semantic Metric Layer | Partial |
| 3.2 Multi-Dataset Joins | ✅ |
| 3.3 Durable Workspace | ✅ |
| 3.4 FastAPI Boundary | ✅ |
| 3.5 Security, Auth, Audit | ✅ |
| 3.6 Evaluation suite | Skeleton |
| 3.7 Deployment & Ops | Planned |

See `docs/PHASE3_PLAN.md`.

## Quick start

```bash
pip install -r requirements.txt
# optional auth – set in .env:
# INSIGHTFORGE_API_KEYS=admin1:admin:sk-change-me

# API
uvicorn app.backend.main:app --reload --port 8000

# UI
streamlit run app/frontend/app.py
```

## Tests

```bash
pytest tests/ -q
```

## Auth (Phase 3.5)

When `INSIGHTFORGE_API_KEYS` is set, send:

```
X-API-Key: sk-change-me
```

or `Authorization: Bearer sk-change-me`.

Roles: `viewer` · `analyst` · `admin`. `/health` stays public.
