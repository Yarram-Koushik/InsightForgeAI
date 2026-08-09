"""
Phase 3.4 – Time Intelligence unit tests.
Run: pytest tests/test_time_intelligence.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.semantic_layer import build_model_from_dataframe
from app.core.time_intelligence import (
    ComparisonKind,
    TimeGrain,
    TimeIntelRequest,
    compile_time_intel,
    parse_time_intel_intent,
    try_compile_time_intel_from_question,
    time_intel_prompt_block,
)


def _orders_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": list(range(1, 13)),
            "amount": [100, 120, 90, 200, 150, 180, 110, 130, 140, 160, 170, 190],
            "order_date": pd.date_range("2024-01-01", periods=12, freq="MS"),
        }
    )


@pytest.fixture
def model():
    return build_model_from_dataframe(_orders_df(), "orders")


def test_parse_yoy():
    intent = parse_time_intel_intent("revenue year over year")
    assert intent is not None
    assert intent["kind"] == ComparisonKind.YEAR_OVER_YEAR


def test_parse_mom():
    intent = parse_time_intel_intent("sales vs last month")
    assert intent is not None
    assert intent["kind"] == ComparisonKind.MONTH_OVER_MONTH


def test_parse_ytd():
    intent = parse_time_intel_intent("what is YTD revenue?")
    assert intent is not None
    assert intent["kind"] == ComparisonKind.YTD


def test_parse_rolling():
    intent = parse_time_intel_intent("rolling 14 days of orders")
    assert intent is not None
    assert intent["kind"] == ComparisonKind.ROLLING
    assert intent["rolling_periods"] == 14
    assert intent["grain"] == TimeGrain.DAY


def test_parse_no_intent():
    assert parse_time_intel_intent("how many rows are there?") is None


def test_compile_mom_sql(model):
    expr, alias = "SUM(\"amount\")", "sum_amount"
    req = TimeIntelRequest(
        table="orders",
        time_column="order_date",
        metric_expr=expr,
        metric_alias=alias,
        kind=ComparisonKind.MONTH_OVER_MONTH,
        grain=TimeGrain.MONTH,
    )
    r = compile_time_intel(req)
    assert r.success, r.error
    assert "current_value" in r.sql
    assert "previous_value" in r.sql
    assert "growth_pct" in r.sql
    assert "NULLIF" in r.sql


def test_compile_yoy_sql():
    req = TimeIntelRequest(
        table="orders",
        time_column="order_date",
        metric_expr="SUM(\"amount\")",
        kind=ComparisonKind.YEAR_OVER_YEAR,
        grain=TimeGrain.YEAR,
    )
    r = compile_time_intel(req)
    assert r.success, r.error
    assert "1 YEAR" in r.sql or "year" in r.sql.lower()


def test_compile_ytd_sql():
    req = TimeIntelRequest(
        table="orders",
        time_column="order_date",
        metric_expr="SUM(\"amount\")",
        kind=ComparisonKind.YTD,
        grain=TimeGrain.YEAR,
    )
    r = compile_time_intel(req)
    assert r.success, r.error
    assert "DATE_TRUNC('year'" in r.sql
    assert "growth_pct" in r.sql


def test_compile_rolling_sql():
    req = TimeIntelRequest(
        table="orders",
        time_column="order_date",
        metric_expr="SUM(\"amount\")",
        kind=ComparisonKind.ROLLING,
        grain=TimeGrain.DAY,
        rolling_periods=7,
    )
    r = compile_time_intel(req)
    assert r.success, r.error
    assert "7 DAYS" in r.sql
    assert "ORDER BY" in r.sql


def test_invalid_table():
    req = TimeIntelRequest(
        table="",
        time_column="order_date",
        metric_expr="COUNT(*)",
    )
    r = compile_time_intel(req)
    assert not r.success


def test_try_from_question(model):
    r = try_compile_time_intel_from_question(
        "amount month over month",
        model,
        "orders",
    )
    assert r.success, r.error
    assert r.sql is not None
    assert "growth_pct" in r.sql


def test_try_from_question_no_ti(model):
    r = try_compile_time_intel_from_question("list all columns", model, "orders")
    assert not r.success


def test_prompt_block_contains_rules():
    block = time_intel_prompt_block("yoy sales")
    assert "TIME INTELLIGENCE" in block
    assert "NULLIF" in block


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
