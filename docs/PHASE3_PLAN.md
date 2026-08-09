# Phase 3 – Semantic Layer & Governed Metrics

**Status (2026-08-10)**  
| Sub-Phase | Focus | Status |
|-----------|--------|--------|
| **3.1 Semantic Metric Layer** | Governed metrics, ratio safety, prompt enrichment | ✅ Done |
| **3.2 Metric Compiler** | Deterministic MetricQuery → SQL | ✅ Done |
| **3.3 Multi-table Relationships** | Join paths, fan-out guards | ✅ Done |
| **3.4 Time Intelligence** | PoP, YoY, MoM, YTD, rolling windows | ✅ Done |
| 3.5 Metric Governance UI | Browse / override / save metric definitions | Planned |

## 3.4 Time Intelligence ✅

### Delivered
- `app/core/time_intelligence.py`
  - Comparison kinds: period-over-period, YoY, MoM, WoW, YTD, rolling
  - `compile_time_intel` → DuckDB SQL with current/previous/delta/growth_pct
  - Null-safe growth (`NULLIF`)
  - NL intent parser (`ytd`, `vs last month`, `rolling 14 days`, …)
  - `try_compile_time_intel_from_question` bridge
  - Prompt block for LLM fallback
- Integration: `nl_to_sql.ask()` tries TI after metric compiler, before pure LLM
- Tests: `tests/test_time_intelligence.py` (13 passed)

### Edge cases
| Case | Behaviour |
|------|-----------|
| No time column | Fail closed with clear error |
| Non-TI question | Intent parser returns None → no interference |
| Divide by zero previous | growth_pct uses NULLIF |
| Rolling N | Capped 1–365 periods |

### Out of 3.4
- Fiscal calendars / custom calendars → future
- Governance UI → 3.5
