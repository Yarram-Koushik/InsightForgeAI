# Phase 4.6 – Knowledge Base, RAG & Proactive Insights

**Status:** ✅ Complete

## Why

Moves InsightForgeAI beyond pure tabular NL→SQL into a hybrid analyst that can answer policy/SOP questions with **citations**, and proactively surface unusual patterns against a recent baseline.

## Deliverables

| Capability | Module | Notes |
|------------|--------|--------|
| Document ingest (PDF / MD / TXT) | `app/core/knowledge_base.py` | Workspace-scoped under `data/workspaces/{id}/knowledge/` |
| Chunking + pure-Python TF-IDF retrieval | same | No heavy embedding model; pypdf optional for PDF |
| Grounded answers with chunk citations | `KnowledgeStore.answer` | Refuses when no relevant chunks |
| Proactive scan (7-period baseline + residual z) | `app/core/proactive.py` | Deterministic cards: watch / alert |
| Router intents | `KNOWLEDGE`, `PROACTIVE` | Heuristic + LLM |
| Orchestrator paths | knowledge / proactive (no SQL) | Citations + grounding_line |
| Tests | `tests/test_knowledge_base.py`, `tests/test_proactive.py` | Offline, no API keys |

## Usage

### Knowledge base

1. Upload PDF / Markdown / TXT policies under the workspace knowledge path (or via UI when wired).
2. Ask e.g. *What's our refund policy?*
3. Answer shows **document citations** (source + chunk_id + excerpt) and a grounding line.

### Proactive scan

Ask any of:

- *Anything unusual in orders?*
- *What should I watch?*
- *Scan for anomalies*

Cards appear with severity, summary, and a suggested follow-up question.

## Edge cases handled

- No matching document → clear refuse (does not invent policy).
- PDF without extractable text → warning, no crash.
- Re-upload same filename → replaces previous chunks for that source.
- Sparse / short series → softer limited-history card or empty result.
- Missing time column → optional distribution outlier path only.

## Tests

```bash
pytest tests/test_knowledge_base.py tests/test_proactive.py -q
```
