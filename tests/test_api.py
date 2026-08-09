"""
Phase 3.4 – FastAPI boundary unit tests.
Run: pytest tests/test_api.py -q

Core tests (health, schemas, error shapes) do not need the full agent stack.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(API_DIR))


@pytest.fixture(scope="module")
def client():
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi not installed")

    import main as api_main
    api_main._workspace = None
    return TestClient(api_main.app), api_main


def test_health(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data.get("service") == "InsightForgeAI"
    assert "version" in data


def test_schemas_load():
    import schemas as sch
    h = sch.HealthResponse()
    assert h.status == "ok"
    req = sch.AskRequest(table_name="orders", question="how many rows?")
    assert req.table_name == "orders"
    assert req.question == "how many rows?"


def test_ask_missing_dataset_returns_error(client):
    c, api_main = client

    class FakeWS:
        def list_datasets(self):
            return []
        def get(self, name):
            return None
        def execute_sql(self, sql, limit=500):
            import pandas as pd
            return pd.DataFrame({"x": [1]}), None
        def list_duckdb_tables(self):
            return []
        def get_table_schema(self, name):
            import pandas as pd
            return pd.DataFrame({"error": ["missing"]})

    api_main.set_workspace(FakeWS())
    r = c.post("/ask", json={"table_name": "does_not_exist", "question": "how many?"})
    assert r.status_code in (200, 404)
    body = r.json()
    if "error" in body:
        assert body.get("success") is False
        assert "code" in body["error"] or "message" in body["error"]
    else:
        assert "detail" in body


def test_sql_with_fake_workspace(client):
    c, api_main = client

    class FakeWS:
        def list_datasets(self):
            return []
        def execute_sql(self, sql, limit=500):
            import pandas as pd
            return pd.DataFrame({"x": [1, 2]}), None
        def list_duckdb_tables(self):
            return []

    api_main.set_workspace(FakeWS())
    r = c.post("/sql", json={"sql": "SELECT 1 AS x"})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["result_row_count"] == 2
    assert data["result"][0]["x"] == 1


def test_list_datasets_with_fake(client):
    c, api_main = client

    class FakeRec:
        source_filename = "t.csv"
        id = "abc"
        metadata = {"cleaned_rows": 10, "cleaned_columns": 3}

    class FakeWS:
        def list_datasets(self):
            return ["orders"]
        def get(self, name):
            return FakeRec()

    api_main.set_workspace(FakeWS())
    r = c.get("/datasets")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["datasets"][0]["name"] == "orders"
    assert data["datasets"][0]["rows"] == 10
