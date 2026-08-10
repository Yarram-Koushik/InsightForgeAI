"""Phase 4.3 – Automated Analytics Depth tests (no external services)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.eda_pack import build_eda_pack
from app.core.root_cause import run_root_cause, looks_like_root_cause_question
from app.core.whatif import run_whatif, parse_whatif_intent, looks_like_whatif_question
from app.core.cohorts import run_rfm, detect_rfm_columns, looks_like_cohort_question


@pytest.fixture
def sales_df():
    return pd.DataFrame(
        {
            "order_id": [1, 2, 3, 4, 5, 6],
            "region": ["North", "South", "North", "East", "South", "North"],
            "segment": ["Consumer", "Corporate", "Consumer", "Home", "Corporate", "Consumer"],
            "amount": [100.0, 200.0, 150.0, 80.0, 220.0, 90.0],
            "order_date": pd.to_datetime(
                ["2024-01-01", "2024-01-05", "2024-02-01", "2024-02-10", "2024-03-01", "2024-03-15"]
            ),
            "customer_id": ["c1", "c2", "c1", "c3", "c2", "c1"],
        }
    )


def test_eda_pack_success(sales_df):
    pack = build_eda_pack(sales_df, table_name="orders")
    assert pack.success is True
    assert pack.rows == 6
    assert pack.columns == 6
    assert pack.narrative
    assert isinstance(pack.correlations, list)


def test_eda_pack_empty():
    pack = build_eda_pack(pd.DataFrame(), table_name="empty")
    assert pack.success is False


def test_root_cause_breakdown(sales_df):
    res = run_root_cause(sales_df, question="why did sales drop by region")
    assert res.success is True
    assert res.measure == "amount"
    assert res.breakdowns
    assert any(b.dimension == "region" for b in res.breakdowns)
    assert res.narrative_bullets


def test_root_cause_no_measure():
    df = pd.DataFrame({"name": ["a", "b"], "city": ["x", "y"]})
    res = run_root_cause(df, question="why")
    assert res.success is False
    assert res.cannot_compute_reason


def test_looks_like_root_cause():
    assert looks_like_root_cause_question("why did sales drop")
    assert not looks_like_root_cause_question("list all orders")


def test_whatif_parse_and_run(sales_df):
    parsed = parse_whatif_intent("+10% amount on North")
    assert parsed is not None
    assert parsed["pct_change"] == 10.0
    assert parsed["dimension_value"].lower() == "north"

    res = run_whatif(sales_df, question="+10% amount on North")
    assert res.success is True
    assert res.scenario_total > res.baseline_total
    assert res.delta > 0


def test_whatif_global_drop(sales_df):
    res = run_whatif(sales_df, question="what if amount decreases by 5%")
    assert res.success is True
    assert res.scenario_total < res.baseline_total


def test_rfm_success(sales_df):
    cols = detect_rfm_columns(sales_df)
    assert cols["customer_id"] and cols["order_date"] and cols["amount"]
    res = run_rfm(sales_df)
    assert res.success is True
    assert res.rfm_df is not None
    assert len(res.rfm_df) == 3  # c1, c2, c3


def test_rfm_missing_columns():
    df = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
    res = run_rfm(df)
    assert res.success is False
    assert res.cannot_compute_reason


def test_cohort_intent():
    assert looks_like_cohort_question("show RFM segments")
    assert looks_like_whatif_question("+15% price on South")
