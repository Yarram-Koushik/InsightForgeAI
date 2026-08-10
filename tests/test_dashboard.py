"""Phase 4.4 – Dashboard widgets & export."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.core.dashboard import (
    DashboardWidget,
    add_widget,
    clear_dashboard,
    load_widgets,
    pin_from_turn,
    refresh_widget,
    remove_widget,
    save_widgets,
)
from app.core.data_manager import Workspace
from app.core.export import build_dashboard_pdf, build_dashboard_pptx


@pytest.fixture
ndef tmp_store(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "dashboard").mkdir()
    return root


@pytest.fixture
ndef workspace():
    ws = Workspace()
    df = pd.DataFrame(
        {
            "region": ["North", "South", "North", "East"],
            "amount": [100.0, 200.0, 150.0, 80.0],
            "orders": [1, 2, 1, 1],
        }
    )
    name = ws.add_dataset("sales", df, "sales.csv")
    ws.register_in_duckdb(name)
    return ws


def test_pin_from_turn_basic():
    turn = {
        "question": "total sales by region",
        "success": True,
        "sql": 'SELECT region, SUM(amount) AS total FROM "sales" GROUP BY region',
        "insight": "North leads with 250.",
        "chart_type": "bar",
        "grounding_line": "Used: sales.amount",
        "citations": [{"table": "sales", "column": "amount"}],
        "result_df": pd.DataFrame({"region": ["North"], "total": [250.0]}),
    }
    w = pin_from_turn(turn, table_name="sales")
    assert w.table_name == "sales"
    assert w.sql and "SUM" in w.sql
    assert w.status == "ok"
    assert w.last_row_count == 1
    assert w.citations


def test_persist_load_roundtrip(tmp_store):
    w = DashboardWidget(
        id="abc123",
        title="Revenue by region",
        question="total by region",
        table_name="sales",
        sql='SELECT region, SUM(amount) FROM "sales" GROUP BY region',
        insight="North leads",
    )
    add_widget(tmp_store, w)
    loaded = load_widgets(tmp_store)
    assert len(loaded) == 1
    assert loaded[0].id == "abc123"
    assert loaded[0].title == "Revenue by region"


def test_remove_and_clear(tmp_store):
    w1 = DashboardWidget(id="a", title="A", question="q1", table_name="t")
    w2 = DashboardWidget(id="b", title="B", question="q2", table_name="t")
    save_widgets(tmp_store, [w1, w2])
    remove_widget(tmp_store, "a")
    left = load_widgets(tmp_store)
    assert len(left) == 1 and left[0].id == "b"
    clear_dashboard(tmp_store)
    assert load_widgets(tmp_store) == []


def test_refresh_ok(workspace):
    w = DashboardWidget(
        id="r1",
        title="Totals",
        question="sum amount",
        table_name="sales",
        sql='SELECT SUM(amount) AS total FROM "sales"',
    )
    updated, df, err = refresh_widget(w, workspace)
    assert err is None
    assert updated.status == "ok"
    assert df is not None and not df.empty
    assert updated.last_row_count == 1


def test_refresh_stale_missing_table(workspace):
    w = DashboardWidget(
        id="r2",
        title="Ghost",
        question="q",
        table_name="does_not_exist",
        sql='SELECT 1',
    )
    updated, df, err = refresh_widget(w, workspace)
    assert updated.status == "stale"
    assert df is None
    assert err and "no longer" in err.lower()


def test_refresh_no_sql(workspace):
    w = DashboardWidget(id="r3", title="NoSQL", question="q", table_name="sales", sql=None)
    updated, df, err = refresh_widget(w, workspace)
    assert updated.status == "error"
    assert "No SQL" in (err or "")


def test_pdf_export_empty():
    payload = build_dashboard_pdf([], title="Empty Dash")
    assert payload.mime == "application/pdf"
    assert payload.data[:4] == b"%PDF"


def test_pdf_export_with_widgets():
    widgets = [
        DashboardWidget(
            id="1",
            title="Sales",
            question="total sales",
            table_name="sales",
            sql="SELECT 1",
            insight="Looks good",
            status="ok",
        )
    ]
    payload = build_dashboard_pdf(widgets, title="Test Dash")
    assert payload.mime == "application/pdf"
    assert len(payload.data) > 500


def test_pptx_export():
    widgets = [
        DashboardWidget(
            id="1",
            title="Sales",
            question="total sales",
            table_name="sales",
            sql="SELECT 1",
            insight="Looks good",
        )
    ]
    payload = build_dashboard_pptx(widgets, title="Test Deck")
    assert "presentation" in payload.mime or payload.filename.endswith(".pptx")
    assert len(payload.data) > 1000
