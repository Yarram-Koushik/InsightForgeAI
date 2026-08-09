# Phase 3 – Semantic Layer & Governed Metrics

**Status (2026-08-10)**  
| Sub-Phase | Focus | Status |
|-----------|--------|--------|
| **3.1 Semantic Metric Layer** | First-class metrics, dimensions, entities, auto-discovery, ratio safety, prompt enrichment | ✅ Done |
| **3.2 Metric Compiler & Deterministic Query** | Compile MetricQuery → safe SQL (grain-aware) | ✅ Done |
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
7. **Free-stack** – pure Python + dataclasses + DuckDB; no external metric server required.
8. **Deterministic when possible** – compiler preferred over LLM for clear metric intents (3.2).

---

## 3.1 Semantic Metric Layer ✅

- `app/core/semantic_layer.py` – Entity, Dimension, Metric, SemanticModel, auto-builder, resolver, prompt blocks
- `metrics.py` – compatibility façade
- NL→SQL prompt enrichment with governed expressions
- Tests: `tests/test_semantic_layer.py` (10 passed)

---

## 3.2 Metric Compiler & Deterministic Query ✅

### Delivered

- `app/core/metric_compiler.py`
  - `MetricQuery` – metrics, dimensions, filters, time grain, order, limit
  - `compile_metric_query(model, query)` → grain-aware DuckDB SQL
  - `try_compile_from_question(question, model)` – NL bridge when intent is clear
  - Time grains: day / week / month / quarter / year via `DATE_TRUNC`
  - Safe filter literals (quote-escape + strip separators/comments)
  - NON-additive warnings when ratios are grouped
  - High-cardinality dimension warnings
  - Limit capping

- Integration
  - `nl_to_sql.ask()` tries the deterministic compiler **first**
  - On success → execute and return (no LLM call)
  - On miss / failure → seamless fallback to existing NL→SQL path

- Tests: `tests/test_metric_compiler.py` (16 passed)

### Edge cases handled in 3.2

| Case | Behaviour |
|------|-----------|
| Unknown metric name | Compile fails closed with clear error |
| AOV by region | SQL is ratio at GROUP BY grain + NON-additive warning |
| time_grain without time column | Warning; grain ignored |
| Filter value with `; DROP --` | Separators/comments stripped; value stays a string literal |
| limit=999999 | Capped to max_limit (default 5000) |
| Ambiguous NL question | `try_compile_from_question` returns success=False → NL→SQL fallback |
| Empty metric list | Fail closed |

### Explicitly out of 3.2

- Multi-table joins → 3.3
- Period-over-period / YTD → 3.4
- User-editable metric catalog UI → 3.5

---

## Success criteria progress

- [x] 3.1 model builds + resolves + enriches prompts
- [x] 3.2 compiler produces correct grain-aware SQL for known metrics
- [x] 3.2 preferred path in `ask()` with safe fallback
- [x] Unit tests for compiler edge cases
- [ ] 3.3 multi-table relationships
- [ ] 3.4 time intelligence
- [ ] 3.5 governance UI
