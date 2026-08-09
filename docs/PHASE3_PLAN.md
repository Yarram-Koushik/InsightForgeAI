# Phase 3 – Expanded Industry Roadmap (Option B)

**Status (2026-08-10)**

| Sub-phase | Focus | Status |
|-----------|--------|--------|
| **3.1** Semantic Metric Layer (registry + versioning) | Metric contract, resolver, versioned definitions | Partial (auto + governance catalog) |
| **3.2** Multi-Dataset Joins | Relationship model, join planner, fan-out guards | ✅ Done (former 3.3) |
| **3.3** Durable Workspace, Chat & Artifacts | Persist datasets, chat, evidence; restore after restart | ✅ **This delivery** |
| **3.4** Backend API Boundary (FastAPI) | API as brain, Streamlit as one client | Planned |
| **3.5** Security, Auth, Audit | Auth, roles, audit log | Planned |
| **3.6** Evaluation & Quality Gates | Real eval suite + golden questions + CI | Skeleton only |
| **3.7** Deployment, Observability & Ops | Docker, health, logs, rate limits | Planned |

---

## 3.3 Durable Workspace, Chat & Artifacts ✅

### Why
Session-only Streamlit state is not a product. Restart currently loses every dataset, every chat turn, and every evidence pack.

### Delivered

**`app/core/workspace_store.py`**
- `WorkspaceStore` – durable persistence for one workspace
- Layout under `data/workspaces/{id}/`:
  - `meta.json` – registry
  - `datasets/{name}/meta.json` + `cleaned.parquet` (+ optional raw)
  - `chat/history.jsonl` – append-only chat turns with retention
  - `catalog/` – reserved for metric overrides
- `save_dataset` / `load_dataset_record` / `delete_dataset`
- `append_chat_turn` + automatic retention (default last 100 turns)
- `load_into(workspace)` – restore all datasets + re-register in DuckDB
- `export_snapshot` / `import_snapshot` (zip)
- Corrupt-line and missing-file tolerance (never crash the app)
- Parquet preferred, CSV fallback if pyarrow unavailable

**Tests** – `tests/test_workspace_store.py` (8 passed)

### Edge cases handled
| Case | Behaviour |
|------|-----------|
| Large chat history | Trimmed to `max_chat_turns` (default 100) |
| Corrupt JSONL line | Skipped; rest of history still loads |
| Missing / corrupt parquet | Dataset skipped with error entry; app continues |
| Dataset name already in memory | Restore skipped for that name (no overwrite) |
| Export / import | Full workspace zip round-trip |

### Integration notes (Streamlit)
```python
from app.core.workspace_store import WorkspaceStore, ChatTurn, get_or_create_store

# On startup
store = get_or_create_store("default")
if "workspace" not in st.session_state:
    st.session_state.workspace = Workspace()
    summary = store.load_into(st.session_state.workspace)
    st.session_state.chat_history = [
        t.to_dict() for t in store.load_chat_history(limit=30)
    ]

# After successful dataset load + cleaning
store.save_dataset(record)

# After each chat turn
store.append_chat_turn(ChatTurn(...))
```

### Out of 3.3 / next
- Multi-workspace switcher UI
- Automatic background save of large evidence packs
- FastAPI routes that use the same store (→ 3.4)
