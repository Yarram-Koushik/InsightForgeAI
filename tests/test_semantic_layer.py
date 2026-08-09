"""
Phase 3.1 – Semantic Metric Layer unit tests (edge cases).
Run: pytest tests/test_semantic_layer.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.semantic_layer import (
    AggType,
    Additivity,
    build_model_from_dataframe,
    resolve_metrics_for_question,
    metric_prompt_block,
    model_prompt_summary,
)


def _orders_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [1, 2, 3, 4, 5, 1],
            "customer_id": [10, 11, 10, 12, 11, 10],
            "amount": [100.0, 200.0, 50.0, 300.0, 150.0, 80.0],
            "region": ["West", "East", "West", "North", "East", "West"],
            "order_date": pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-03", "2024-02-01", "2024-02-02", "2024-02-03"]
            ),
        }
    )


def test_basic_model_builds():
    df = _orders_df()
    model = build_model_from_dataframe(df, "orders")
    assert model.table_name == "orders"
    assert model.row_count == 6
    assert any(m.name == "row_count" for m in model.metrics)
    assert any(m.agg == AggType.SUM for m in model.metrics)
    assert model.time_dimension is not None


def test_ids_not_used_as_sum_measures():
    df = _orders_df()
    model = build_model_from_dataframe(df, "orders")
    sum_metrics = [m for m in model.metrics if m.agg == AggType.SUM]
    for m in sum_metrics:
        assert m.measure_column is not None
        assert "id" not in m.measure_column.lower()


def test_aov_is_ratio_and_non_additive():
    df = _orders_df()
    model = build_model_from_dataframe(df, "orders")
    aov = next((m for m in model.metrics if m.name == "aov"), None)
    assert aov is not None, "AOV metric should be proposed when amount + order_id exist"
    assert aov.agg == AggType.RATIO
    assert aov.additivity == Additivity.NON
    sql = aov.sql_expression()
    assert "SUM(" in sql
    assert "NULLIF" in sql
    assert "COUNT(DISTINCT" in sql
    assert "AVG(" not in sql


def test_resolve_aov_question():
    df = _orders_df()
    model = build_model_from_dataframe(df, "orders")
    tips = resolve_metrics_for_question("What is the average order value?", model)
    assert tips, "Should resolve at least one metric"
    assert tips[0]["key"] == "aov"
    assert "SUM" in tips[0]["sql_hint"]
    assert "AVG(" not in tips[0]["sql_hint"]


def test_resolve_unique_customers():
    df = _orders_df()
    model = build_model_from_dataframe(df, "orders")
    tips = resolve_metrics_for_question("How many unique customers do we have?", model)
    assert tips
    assert tips[0]["agg"] == "count_distinct"
    assert "COUNT(DISTINCT" in tips[0]["sql_hint"]


def test_prompt_block_contains_hard_rules():
    df = _orders_df()
    model = build_model_from_dataframe(df, "orders")
    block = metric_prompt_block("show aov by region", model)
    assert "NEVER use AVG()" in block or "NEVER" in block
    assert "NULLIF" in block
    assert "NON-additive" in block or "non-additive" in block.lower() or "RATIO" in block or "aov" in block.lower()


def test_empty_dataframe():
    df = pd.DataFrame()
    model = build_model_from_dataframe(df, "empty")
    assert model.row_count == 0
    assert any(m.name == "row_count" for m in model.metrics)
    assert model.warnings


def test_only_id_columns():
    df = pd.DataFrame({"user_id": range(20), "session_id": range(100, 120)})
    model = build_model_from_dataframe(df, "ids_only")
    sum_metrics = [m for m in model.metrics if m.agg == AggType.SUM]
    assert len(sum_metrics) == 0
    assert any(m.agg == AggType.COUNT for m in model.metrics)


def test_model_prompt_summary_shape():
    df = _orders_df()
    model = build_model_from_dataframe(df, "orders")
    text = model_prompt_summary(model)
    assert "SEMANTIC MODEL" in text
    assert "Metrics:" in text
    assert "orders" in text


def test_high_cardinality_not_dimension():
    df = pd.DataFrame(
        {
            "amount": [10.0, 20.0, 30.0],
            "note": [f"free text {i}" for i in range(3)],
        }
    )
    model = build_model_from_dataframe(df, "notes")
    cat_dims = [d for d in model.dimensions if d.dim_type.value == "categorical"]
    assert all(d.column != "note" for d in cat_dims)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
