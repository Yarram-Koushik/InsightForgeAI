# Phase 4.4 – Dashboards & Export

**Status:** Implemented on main  
**Date:** 2026-08-11

## Goal

Conversations become durable, shareable artifacts.

- Pin any successful chat turn (SQL + insight + chart metadata) to a workspace dashboard
- Dashboard page: list widgets, refresh against current data, remove
- Export: **PDF** (reportlab) + **PPTX** (python-pptx)
- Stale detection when dataset disappears or SQL fails after schema change

## Delivered

| Component | Path |
|-----------|------|
| Widget model + pin/refresh/persist | `app/core/dashboard.py` |
| PDF + PPTX builders | `app/core/export.py` (`build_dashboard_pdf`, `build_dashboard_pptx`) |
| Streamlit Dashboard tab + Pin buttons | `app/frontend/app.py` |
| Tests | `tests/test_dashboard.py` |
| Deps | `reportlab`, `python-pptx` in `requirements.txt` |

## Storage

```
data/workspaces/{workspace_id}/dashboard/widgets.json
```

Widgets store **metadata only** (question, SQL, insight, chart_type, grounding, status).  
Figures and full DataFrames are **not** persisted; refresh re-executes SQL via the existing guarded `Workspace.execute_sql`.

## How to use

1. Ask a question in **Chat & Analytics** that produces SQL / a table / a chart.
2. Click **📌 Pin to dashboard** on that turn.
3. Open the **📌 Dashboard** tab.
4. **Refresh** individual widgets or **Refresh all**.
5. **Export PDF** or **Export PPTX**.

## Edge cases handled

| Case | Behaviour |
|------|-----------|
| Dataset removed from workspace | Widget status → `stale` with clear message |
| SQL fails after schema change | status → `stale`, error shown |
| No SQL on turn | Pin still allowed; refresh reports "No SQL stored" |
| Empty dashboard | Clean empty state + export still produces a title-only report |

## Done-when criteria

- [x] Pin 2–3 widgets from one conversation
- [x] Open Dashboard tab and see the grid
- [x] Refresh re-runs SQL against current DuckDB tables
- [x] Export produces downloadable PDF and PPTX
- [x] No secrets or full result frames written to disk

## Explicit non-goals (this sub-phase)

- Live Power BI / Tableau connector (export CSV + metric defs is future)
- Drag-and-drop grid layout / free-form canvas
- Embedding interactive Plotly into PDF (text + SQL evidence first)
