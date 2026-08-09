# Phase 3 – Semantic Layer & Governed Metrics

**Status (2026-08-10)**  
| Sub-Phase | Focus | Status |
|-----------|--------|--------|
| **3.1 Semantic Metric Layer** | First-class metrics, dimensions, entities, auto-discovery, ratio safety, prompt enrichment | ✅ Done |
| **3.2 Metric Compiler & Deterministic Query** | Compile MetricQuery → safe SQL (grain-aware) | ✅ Done |
| **3.3 Multi-table Relationships** | Entities, join paths, fan-out guards | ✅ Done |
| 3.4 Time Intelligence | Period-over-period, YTD, rolling windows | Planned |
| 3.5 Metric Governance UI | Browse / override / save metric definitions | Planned |

## Why Phase 3

Phase 2 answers questions with free-form NL→SQL. That is powerful but still fragile for business-critical numbers. Phase 3 adds governed metrics, deterministic compilation, and safe multi-table joins.

## Design Principles (Industry)

1. Metrics are first-class with explicit additivity.
2. Null-safe arithmetic (`NULLIF`).
3. IDs are never measures.
4. Deterministic compile when intent is clear; LLM fallback otherwise.
5. Joins only along detected relationships; fan-out is warned/blocked for metrics.
6. Backward compatible free-stack (Python + DuckDB).

---

## 3.1 Semantic Metric Layer ✅
See prior notes. `app/core/semantic_layer.py` + tests (10).

## 3.2 Metric Compiler ✅
`app/core/metric_compiler.py` + tests (16). Preferred path inside `nl_to_sql.ask()`.

## 3.3 Multi-table Relationships ✅

### Delivered
- `app/core/relationships.py`
  - `Relationship`, `RelationshipGraph`, `JoinPath`, `JoinStep`
  - Auto-detect via column-name heuristics + value overlap
  - Cardinality inference (1:1, 1:N, N:1, N:N); prefer N:1 orientation
  - Shortest join-path search with fan-out marking
  - `compile_join_sql` with optional fan-out block
  - Prompt block for NL→SQL
- Integration: `build_schema_context` appends RELATIONSHIPS when ≥2 tables loaded
- System prompt JOIN rules (explicit ON, no Cartesian, avoid 1:N when aggregating)
- Tests: `tests/test_relationships.py` (10 passed)

### Edge cases
| Case | Behaviour |
|------|-----------|
| Single table loaded | No relationships; warning; single-table path unchanged |
| orders.customer_id ↔ customers.customer_id | Detected; oriented N:1 when possible |
| Unrelated tables | No false links above confidence threshold |
| customers → orders (1:N) | Fan-out risk flagged; can block compile |
| No path within max hops | `find_join_path` returns None |

### Out of scope for 3.3
- User-defined manual relationships UI → 3.5
- Multi-hop metric compiler (compile across joins) → can extend in 3.4/3.5
- Period-over-period → 3.4

---

## Success criteria progress
- [x] 3.1–3.3 core modules + tests
- [ ] 3.4 time intelligence
- [ ] 3.5 governance UI
