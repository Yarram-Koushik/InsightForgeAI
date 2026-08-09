# Phase 3 – Expanded Industry Roadmap (Option B)

**Status (2026-08-10)**

| Sub-phase | Focus | Status |
|-----------|--------|--------|
| **3.1** Semantic Metric Layer | Metric registry, versioning, resolver | Partial (auto + governance) |
| **3.2** Multi-Dataset Joins | Relationship model + fan-out guards | ✅ Done |
| **3.3** Durable Workspace, Chat & Artifacts | Persist + restore datasets & chat | ✅ Done |
| **3.4** Backend API Boundary (FastAPI) | API as brain, Streamlit as one client | ✅ Done |
| **3.5** Security, Auth, Audit | API keys, roles, audit log, upload hardening | ✅ Done |
| **3.6** Evaluation & Quality Gates | Real eval suite + CI scorecard | ✅ Done |
| **3.7** Deployment, Observability & Ops | Docker, health/ready, logs, rate limits | ✅ **This delivery** |

---

## 3.7 Deployment, Observability & Ops ✅

### Delivered

**Docker**
- `docker/Dockerfile` – API image
- `docker/Dockerfile.ui` – Streamlit UI image
- `docker-compose.yml` – api + ui + shared volume
- `.dockerignore`

**Observability (`app/core/observability.py`)**
- Structured JSON logs to stdout
- In-process metrics (latency p50/p95, counters)
- Token-bucket rate limiter
- Readiness checks (degraded without LLM keys)

**API**
- `GET /health` – liveness
- `GET /ready` – readiness
- `GET /metrics` – snapshot
- Middleware: rate limit, request logging, metrics

**Tests** – `tests/test_observability.py`

### Deploy

```bash
cp .env.example .env
docker compose up --build
```

### Phase 3 complete (Option B core)

3.1 remains partially open (full versioned metric registry). Everything else in the industry roadmap is delivered.
