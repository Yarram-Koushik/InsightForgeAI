"""
Phase 3.2 – Metric Compiler unit tests (edge cases).
Run: pytest tests/test_metric_compiler.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.semantic_layer import build_model_from_dataframe, AggType, Additivity
from app.core.metric_compiler import (
    TimeGrain,
    FilterOp,
    MetricFilter,
    MetricQuery,
    compile_metric_query,
    try_compile_from_question,
    infer_time_grain,
    infer_dimensions_from_question,
)


def _orders_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [1, 2, 3, 4, 5, 1],
            "customer_id": [10, 11, 10, 12, 11, 10],
            "amount": [100.0, 200.0, 50.0, 300.0, 150.0, 80.0],
            "region": ["West", "East", "West", "North", "East", "West"],
            "order_date": pd.to_datetime(
                [
                    "2024-01-01",
                    "2024-01-02",
                    "2024-01-03",
                    "2024-02-01",
                    "2024-02-02",
                    "2024-02-03",
                ]
            ),
        }
    )


@pytest.fixture
def model():
    return build_model_from_dataframe(_orders_df(), "orders")


def test_compile_simple_total(model):
    sum_metrics = [m.name for m in model.metrics if m.agg == AggType.SUM]
    assert sum_metrics
    q = MetricQuery(metric_names=[sum_metrics[0]])
    r = compile_metric_query(model, q)
    assert r.success, r.error
    assert r.sql is not None
    assert "SELECT" in r.sql
    assert "FROM" in r.sql
    assert "SUM(" in r.sql
    assert "GROUP BY" not in r.sql


def test_compile_aov_is_ratio_not_avg(model):
    aov = model.metric_by_name("aov")
    assert aov is not None
    q = MetricQuery(metric_names=["aov"])
    r = compile_metric_query(model, q)
    assert r.success, r.error
    assert "SUM(" in r.sql
    assert "NULLIF" in r.sql
    assert "COUNT(DISTINCT" in r.sql
    assert "AVG(" not in r.sql


def test_compile_with_dimension(model):
    sum_metrics = [m.name for m in model.metrics if m.agg == AggType.SUM]
    q = MetricQuery(metric_names=[sum_metrics[0]], dimensions=["region"])
    r = compile_metric_query(model, q)
    assert r.success, r.error
    assert "GROUP BY" in r.sql
    assert '"region"' in r.sql
    assert "region" in r.dimensions_used


def test_compile_aov_by_region_warns_non_additive(model):
    q = MetricQuery(metric_names=["aov"], dimensions=["region"])
    r = compile_metric_query(model, q)
    assert r.success, r.error
    assert any("NON-additive" in w for w in r.warnings)
    assert "GROUP BY" in r.sql
    assert "NULLIF" in r.sql


def test_compile_time_grain_month(model):
    sum_metrics = [m.name for m in model.metrics if m.agg == AggType.SUM]
    q = MetricQuery(
        metric_names=[sum_metrics[0]],
        time_grain=TimeGrain.MONTH,
    )
    r = compile_metric_query(model, q)
    assert r.success, r.error
    assert "DATE_TRUNC('month'" in r.sql
    assert "GROUP BY" in r.sql


def test_compile_filter_eq(model):
    sum_metrics = [m.name for m in model.metrics if m.agg == AggType.SUM]
    q = MetricQuery(
        metric_names=[sum_metrics[0]],
        filters=[MetricFilter(column="region", op=FilterOp.EQ, value="West")],
    )
    r = compile_metric_query(model, q)
    assert r.success, r.error
    assert "WHERE" in r.sql
    assert "'West'" in r.sql


def test_compile_filter_in(model):
    sum_metrics = [m.name for m in model.metrics if m.agg == AggType.SUM]
    q = MetricQuery(
        metric_names=[sum_metrics[0]],
        filters=[MetricFilter(column="region", op=FilterOp.IN, value=["West", "East"])],
    )
    r = compile_metric_query(model, q)
    assert r.success, r.error
    assert "IN (" in r.sql


def test_compile_unknown_metric_fails(model):
    q = MetricQuery(metric_names=["not_a_real_metric_xyz"])
    r = compile_metric_query(model, q)
    assert not r.success
    assert "Unknown metric" in (r.error or "")


def test_compile_empty_metrics_fails(model):
    q = MetricQuery(metric_names=[])
    r = compile_metric_query(model, q)
    assert not r.success


def test_sql_injection_in_filter_value_stripped(model):
    sum_metrics = [m.name for m in model.metrics if m.agg == AggType.SUM]
    q = MetricQuery(
        metric_names=[sum_metrics[0]],
        filters=[MetricFilter(column="region", op=FilterOp.EQ, value="West'; DROP TABLE orders;--")],
    )
    r = compile_metric_query(model, q)
    assert r.success, r.error
    where_clause = r.sql.split("WHERE", 1)[-1] if "WHERE" in r.sql else r.sql
    assert ";" not in where_clause
    assert "--" not in where_clause
    assert "'West" in r.sql or "'west" in r.sql.lower()


def test_try_compile_aov_question(model):
    r = try_compile_from_question("What is the average order value?", model)
    assert r.success, r.error
    assert "aov" in r.metrics_used
    assert "NULLIF" in (r.sql or "")


def test_try_compile_by_region(model):
    r = try_compile_from_question("total amount by region", model)
    if r.success:
        assert "region" in r.dimensions_used or "region" in (r.sql or "").lower()
    else:
        assert r.error


def test_infer_time_grain():
    assert infer_time_grain("sales by month") == TimeGrain.MONTH
    assert infer_time_grain("daily revenue") == TimeGrain.DAY
    assert infer_time_grain("total revenue") == TimeGrain.NONE


def test_infer_dimensions(model):
    dims = infer_dimensions_from_question("revenue by region", model)
    assert "region" in dims


def test_limit_is_capped(model):
    sum_metrics = [m.name for m in model.metrics if m.agg == AggType.SUM]
    q = MetricQuery(metric_names=[sum_metrics[0]], limit=999999)
    r = compile_metric_query(model, q, max_limit=500)
    assert r.success
    assert "LIMIT 500" in r.sql


def test_order_by_metric(model):
    sum_metrics = [m.name for m in model.metrics if m.agg == AggType.SUM]
    q = MetricQuery(
        metric_names=[sum_metrics[0]],
        dimensions=["region"],
        order_by=sum_metrics[0],
        order_dir="asc",
    )
    r = compile_metric_query(model, q)
    assert r.success
    assert "ORDER BY" in r.sql
    assert "ASC" in r.sql


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
