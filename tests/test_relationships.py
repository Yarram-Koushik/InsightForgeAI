"""
Phase 3.3 – Multi-table Relationships unit tests.
Run: pytest tests/test_relationships.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.relationships import (
    Cardinality,
    detect_relationships,
    find_join_path,
    compile_join_sql,
    relationships_prompt_block,
    RelationshipGraph,
)


class _Rec:
    def __init__(self, df: pd.DataFrame):
        self.cleaned_df = df
        self.metadata = {}


class _WS:
    def __init__(self, tables: dict):
        self._t = {k: _Rec(v) for k, v in tables.items()}

    def list_datasets(self):
        return list(self._t.keys())

    def get(self, name):
        return self._t.get(name)


def _sample_workspace():
    customers = pd.DataFrame(
        {
            "customer_id": [1, 2, 3, 4],
            "name": ["A", "B", "C", "D"],
            "region": ["West", "East", "West", "North"],
        }
    )
    orders = pd.DataFrame(
        {
            "order_id": [10, 11, 12, 13, 14],
            "customer_id": [1, 1, 2, 3, 2],
            "amount": [100.0, 50.0, 200.0, 80.0, 120.0],
        }
    )
    return _WS({"customers": customers, "orders": orders})


def test_detect_orders_customers_link():
    ws = _sample_workspace()
    g = detect_relationships(ws)
    assert len(g.relationships) >= 1
    rel = g.relationships[0]
    cols = {rel.left_column, rel.right_column}
    assert "customer_id" in cols
    tables = {rel.left_table, rel.right_table}
    assert tables == {"orders", "customers"}


def test_cardinality_many_to_one_preferred():
    ws = _sample_workspace()
    g = detect_relationships(ws)
    found = False
    for r in g.relationships:
        if r.left_table == "orders" and r.right_table == "customers":
            assert r.cardinality in (
                Cardinality.MANY_TO_ONE,
                Cardinality.UNKNOWN,
                Cardinality.MANY_TO_MANY,
            )
            found = True
        if r.left_table == "customers" and r.right_table == "orders":
            assert r.cardinality in (
                Cardinality.ONE_TO_MANY,
                Cardinality.UNKNOWN,
                Cardinality.MANY_TO_MANY,
            )
            found = True
    assert found


def test_join_path_exists():
    ws = _sample_workspace()
    g = detect_relationships(ws)
    path = find_join_path(g, "orders", "customers")
    assert path is not None
    assert path.tables[0] == "orders"
    assert path.tables[-1] == "customers"
    assert len(path.steps) == 1


def test_join_path_same_table():
    ws = _sample_workspace()
    g = detect_relationships(ws)
    path = find_join_path(g, "orders", "orders")
    assert path is not None
    assert path.is_empty or path.tables == ["orders"]


def test_compile_join_sql():
    ws = _sample_workspace()
    g = detect_relationships(ws)
    path = find_join_path(g, "orders", "customers")
    assert path is not None
    sql, warnings, err = compile_join_sql(path, limit=50)
    assert err is None
    assert sql is not None
    assert "JOIN" in sql
    assert "ON" in sql
    assert "orders" in sql
    assert "customers" in sql
    assert "LIMIT 50" in sql


def test_fan_out_blocked_when_requested():
    ws = _sample_workspace()
    g = detect_relationships(ws)
    path = find_join_path(g, "customers", "orders")
    if path is None:
        pytest.skip("path not found")
    if path.fan_out_risk:
        sql, warnings, err = compile_join_sql(path, block_fan_out=True)
        assert sql is None
        assert err is not None
        assert "Fan-out" in err


def test_single_table_warning():
    ws = _WS({"only": pd.DataFrame({"a": [1, 2]})})
    g = detect_relationships(ws)
    assert any("at least two" in w.lower() for w in g.warnings)


def test_prompt_block_non_empty():
    ws = _sample_workspace()
    g = detect_relationships(ws)
    block = relationships_prompt_block(g, primary_table="orders")
    assert "RELATIONSHIPS" in block
    assert "JOIN" in block.upper() or "→" in block


def test_no_false_link_on_unrelated():
    ws = _WS(
        {
            "alpha": pd.DataFrame({"x": [1, 2, 3], "val": [10, 20, 30]}),
            "beta": pd.DataFrame({"y": [9, 8, 7], "score": [0.1, 0.2, 0.3]}),
        }
    )
    g = detect_relationships(ws, min_confidence=0.55)
    assert len(g.relationships) == 0


def test_summary_dict_shape():
    ws = _sample_workspace()
    g = detect_relationships(ws)
    d = g.summary_dict()
    assert "relationships" in d
    assert "tables" in d


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
