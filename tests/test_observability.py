"""
Phase 3.7 – Observability unit tests.
Run: pytest tests/test_observability.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.observability import (
    Metrics,
    RateLimiter,
    log_event,
    readiness_checks,
)


def test_metrics_record_and_snapshot():
    m = Metrics()
    m.record_request("/ask", 200, 12.5)
    m.record_request("/ask", 500, 30.0)
    m.record_llm(True)
    m.record_llm(False)
    snap = m.snapshot()
    assert snap["requests_total"] == 2
    assert snap["errors_total"] == 1
    assert snap["llm_calls"] == 2
    assert snap["llm_errors"] == 1
    assert snap["latency_ms_p50"] is not None


def test_rate_limiter_allows_burst_then_blocks():
    lim = RateLimiter(rate=1.0, capacity=2.0)
    assert lim.allow("k")[0] is True
    assert lim.allow("k")[0] is True
    allowed, retry = lim.allow("k")
    assert allowed is False
    assert retry > 0


def test_readiness_checks_structure(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("INSIGHTFORGE_API_KEYS", raising=False)
    checks = readiness_checks()
    assert checks["ready"] is True
    assert checks["degraded"] is True
    assert "degraded_reason" in checks


def test_log_event_does_not_raise(capsys):
    log_event("test_event", foo=1)
    out = capsys.readouterr().out
    assert "test_event" in out
