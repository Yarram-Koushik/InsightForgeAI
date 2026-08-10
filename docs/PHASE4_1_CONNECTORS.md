# Phase 4.1 – Live Data Connectors

**Status:** Implementation complete (ready to merge)  
**Date:** 2026-08-10

## Goal

Turn InsightForgeAI from a file-upload demo into a platform that can also talk to live databases — without changing the existing Workspace / DatasetRecord / DuckDB / NL→SQL path.

## Delivered

| Component | Path | Notes |
|-----------|------|-------|
| Abstract interface | `app/core/connectors/base.py` | `ConnectionConfig`, `TableInfo`, `BaseConnector` |
| PostgreSQL | `app/core/connectors/postgres.py` | psycopg2, read-only, schema introspect, sample, soft 500k row ceiling |
| MySQL / MariaDB | `app/core/connectors/mysql.py` | pymysql, same contract |
| Factory + register | `app/core/connectors/__init__.py` | `create_connector`, `register_table_as_dataset` |
| UI helpers | `app/core/connectors/ui_helpers.py` | form → config, test+list, bulk load |
| Tests | `tests/test_connectors.py` | 12 unit tests (mocked), optional live |

## How it plugs into the existing product

1. User fills connection form (or sets env vars).
2. `test_connection()` → list tables.
3. User picks 1–N tables → `register_table_as_dataset(...)`.
4. That function:
   - loads a DataFrame (with LIMIT safety),
   - creates a normal `DatasetRecord` via `Workspace.add_dataset`,
   - runs the same safe cleaning pipeline as file uploads,
   - registers into DuckDB,
   - writes lineage `loaded_from_connector` + metadata (`source_type=connector`).
5. From that point the table is identical to a CSV/Excel dataset for NL→SQL, metrics, governance, chat, durable store.

## Credentials (never in git)

**Preferred for demos / CI**

```bash
# PostgreSQL
export POSTGRES_URL=postgresql://user:pass@host:5432/dbname
# or discrete
export POSTGRES_HOST=...
export POSTGRES_PORT=5432
export POSTGRES_DB=...
export POSTGRES_USER=...
export POSTGRES_PASSWORD=...
export POSTGRES_SSLMODE=prefer   # optional

# MySQL
export MYSQL_URL=mysql://user:pass@host:3306/dbname
# or MYSQL_HOST / MYSQL_PORT / MYSQL_DB / MYSQL_USER / MYSQL_PASSWORD
```

**UI form**

Password is held only in Streamlit session state (or process memory).  
`ConnectionConfig.public_dict()` never serialises the password.  
Durable workspace meta stores only the registered dataset (parquet), not the live credential.

## Edge cases handled

| Case | Behaviour |
|------|-----------|
| Wrong password | Clear “Authentication failed…” – no stack trace |
| Host unreachable | “Could not reach the database host…” |
| Missing DB | “Database or schema does not exist…” |
| Huge table | Soft LIMIT 500_000 on full load; UI can pass a lower limit |
| Unsafe WHERE | Rejected if it contains `;` or SQL comments |
| Empty table | Still registered so schema is visible |
| Driver missing | RuntimeError with install hint (`pip install psycopg2-binary` / `pymysql`) |

## Streamlit UI integration (minimal patch)

Add an expander in the sidebar (after the file uploader block).  
See `docs/UI_SIDEBAR_SNIPPET.py` for a drop-in fragment that:

- collects host / port / db / user / password,
- tests connection,
- lists tables with row estimates,
- lets the user multi-select and load,
- calls the same `_persist_dataset` used by file upload so durable workspace keeps working.

## Dependencies to add

```
psycopg2-binary>=2.9
pymysql>=1.1
```

Add to `requirements.txt` and (optionally) `pyproject.toml`.

## Done-when criteria (from roadmap)

- [x] Connector interface: connect → preview → register as dataset  
- [x] PostgreSQL + MySQL first  
- [x] Credentials via env / secrets, never in git  
- [x] Read-only queries, schema introspect, sample rows  
- [x] Connector health + last-sync metadata  
- [x] UI path documented (Add connection → list tables → load)  
- [x] Same pipeline as Excel after load (NL question works unchanged)

## Explicit non-goals (this sub-phase)

- BigQuery / Snowflake / Sheets / Notion / Airtable (same interface later)
- Persistent password vault / secret manager (enterprise 4.5)
- Live query push-down without materialising into DuckDB (future optimisation)

## Suggested commit message

```
feat(4.1): live Postgres + MySQL connectors → register as workspace datasets

- BaseConnector + ConnectionConfig (password never persisted)
- Postgres (psycopg2) and MySQL (pymysql) drivers, read-only
- register_table_as_dataset reuses DatasetRecord + cleaning + DuckDB
- 12 unit tests; optional live path via INSIGHTFORGE_LIVE_DB=1
```
