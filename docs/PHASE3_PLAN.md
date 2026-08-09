# Phase 3 – Semantic Layer & Governed Metrics

**Status (2026-08-09)**  
| Sub-Phase | Focus | Status |
|-----------|--------|--------|
| **3.1 Semantic Metric Layer** | First-class metrics, dimensions, entities, auto-discovery, ratio safety, prompt enrichment | 🚧 In progress |
| 3.2 Metric Compiler & Deterministic Query | Compile MetricQuery → safe SQL (grain-aware) | Planned |
| 3.3 Multi-table Relationships | Entities, join paths, fan-out guards | Planned |
| 3.4 Time Intelligence | Period-over-period, YTD, rolling windows | Planned |
| 3.5 Metric Governance UI | Browse / override / save metric definitions | Planned |

## Why Phase 3

Phase 2 answers questions with free-form NL→SQL. That is powerful but still fragile for business-critical numbers:

- “Average order value” can be wrongly emitted as `AVG(amount)` instead of `SUM / COUNT DISTINCT`
- ID columns get summed
- Ratios are re-aggregated incorrectly across dimensions
- Different users get different definitions for the same business concept

A **Semantic Metric Layer** sits between the cleaned tables (Phase 1) and the agents (Phase 2). It defines *what* Revenue, AOV, Unique Customers, Conversion Rate mean once, then every agent and SQL path reuses the same definitions.

## Design Principles (Industry)

1. **Metrics are first-class** – not just column hints.
2. **Additivity is explicit** – full / semi / non-additive; ratios are never naïvely averaged.
3. **Null-safe arithmetic** – every division uses `NULLIF(..., 0)`.
4. **IDs are never measures** – enforced by semantic type + name heuristics.
5. **Transparent** – every resolved metric can explain its SQL expression and reason.
6. **Backward compatible** – existing Phase 2 agents keep working; the layer only *enriches*.
7. **Free-stack** – pure Python + dataclasses + DuckDB; no external metric server required for 3.1.

## 3.1 Scope (this delivery)

### Delivered

- `app/core/semantic_layer.py`
  - `Entity`, `Dimension`, `Metric`, `SemanticModel` dataclasses
  - Aggregation kinds: `SUM | COUNT | COUNT_DISTINCT | AVG | MIN | MAX | RATIO | EXPRESSION`
  - Additivity: `FULL | SEMI | NON`
  - Auto-builder from Phase-1 semantic schema + column-name heuristics
  - Built-in business metric templates (revenue, orders, AOV, unique entities, …)
  - Question → metric resolution with confidence and reason
  - Prompt block generator for NL→SQL (replaces / upgrades Phase 2.7 `metrics.py` hints)
  - Safe SQL expression helpers (`nullif`, quoted identifiers)
  - Edge-case guards (empty table, no numeric measures, all-ID tables, high-cardinality dims)

- Integration
  - `nl_to_sql.build_schema_context` now appends a **SEMANTIC METRICS** section
  - Stronger system-prompt rules for ratio and distinct metrics
  - `metrics.py` remains as a thin compatibility façade

- Docs & tests
  - This plan
  - `tests/test_semantic_layer.py` covering core edge cases

### Explicitly out of 3.1

- Persistent user-defined metrics (YAML / UI) → 3.5
- Multi-table joins / relationship graph → 3.3
- Full deterministic MetricQuery compiler (still LLM for complex filters) → 3.2
- Time intelligence (PoP, YTD) → 3.4

## Edge Cases Handled in 3.1

| Case | Behaviour |
|------|-----------|
| Table with only ID columns | No SUM/AVG metrics proposed; only counts |
| Ratio metric (AOV) | SQL always `SUM(x) / NULLIF(COUNT(DISTINCT y), 0)` |
| User asks “average of AOV by region” | Resolver flags non-additivity; prompt warns LLM |
| Missing value / entity columns | Metric skipped with reason |
| High-cardinality free-text | Not promoted to dimension |
| Currency-looking columns | Preferred as revenue measure |
| Empty or single-row table | Model still builds; metrics marked low-confidence |
| Conflicting column names | Prefer higher semantic confidence + name match |

## Success Criteria for 3.1

- [x] `build_semantic_model(workspace, table)` returns a populated model for any Phase-1 cleaned dataset
- [x] `resolve_metrics_for_question` returns ranked metrics with reasons
- [x] NL→SQL prompt contains explicit metric expressions and “never AVG a ratio” rules
- [x] Existing Phase 2 flows continue to work without regression
- [x] Unit tests cover ratio safety, ID exclusion, empty schema
