# Phase 3 – Expanded Industry Roadmap (Option B)

**Status (2026-08-10)**

| Sub-phase | Focus | Status |
|-----------|--------|--------|
| **3.1** Semantic Metric Layer | Metric registry, versioning, resolver | Partial (auto + governance) |
| **3.2** Multi-Dataset Joins | Relationship model + fan-out guards | ✅ Done |
| **3.3** Durable Workspace, Chat & Artifacts | Persist + restore datasets & chat | ✅ Done |
| **3.4** Backend API Boundary (FastAPI) | API as brain, Streamlit as one client | ✅ Done |
| **3.5** Security, Auth, Audit | API keys, roles, audit log, upload hardening | ✅ **This delivery** |
| **3.6** Evaluation & Quality Gates | Real eval suite + CI | Skeleton |
| **3.7** Deployment, Observability & Ops | Docker, health, logs, rate limits | Planned |

---

## 3.5 Security, Auth & Audit ✅

### Delivered

**`app/core/security.py`**
- API-key auth via `INSIGHTFORGE_API_KEYS` env
- Roles: `viewer` < `analyst` < `admin`
- Fail-closed when keys configured
- Append-only JSONL audit log under `data/audit/audit-YYYY-MM-DD.jsonl`
- Upload hardening: size (50 MB), extension allow-list
- `generate_api_key()` helper

**API wiring (`app/backend/main.py`)**
- `X-API-Key` or `Authorization: Bearer <key>`
- `/health` always public
- `/datasets`, `/schema`, `/sql` → viewer+
- `/ask`, `/upload` → analyst+
- `GET /audit` → admin only
- Every ask / sql / upload writes an audit event

**Tests** – `tests/test_security.py`

### Enable auth

```bash
# .env
INSIGHTFORGE_API_KEYS=admin1:admin:sk-your-secret,analyst1:analyst:sk-analyst-secret
```

When `INSIGHTFORGE_API_KEYS` is empty, the API stays open (local development).

### Edge cases
| Case | Behaviour |
|------|-----------|
| No keys configured | Open mode |
| Missing / wrong key | 401 UNAUTHORIZED |
| Viewer calls /ask | 403 FORBIDDEN |
| Oversized / bad extension upload | 413 / 415 |
| Audit write fails | Swallowed – never breaks the request |
