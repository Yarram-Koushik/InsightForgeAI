# Phase 3 – Expanded Industry Roadmap (Option B)

**Status (2026-08-10)**

| Sub-phase | Focus | Status |
|-----------|--------|--------|
| **3.1** Semantic Metric Layer | Metric registry, versioning, resolver, contract | ✅ **Complete** |
| **3.2** Multi-Dataset Joins | Relationship model + fan-out guards | ✅ Done |
| **3.3** Durable Workspace, Chat & Artifacts | Persist + restore datasets & chat | ✅ Done |
| **3.4** Backend API Boundary (FastAPI) | API as brain, Streamlit as one client | ✅ Done |
| **3.5** Security, Auth, Audit | API keys, roles, audit log, upload hardening | ✅ Done |
| **3.6** Evaluation & Quality Gates | Real eval suite + CI scorecard | ✅ Done |
| **3.7** Deployment, Observability & Ops | Docker, health/ready, logs, rate limits | ✅ Done |

---

## 3.1 Semantic Metric Layer ✅ (completed)

### Delivered

**`app/core/semantic_layer.py`**
- Entities, Dimensions, Metrics with full contract fields
- Auto-discovery from Phase-1 schema
- `version`, `owner`, `grain`, `domain`, `required_columns` on every Metric
- AOV is a governed ratio (`SUM / NULLIF(COUNT DISTINCT, 0)`), never AVG

**`app/core/metric_contract.py`** (new)
- `resolve_metric_contract()` with statuses:
  - `RESOLVED` – single clear winner
  - `AMBIGUOUS` – same label / competing domains → clarify questions
  - `CANNOT_COMPUTE` – required columns missing
  - `NO_MATCH`
- `metric_required_columns` / `missing_columns_for_metric`
- `bump_metric_version` – history when SQL/grain/domain changes
- `resolution_prompt_block` for NL→SQL

**`app/core/metric_governance.py`**
- Version-aware overrides (history[], version++)
- Owner / domain / grain / required_columns persisted in catalog JSON

**Tests** – `tests/test_metric_contract.py` (9 passed)

### Done-when checks

| Criterion | Status |
|-----------|--------|
| “Average order value” uses registry definition | ✅ resolves to `aov` ratio SQL |
| Versioned definitions | ✅ version + history on override |
| Same label, different defs (finance vs ops) | ✅ AMBIGUOUS + clarify |
| Missing columns → cannot compute | ✅ CANNOT_COMPUTE + message |
| Conflicting metrics → clarify, don’t guess | ✅ AMBIGUOUS status |

### Phase 3 complete

All Option B sub-phases are delivered.
