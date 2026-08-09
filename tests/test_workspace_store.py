"""
Phase 3.3 – Durable Workspace unit tests.
Run: pytest tests/test_workspace_store.py -q
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.workspace_store import (
    ChatTurn,
    WorkspaceStore,
    get_or_create_store,
    list_workspaces,
)


@pytest.fixture
def tmp_root(tmp_path):
    return tmp_path / "workspaces"


@pytest.fixture
def store(tmp_root):
    return WorkspaceStore(workspace_id="test_ws", root=tmp_root, max_chat_turns=5)


def _sample_df():
    return pd.DataFrame(
        {
            "order_id": [1, 2, 3, 4],
            "amount": [100.0, 150.0, 90.0, 200.0],
            "region": ["N", "S", "N", "E"],
        }
    )


class FakeRecord:
    def __init__(self, name="orders"):
        self.id = "abc123"
        self.name = name
        self.source_filename = "orders.csv"
        self.created_at = "2026-08-10T00:00:00"
        self.raw_df = _sample_df()
        self.cleaned_df = _sample_df()
        self.metadata = {
            "source_filename": "orders.csv",
            "original_rows": 4,
            "cleaned_rows": 4,
            "original_columns": 3,
        }
        self.issues = [{"col": "amount", "issue": "none"}]
        self.lineage = [{"action": "clean", "rows_removed": 0}]


def test_save_and_load_dataset(store):
    rec = FakeRecord()
    ddir = store.save_dataset(rec, include_raw=True)
    assert (ddir / "cleaned.parquet").exists()
    assert (ddir / "meta.json").exists()
    assert "orders" in store.list_saved_datasets()

    loaded = store.load_dataset_record("orders")
    assert loaded is not None
    assert loaded.name == "orders"
    assert len(loaded.cleaned_df) == 4
    assert loaded.metadata.get("cleaned_rows") == 4


def test_chat_append_and_retention(store):
    for i in range(8):
        store.append_chat_turn(
            ChatTurn(id=str(i), question=f"q{i}", success=True, table_name="orders")
        )
    turns = store.load_chat_history()
    # max_chat_turns=5 → only last 5 kept
    assert len(turns) == 5
    assert turns[0].question == "q3"
    assert turns[-1].question == "q7"


def test_corrupt_chat_line_skipped(store):
    store.chat_path.parent.mkdir(parents=True, exist_ok=True)
    with store.chat_path.open("w", encoding="utf-8") as f:
        f.write('{"id":"1","question":"ok","success":true}\n')
        f.write("this is not json\n")
        f.write('{"id":"2","question":"also ok","success":true}\n')
    turns = store.load_chat_history()
    assert len(turns) == 2
    assert turns[0].question == "ok"


def test_export_import_roundtrip(store, tmp_root):
    rec = FakeRecord()
    store.save_dataset(rec)
    store.append_chat_turn(ChatTurn(id="t1", question="hello", success=True))

    zip_path = tmp_root / "snapshot.zip"
    store.export_snapshot(zip_path)
    assert zip_path.exists()

    imported = WorkspaceStore.import_snapshot(zip_path, workspace_id="imported_ws", root=tmp_root)
    assert "orders" in imported.list_saved_datasets()
    turns = imported.load_chat_history()
    assert len(turns) == 1
    assert turns[0].question == "hello"


def test_delete_dataset(store):
    store.save_dataset(FakeRecord())
    assert "orders" in store.list_saved_datasets()
    store.delete_dataset("orders")
    assert "orders" not in store.list_saved_datasets()
    assert not store.dataset_dir("orders").exists()


def test_summary(store):
    store.save_dataset(FakeRecord())
    s = store.summary()
    assert s["workspace_id"] == "test_ws"
    assert s["dataset_count"] == 1
    assert "orders" in s["datasets"]


def test_list_workspaces(tmp_root):
    WorkspaceStore("ws_a", root=tmp_root)
    WorkspaceStore("ws_b", root=tmp_root)
    ids = list_workspaces(root=tmp_root)
    assert "ws_a" in ids
    assert "ws_b" in ids


def test_load_into_fake_workspace(store):
    """Minimal integration: load_into a duck-typing workspace."""
    store.save_dataset(FakeRecord())

    class FakeWS:
        def __init__(self):
            self.datasets = {}
            self.registered = []

        def register_in_duckdb(self, name):
            self.registered.append(name)
            return True

    ws = FakeWS()
    result = store.load_into(ws)
    assert "orders" in result["restored"]
    assert "orders" in ws.datasets
    assert "orders" in ws.registered
