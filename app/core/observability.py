"""
InsightForgeAI – Observability helpers (Phase 3.7)

- Structured request logging (JSON lines to stdout)
- In-process metrics counters (latency, status, provider hints)
- Simple token-bucket rate limiter (per IP / API key)
- Readiness checks (workspace, optional LLM keys)

No external APM required — logs are machine-parseable for any log drain.
"""

from __future__ import annotations

import json
import os
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional, Tuple


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log_event(event: str, **fields: Any) -> None:
    payload = {"ts": now_iso(), "event": event, **fields}
    try:
        print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
    except Exception:
        print(f"{payload.get('ts')} event={event}", flush=True)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests_total = 0
        self.requests_by_status: Dict[int, int] = defaultdict(int)
        self.requests_by_path: Dict[str, int] = defaultdict(int)
        self.latency_ms: Deque[float] = deque(maxlen=500)
        self.errors_total = 0
        self.llm_calls = 0
        self.llm_errors = 0
        self.started_at = now_iso()

    def record_request(self, path: str, status: int, latency_ms: float) -> None:
        with self._lock:
            self.requests_total += 1
            self.requests_by_status[status] += 1
            bare = path.split("?")[0]
            self.requests_by_path[bare] += 1
            self.latency_ms.append(latency_ms)
            if status >= 500:
                self.errors_total += 1

    def record_llm(self, success: bool) -> None:
        with self._lock:
            self.llm_calls += 1
            if not success:
                self.llm_errors += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            lat = list(self.latency_ms)
            p50 = p95 = None
            if lat:
                s = sorted(lat)
                p50 = round(s[len(s) // 2], 1)
                p95 = round(s[min(len(s) - 1, int(len(s) * 0.95))], 1)
            return {
                "started_at": self.started_at,
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "requests_by_status": dict(self.requests_by_status),
                "requests_by_path": dict(self.requests_by_path),
                "latency_ms_p50": p50,
                "latency_ms_p95": p95,
                "llm_calls": self.llm_calls,
                "llm_errors": self.llm_errors,
            }


METRICS = Metrics()


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, rate: float = 5.0, capacity: float = 20.0) -> None:
        self.rate = max(0.1, rate)
        self.capacity = max(1.0, capacity)
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> Tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = _Bucket(tokens=self.capacity, updated=now)
                self._buckets[key] = b
            elapsed = now - b.updated
            b.tokens = min(self.capacity, b.tokens + elapsed * self.rate)
            b.updated = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True, 0.0
            need = 1.0 - b.tokens
            retry = need / self.rate
            return False, round(retry, 2)


def default_rate_limiter() -> RateLimiter:
    rate = float(os.getenv("RATE_LIMIT_PER_SEC", "5"))
    burst = float(os.getenv("RATE_LIMIT_BURST", "20"))
    return RateLimiter(rate=rate, capacity=burst)


RATE_LIMITER = default_rate_limiter()


def readiness_checks() -> Dict[str, Any]:
    checks: Dict[str, Any] = {
        "process": "ok",
        "auth_enabled": bool((os.getenv("INSIGHTFORGE_API_KEYS") or "").strip()),
        "groq_key": bool((os.getenv("GROQ_API_KEY") or "").strip()),
        "gemini_key": bool((os.getenv("GOOGLE_API_KEY") or "").strip()),
    }
    checks["ready"] = True
    checks["degraded"] = not (checks["groq_key"] or checks["gemini_key"])
    if checks["degraded"]:
        checks["degraded_reason"] = "No LLM API keys configured – SQL-only / offline features still work"
    return checks


__all__ = [
    "log_event",
    "METRICS",
    "Metrics",
    "RateLimiter",
    "RATE_LIMITER",
    "default_rate_limiter",
    "readiness_checks",
    "now_iso",
]
