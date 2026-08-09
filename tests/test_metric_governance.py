"""
Phase 3.5 – Metric Governance unit tests.
Run: pytest tests/test_metric_governance.py -q
"""
from __future__ import annotations

import json
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
from app.core.metric_governance import (
    apply_overrides,
    build_governed_semantic_model,
    catalog_path,
    disable_metric,
    empty_catalog,
    enable_metric,
    load_catalog,
    metric_from_dict,
    metric_to_dict,
    reset_catalog,
    save_catalog,
    set_metric_override,
)


def _orders_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": list(range(1, 9)),
            "customer_id": [1, 2, 1, 3, 2, 1, 4, 3],
            "amount": [100.0, 150.0, 90.0, 200.0, 120.0, 80.0, 300.0, 110.0],
            "region": ["North", "South", "North", "East", "South", "North", "West", "East"],
            "order_date": pd.date_range("2024-01-01", periods=8, freq="D"),
        }
    )


@pytest.fixture
def tmp_catalog(tmp_path):
    return tmp_path / "metric_catalog"


@pytest.fixture
def auto_model():
    return build_model_from_dataframe(_orders_df(), "orders")


def test_metric_roundtrip_dict():
    m = Metric(
        name="aov",
        label="Average order value",
        description="SUM(amount)/COUNT(DISTINCT order_id)",
        agg=AggType.RATIO,
        additivity=Additivity.NON,
        measure_column="amount",
        entity_column="order_id",
        preferred=True,
        tags=["revenue", "ratio"],
    )
    d = metric_to_dict(m)
    assert d["agg"] == "ratio"
    assert d["additivity"] == "non"
    assert "sql_preview" in d
    m2 = metric_from_dict(d)
    assert m2.name == "aov"
    assert m2.agg == AggType.RATIO
    assert m2.additivity == Additivity.NON
    assert m2.measure_column == "amount"


def test_empty_catalog_load_save(tmp_catalog):
    cat = load_catalog("orders", root=tmp_catalog)
    assert cat["metrics"] == []
    assert cat["disabled"] == []
    path = save_catalog("orders", cat, root=tmp_catalog)
    assert path.exists()
    loaded = load_catalog("orders", root=tmp_catalog)
    assert loaded["version"] == 1


def test_apply_overrides_replace_and_disable(auto_model, tmp_catalog):
    # Disable row_count and override / add a custom metric
    custom = Metric(
        name="gross_revenue",
        label="Gross Revenue",
        description="User defined total amount",
        agg=AggType.SUM,
        additivity=Additivity.FULL,
        measure_column="amount",
        preferred=True,
        tags=["user", "revenue"],
        reason="Custom override",
    )
    cat = {
        "version": 1,
        "disabled": ["row_count"],
        "metrics": [metric_to_dict(custom)],
        "notes": "test",
    }
    governed = apply_overrides(auto_model, cat)
    names = [m.name for m in governed.metrics]
    assert "row_count" not in names
    assert "gross_revenue" in names
    assert governed.source == "user"
    gr = governed.metric_by_name("gross_revenue")
    assert gr is not None
    assert gr.preferred is True
    assert "SUM" in gr.sql_expression()


def test_set_and_disable_persist(auto_model, tmp_catalog):
    m = Metric(
        name="custom_aov",
        label="Custom AOV",
        description="test",
        agg=AggType.RATIO,
        additivity=Additivity.NON,
        measure_column="amount",
        entity_column="order_id",
        preferred=True,
    )
    set_metric_override("orders", m, root=tmp_catalog)
    cat = load_catalog("orders", root=tmp_catalog)
    assert any(x["name"] == "custom_aov" for x in cat["metrics"])

    disable_metric("orders", "custom_aov", root=tmp_catalog)
    cat2 = load_catalog("orders", root=tmp_catalog)
    assert "custom_aov" in cat2["disabled"]
    assert not any(x["name"] == "custom_aov" for x in cat2["metrics"])

    enable_metric("orders", "custom_aov", root=tmp_catalog)
    cat3 = load_catalog("orders", root=tmp_catalog)
    assert "custom_aov" not in cat3["disabled"]


def test_reset_catalog(tmp_catalog):
    save_catalog("orders", {"version": 1, "disabled": ["x"], "metrics": [], "notes": ""}, root=tmp_catalog)
    assert catalog_path("orders", root=tmp_catalog).exists()
    assert reset_catalog("orders", root=tmp_catalog) is True
    assert not catalog_path("orders", root=tmp_catalog).exists()


def test_build_governed_with_catalog(tmp_catalog):
    """End-to-end with a fake workspace via build_model_from_dataframe path."""
    # We cannot easily call build_governed without a real Workspace that has the table.
    # Instead exercise apply path which is the core of governance.
    model = build_model_from_dataframe(_orders_df(), "orders")
    assert any(m.name == "row_count" for m in model.metrics)

    cat = empty_catalog()
    cat["disabled"] = ["row_count"]
    save_catalog("orders", cat, root=tmp_catalog)

    governed = apply_overrides(model, load_catalog("orders", root=tmp_catalog))
    assert not any(m.name == "row_count" for m in governed.metrics)
    assert governed.source == "user"


def test_malformed_catalog_entry_skipped(auto_model):
    cat = {
        "version": 1,
        "disabled": [],
        "metrics": [
            {"name": "bad", "agg": "not_a_real_agg"},  # still reconstructs with fallback
            "not_a_dict",
            {"name": ""},  # empty name skipped
        ],
    }
    governed = apply_overrides(auto_model, cat)
    # Should not raise; model still usable
    assert isinstance(governed.metrics, list)
    assert len(governed.metrics) >= 1


def test_sql_preview_present_after_override(auto_model, tmp_catalog):
    m = Metric(
        name="sum_amount_user",
        label="Total Amount (user)",
        description="",
        agg=AggType.SUM,
        measure_column="amount",
        preferred=True,
    )
    set_metric_override("orders", m, root=tmp_catalog)
    cat = load_catalog("orders", root=tmp_catalog)
    entry = next(x for x in cat["metrics"] if x["name"] == "sum_amount_user")
    assert "sql_preview" in entry
    assert "SUM" in (entry["sql_preview"] or "")
