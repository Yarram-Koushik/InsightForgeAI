# InsightForgeAI

AI-Powered Business Intelligence Assistant — *ChatGPT for company data*

## Status

Phase 1 ✅ · Phase 2 ✅ · Phase 3.1–3.7 ✅ · **Phase 4.1–4.5 ✅**

## Phase 4 (Industry depth)

| Sub-phase | Status |
|-----------|--------|
| 4.1 Connectors (Postgres / MySQL, read-only, never persist secrets) | ✅ |
| 4.2 Conversational memory & citations (follow-up expand, grounding line) | ✅ |
| 4.3 Automated analytics depth (EDA pack, root-cause, what-if, RFM) | ✅ |
| 4.4 Dashboards & Export (pin widgets, refresh, PDF + PPTX) | ✅ |
| 4.5 Enterprise multi-user & scheduling (workspaces, schedules, Slack/email, audit) | ✅ |

## Phase 3 (Option B – Industry)

| Sub-phase | Status |
|-----------|--------|
| 3.1 Semantic Metric Layer | ✅ |
| 3.2 Multi-Dataset Joins | ✅ |
| 3.3 Durable Workspace | ✅ |
| 3.4 FastAPI Boundary | ✅ |
| 3.5 Security, Auth, Audit | ✅ |
| 3.6 Evaluation & Quality Gates | ✅ |
| 3.7 Deployment & Ops | ✅ |

## Quick start (local)

```bash
pip install -r requirements.txt
cp .env.example .env

uvicorn app.backend.main:app --reload --port 8000
streamlit run app/frontend/app.py
```

## Docker (Phase 3.7+)

```bash
cp .env.example .env
docker compose up --build
```

- API: http://localhost:8000/health · /ready · /metrics · /schedules
- UI:  http://localhost:8501

## Phase 4.5 – Scheduling & multi-user

Admin (role `admin`) can create schedules:

```bash
curl -X POST http://localhost:8000/schedules \
  -H "X-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{
    "name": "Daily revenue by region",
    "workspace_id": "default",
    "kind": "question",
    "question": "total revenue by region",
    "table_name": "phase3_test_orders",
    "daily_at": "08:00",
    "channel": "log"
  }'
```

- Channels: `log` | `slack` | `email`
- Run now: `POST /schedules/{workspace_id}/{id}/run`
- Audit: `GET /audit` (shows `schedule_create` / `schedule_run`)
- Metrics: `GET /metrics` → `schedule_runs`, `queries_total`

See [docs/PHASE4_5_ENTERPRISE.md](docs/PHASE4_5_ENTERPRISE.md).

```bash
pytest tests/test_scheduling.py -q
```

## Phase 4.3 – how to use analytics paths

In the **Chat & Analytics** tab (with a dataset loaded):

| Intent | Example question |
|--------|------------------|
| EDA pack | `run eda` / click **Run EDA pack** |
| Root-cause | `why did sales drop by region` |
| What-if | `+10% amount on North` |
| RFM | `show RFM segments` |

Every answer shows a **grounding line** and optional **citations**. Follow-ups like `by region` or `only North` are expanded from chat history.

## Phase 4.4 – Dashboards & Export

1. Ask a question that returns SQL / a table / a chart.
2. Click **📌 Pin to dashboard** on that turn.
3. Open the **📌 Dashboard** tab → refresh widgets, remove, or export.
4. **Export PDF** or **Export PPTX** for shareable reports.

Widgets persist under `data/workspaces/{id}/dashboard/widgets.json` (metadata only; refresh re-runs SQL).

```bash
pytest tests/test_dashboard.py -q
```

## Eval (Phase 3.6)

```bash
pytest tests/test_eval_harness.py -q
pytest tests/test_phase43_analytics.py -q
python -m app.core.eval_harness --mode offline --fail-under 80
```

## Auth (Phase 3.5)

```bash
INSIGHTFORGE_API_KEYS=admin1:admin:sk-change-me
```

Send `X-API-Key: sk-change-me`. Roles: viewer · analyst · admin.
