# Phase 4.5 – Enterprise Multi-User & Scheduling

**Status:** ✅ Complete (2026-08-12)

## Goals

Ship the team-product layer on top of Phases 1–4.4 **without** reworking ingestion, semantic layer, connectors, or dashboards.

| Capability | Delivered |
|------------|-----------|
| Workspaces per org/user | Durable store meta: `owner_id`, `org_id`, `display_name` + `/workspaces` API |
| Roles | Existing `viewer` / `analyst` / `admin` (API keys); schedule CRUD is **admin** |
| Saved insights | Named questions per workspace (`insights.json`) + `/insights` |
| Scheduled reports | Interval or daily-at UTC; channels: `log` / `slack` / `email` |
| Delivery | Slack webhook, SMTP email, always audited |
| Usage metrics | `queries_total`, `schedule_runs`, `schedule_failures`, token counters on `/metrics` |
| Audit surface | Existing `/audit` + schedule_* actions |

## Files

- `app/core/scheduling.py` – models, store, due logic, run, deliver, background worker
- `app/core/observability.py` – extended metrics
- `app/backend/main.py` – `/schedules`, `/workspaces`, `/insights`, startup poller
- `app/backend/schemas.py` – request/response models
- `tests/test_scheduling.py`

## Cadence model (intentionally simple)

- `interval_minutes` – every N minutes
- `daily_at` – `"HH:MM"` UTC once per day

No external cron library. Suitable for Docker single-node; scale later with an external worker if needed.

## API (admin for mutations)

```
GET  /workspaces
GET  /workspaces/{id}
GET  /schedules?workspace_id=
POST /schedules
PATCH /schedules/{workspace_id}/{id}
DELETE /schedules/{workspace_id}/{id}
POST /schedules/{workspace_id}/{id}/run
GET  /insights?workspace_id=
POST /insights
```

## Env

```bash
# Scheduler poller (default on)
INSIGHTFORGE_SCHEDULER=1
SCHEDULER_POLL_SEC=60

# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Email
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=reports@example.com
SMTP_TLS=1
REPORT_EMAIL_TO=team@example.com
```

## Done-when checklist

1. Admin creates schedule: daily "revenue by region", channel=log or slack
2. `POST .../run` or wait for next_run → artifact delivered / logged
3. `/audit` shows `schedule_create` and `schedule_run` with principal
4. `/metrics` shows `schedule_runs`

```bash
pytest tests/test_scheduling.py -q
```

## Non-goals (later)

- Full multi-tenant row-level security
- External Celery/RQ cluster
- Live Tableau/Power BI push connectors
