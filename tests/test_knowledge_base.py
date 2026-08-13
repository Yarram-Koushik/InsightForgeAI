"""Phase 4.6 – Knowledge base & RAG tests (deterministic, no LLM required)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.knowledge_base import (
    KnowledgeStore,
    chunk_text,
    extract_text_from_bytes,
)


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(workspace_id="test_kb", root=tmp_path)


def test_chunk_text_basic():
    text = "Paragraph one. " * 40 + "\n\n" + "Paragraph two about refunds. " * 30
    pieces = chunk_text(text, chunk_size=200, overlap=40)
    assert len(pieces) >= 2
    assert all(p[0].strip() for p in pieces)
    joined_len = sum(len(p[0]) for p in pieces)
    assert joined_len >= len(text) * 0.7


def test_ingest_and_retrieve_policy(store):
    policy = """
    Company Refund Policy

    Customers may request a refund within 30 days of purchase.
    Digital goods are non-refundable after download.
    Shipping costs are non-refundable unless the item is defective.
    Contact support@example.com for all refund requests.
    """
    result = store.ingest_text(policy, source="refund_policy.md")
    assert result.success
    assert result.n_chunks >= 1
    assert result.doc_id

    hits = store.retrieve("What is our refund policy for digital goods?", top_k=3)
    assert len(hits) >= 1
    assert hits[0].score > 0
    assert "refund" in hits[0].chunk.text.lower() or "digital" in hits[0].chunk.text.lower()

    out = store.answer("What's our refund policy?", top_k=3, llm_client=None)
    assert out["success"] is True
    assert out["hits"] >= 1
    assert out["citations"]
    assert "refund" in out["answer"].lower() or "chunk_id" in out["answer"].lower()
    assert out["grounding_line"]


def test_answer_refuses_when_empty(store):
    out = store.answer("What is the capital of Mars?", top_k=3, llm_client=None)
    assert out["success"] is False
    assert out["hits"] == 0
    assert "could not find" in out["answer"].lower() or "knowledge base" in out["answer"].lower()


def test_re_ingest_replaces_same_source(store):
    store.ingest_text("Version one of the SOP.", source="sop.md")
    store.ingest_text("Version two of the SOP with updated steps.", source="sop.md")
    docs = store.list_documents()
    sources = [d.get("source") for d in docs]
    assert sources.count("sop.md") == 1
    hits = store.retrieve("updated steps", top_k=2)
    assert any("Version two" in h.chunk.text for h in hits)


def test_extract_text_txt():
    data = b"Hello policy world.\nLine two."
    text, warns = extract_text_from_bytes(data, "policy.txt")
    assert "Hello policy world" in text
    assert isinstance(warns, list)


def test_delete_document(store):
    r = store.ingest_text("Temporary doc content about shipping.", source="ship.md")
    assert r.success
    removed = store.delete_document(doc_id=r.doc_id)
    assert removed >= 1
    assert store.retrieve("shipping", top_k=2) == []


def test_summary(store):
    store.ingest_text("A" * 50, source="a.md")
    s = store.summary()
    assert s["doc_count"] >= 1
    assert s["chunk_count"] >= 1
    assert s["workspace_id"] == "test_kb"
