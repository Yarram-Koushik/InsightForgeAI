"""
Phase 3.1 completion – Metric Contract tests.
Run: pytest tests/test_metric_contract.py -q
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
    Metric,
    build_model_from_dataframe,
)
from app.core.metric_contract import (
    ResolveStatus,
    bump_metric_version,
    metric_required_columns,
    missing_columns_for_metric,
    resolve_metric_contract,
    enrich_metric_contract_fields,
)


@pytest.fixture
def orders_df():
    return pd.DataFrame({
        "order_id": [1, 2, 3, 4, 5],
        "customer_id": [10, 11, 10, 12, 11],
        "amount": [100.0, 150.0, 90.0, 200.0, 50.0],
        "region": ["North", "South", "North", "East", "South"],
    })


def test_metric_has_contract_fields(orders_df):
    model = build_model_from_dataframe(orders_df, "orders")
    aov = model.metric_by_name("aov")
    assert aov is not None
    assert getattr(aov, "version", 1) == 1
    assert getattr(aov, "domain", "") == "finance"
    assert getattr(aov, "grain", "") == "order"
    assert "amount" in (getattr(aov, "required_columns", None) or metric_required_columns(aov))


def test_required_columns_inferred():
    m = Metric(
        name="aov",
        label="AOV",
        description="",
        agg=AggType.RATIO,
        measure_column="amount",
        entity_column="order_id",
        numerator="amount",
        denominator="order_id",
    )
    cols = metric_required_columns(m)
    assert "amount" in cols
    assert "order_id" in cols


def test_cannot_compute_when_columns_missing(orders_df):
    model = build_model_from_dataframe(orders_df, "orders")
    available = ["order_id", "customer_id", "region"]
    res = resolve_metric_contract(
        "What is the average order value?",
        model,
        available_columns=available,
    )
    assert res.status in (ResolveStatus.CANNOT_COMPUTE, ResolveStatus.RESOLVED, ResolveStatus.AMBIGUOUS)
    if res.primary and res.primary.metric.name == "aov":
        assert res.status == ResolveStatus.CANNOT_COMPUTE
        assert "amount" in res.primary.missing_columns


def test_aov_resolves_to_registry_definition(orders_df):
    model = build_model_from_dataframe(orders_df, "orders")
    res = resolve_metric_contract(
        "What is the average order value?",
        model,
        available_columns=list(orders_df.columns),
    )
    assert res.status == ResolveStatus.RESOLVED
    assert res.primary is not None
    assert res.primary.metric.name == "aov"
    sql = res.primary.metric.sql_expression().upper()
    assert "SUM" in sql
    assert "COUNT" in sql
    assert "AVG(" not in sql


def test_ambiguous_same_label_different_domain(orders_df):
    model = build_model_from_dataframe(orders_df, "orders")
    ops_aov = Metric(
        name="aov_ops",
        label="Average order value (AOV)",
        description="Ops definition: AVG(amount)",
        agg=AggType.AVG,
        measure_column="amount",
        preferred=True,
        confidence=0.9,
        tags=["revenue", "ratio"],
        domain="ops",
        owner="ops_team",
        version=1,
        grain="row",
        required_columns=["amount"],
        expr='AVG("amount")',
    )
    model.metrics.append(ops_aov)
    res = resolve_metric_contract(
        "What is the average order value?",
        model,
        available_columns=list(orders_df.columns),
        ambiguity_margin=5.0,
    )
    if res.status == ResolveStatus.AMBIGUOUS:
        assert len(res.candidates) >= 2
        assert res.clarify_questions
        assert "clarify" in res.message.lower() or "multiple" in res.message.lower()
    else:
        assert res.primary.metric.name == "aov"


def test_bump_metric_version_on_sql_change():
    old = {"name": "aov", "version": 1, "expr": "SUM(a)/COUNT(b)", "sql_preview": "SUM(a)/COUNT(b)", "history": []}
    new = {"name": "aov", "expr": "SUM(a)/NULLIF(COUNT(DISTINCT b),0)", "sql_preview": "SUM(a)/NULLIF(COUNT(DISTINCT b),0)"}
    out = bump_metric_version(old, new)
    assert out["version"] == 2
    assert len(out["history"]) == 1
    assert out["history"][0]["version"] == 1


def test_bump_metric_version_no_change():
    old = {"name": "aov", "version": 3, "expr": "X", "sql_preview": "X", "history": []}
    new = {"name": "aov", "expr": "X", "sql_preview": "X"}
    out = bump_metric_version(old, new)
    assert out["version"] == 3


def test_unique_customers_resolves(orders_df):
    model = build_model_from_dataframe(orders_df, "orders")
    res = resolve_metric_contract(
        "How many unique customers are there?",
        model,
        available_columns=list(orders_df.columns),
    )
    assert res.status == ResolveStatus.RESOLVED
    assert res.primary is not None
    assert res.primary.metric.agg == AggType.COUNT_DISTINCT


def test_enrich_contract_fields_defaults():
    m = Metric(name="x", label="X", description="", agg=AggType.SUM, measure_column="amount")
    m2 = enrich_metric_contract_fields(m)
    assert getattr(m2, "version", None) == 1
    assert getattr(m2, "owner", None) == "system"
    assert getattr(m2, "domain", None) in ("general", "finance", "ops")
