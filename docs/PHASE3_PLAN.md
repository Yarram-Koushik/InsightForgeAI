# Phase 3 – Expanded Industry Roadmap (Option B)

**Status (2026-08-10)**

| Sub-phase | Focus | Status |
|-----------|--------|--------|
| **3.1** Semantic Metric Layer | Metric registry, versioning, resolver | Partial (auto + governance) |
| **3.2** Multi-Dataset Joins | Relationship model + fan-out guards | ✅ Done |
| **3.3** Durable Workspace, Chat & Artifacts | Persist + restore datasets & chat | ✅ Done |
| **3.4** Backend API Boundary (FastAPI) | API as brain, Streamlit as one client | ✅ Done |
| **3.5** Security, Auth, Audit | API keys, roles, audit log, upload hardening | ✅ Done |
| **3.6** Evaluation & Quality Gates | Real eval suite + CI scorecard | ✅ **This delivery** |
| **3.7** Deployment, Observability & Ops | Docker, health, logs, rate limits | Planned |

---

## 3.6 Evaluation & Quality Gates ✅

### Delivered

**`app/core/eval_harness.py`** (expanded from Phase 2.7 skeleton)
- `EvalCase` / `EvalResult` / `Scorecard` models
- Golden bank (~15 cases) across domains: revenue, students, support, meta, clarify, forecast
- Offline scorer (intent + SQL shape allowlists) – **no LLM keys required**
- Heuristic intent + synthetic SQL for CI-stable checks
- Optional live path (orchestrator) with infra-vs-product failure split
- CLI: `python -m app.core.eval_harness --mode offline --fail-under 80`
- Scorecard: pass rate, by-intent, by-domain, failure list

**`tests/test_eval_harness.py`**
**`.github/workflows/eval.yml`** – runs offline suite on push/PR

### Run locally

```bash
pytest tests/test_eval_harness.py -q
python -m app.core.eval_harness --mode offline --fail-under 80
python -m app.core.eval_harness --json
```

### Design choices
| Concern | Approach |
|---------|----------|
| LLM non-determinism | Fragment allowlists, not exact SQL equality |
| Schema drift | Offline mode does not need real tables |
| API / LLM outage | Marked as `infra` errors, not product fails |
| CI | Offline suite only; live is opt-in |

### Out of 3.6 / next
- Larger domain-specific golden sets checked into `tests/fixtures/`
- Live eval job with secrets + sample Parquet
- Deployment & ops (3.7)
