"""Phase 4.6 – Proactive insights tests (deterministic)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.proactive import (
    ProactiveCard,
    cards_to_message,
    scan_dataframe,
)


def _make_series(n: int = 28, jump_at: int = 21, jump_factor: float = 1.8) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    y = np.full(n, 100.0)
    y[jump_at:] = 100.0 * jump_factor
    rng = np.random.default_rng(42)
    y = y + rng.normal(0, 2, size=n)
    return pd.DataFrame({"order_date": dates, "amount": y})


def test_scan_detects_period_change():
    df = _make_series(n=28, jump_at=21, jump_factor=1.9)
    cards = scan_dataframe(df, table_name="orders", metric_name="amount", window=7)
    assert isinstance(cards, list)
    assert len(cards) >= 1
    assert any(c.severity in ("watch", "alert") for c in cards)
    assert any("amount" in (c.metric or "") or "amount" in c.title.lower() for c in cards)


def test_scan_empty_df():
    assert scan_dataframe(None) == []
    assert scan_dataframe(pd.DataFrame()) == []


def test_scan_no_time_column_outlier():
    df = pd.DataFrame({"value": [1, 2, 2, 3, 2, 100, 2, 3]})
    cards = scan_dataframe(df, table_name="t")
    assert isinstance(cards, list)


def test_cards_to_message_empty():
    msg = cards_to_message([])
    assert "no unusual" in msg.lower()


def test_cards_to_message_with_cards():
    cards = [
        ProactiveCard(
            id="1",
            title="Revenue up",
            severity="alert",
            summary="Last 7d +40%.",
            suggested_question="why did revenue change",
        )
    ]
    msg = cards_to_message(cards)
    assert "Proactive" in msg
    assert "Revenue up" in msg
    assert "why did revenue change" in msg


def test_card_to_dict():
    c = ProactiveCard(id="x", title="t", severity="info", summary="s")
    d = c.to_dict()
    assert d["id"] == "x"
    assert d["severity"] == "info"
