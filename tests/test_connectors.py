"""
Phase 4.1 – Live Data Connectors tests.

Unit tests do not require a running Postgres/MySQL instance.
Live tests are skipped unless INSIGHTFORGE_LIVE_DB=1 and credentials exist.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Make phase4_1 package importable when running from artifacts
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Also allow app.core style if the real project is on path
PROJECT = ROOT  # when copied into repo, this becomes app/...

from app.core.connectors.base import (  # noqa: E402
    ConnectionConfig,
    TableInfo,
    safe_identifier,
)
from app.core.connectors import (  # noqa: E402
    create_connector,
    register_table_as_dataset,
    SUPPORTED_DIALECTS,
)
from app.core.connectors.postgres import PostgresConnector  # noqa: E402
from app.core.connectors.mysql import MySQLConnector  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pg_config(**kwargs) -> ConnectionConfig:
    base = dict(
        name="test_pg",
        dialect="postgres",
        host="localhost",
        port=5432,
        database="testdb",
        user="testuser",
        password="secret",
        schema="public",
    )
    base.update(kwargs)
    return ConnectionConfig(**base)


def _mysql_config(**kwargs) -> ConnectionConfig:
    base = dict(
        name="test_mysql",
        dialect="mysql",
        host="localhost",
        port=3306,
        database="testdb",
        user="testuser",
        password="secret",
    )
    base.update(kwargs)
    return ConnectionConfig(**base)


# ---------------------------------------------------------------------------
# Unit: models & factory
# ---------------------------------------------------------------------------

def test_connection_config_public_dict_hides_password():
    cfg = _pg_config()
    pub = cfg.public_dict()
    assert "password" not in pub
    assert pub["has_password"] is True
    assert pub["name"] == "test_pg"
    assert pub["dialect"] == "postgres"


def test_safe_identifier():
    assert safe_identifier("Orders 2024!") == "Orders_2024"
    assert safe_identifier("123abc") == "t_123abc"
    assert safe_identifier("") == "unnamed"


def test_create_connector_postgres():
    c = create_connector(_pg_config())
    assert isinstance(c, PostgresConnector)
    assert c.dialect == "postgres"


def test_create_connector_mysql():
    c = create_connector(_mysql_config())
    assert isinstance(c, MySQLConnector)
    assert c.dialect == "mysql"


def test_create_connector_unsupported():
    with pytest.raises(ValueError, match="Unsupported"):
        create_connector(ConnectionConfig(
            name="x", dialect="snowflake", host="h", port=1, database="d", user="u"
        ))


def test_source_label():
    cfg = _pg_config()
    assert "postgres://localhost/testdb.public.orders" in cfg.source_label("orders", "public")


# ---------------------------------------------------------------------------
# Unit: friendly errors + health (mocked)
# ---------------------------------------------------------------------------

def test_postgres_friendly_auth_error():
    conn = PostgresConnector(_pg_config())
    with patch.object(conn, "_connect", side_effect=Exception("password authentication failed for user")):
        ok, msg = conn.test_connection()
    assert ok is False
    assert "Authentication failed" in msg


def test_postgres_friendly_network_error():
    conn = PostgresConnector(_pg_config())
    with patch.object(conn, "_connect", side_effect=Exception("could not connect to server: Connection refused")):
        ok, msg = conn.test_connection()
    assert ok is False
    assert "Could not reach" in msg


def test_health_updates_config():
    conn = PostgresConnector(_pg_config())
    with patch.object(conn, "test_connection", return_value=(True, "ok")):
        h = conn.health()
    assert h["ok"] is True
    assert conn.config.status == "healthy"
    assert conn.config.last_health_ok is True


def test_unsafe_where_rejected():
    conn = PostgresConnector(_pg_config())
    with patch.object(conn, "_connect") as mock_c:
        mock_c.return_value = MagicMock()
        with pytest.raises(ValueError, match="Unsafe"):
            conn.load_table("orders", where="1=1; DROP TABLE x")


# ---------------------------------------------------------------------------
# Integration-style: register into a real Workspace (in-memory)
# ---------------------------------------------------------------------------

def test_register_table_as_dataset_uses_workspace_path():
    """End-to-end path without a real DB: mock load_table → real DatasetRecord + DuckDB."""
    from app.core.data_manager import Workspace  # may need path adjustment in CI

    # Ensure data_manager is importable
    try:
        from app.core.data_manager import Workspace, DatasetRecord
    except ImportError:
        # When running from artifacts only, load data_manager from sibling if present
        dm_path = ROOT / "app" / "core" / "data_manager.py"
        if not dm_path.exists():
            # Fall back: use the one from the live repo if cloned, else skip
            pytest.skip("data_manager not available in this test environment")
        import importlib.util
        spec = importlib.util.spec_from_file_location("data_manager", dm_path)
        dm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dm)
        Workspace = dm.Workspace

    sample = pd.DataFrame(
        {
            "order_id": [1, 2, 3],
            "region": ["North", "South", "North"],
            "amount": [10.0, 20.5, 15.0],
        }
    )

    class FakeConnector(PostgresConnector):
        def load_table(self, table, limit=None, schema=None, where=None):
            return sample.copy()

        def test_connection(self):
            return True, "fake"

    ws = Workspace()
    connector = FakeConnector(_pg_config(name="demo"))
    # Skip real cleaning module if not present; register_table tolerates it
    name = register_table_as_dataset(
        workspace=ws,
        connector=connector,
        table="orders",
        schema="public",
        limit=100,
        dataset_name="demo_orders",
        run_cleaning=False,
    )
    assert name == "demo_orders" or name.startswith("demo_orders")
    rec = ws.get(name)
    assert rec is not None
    assert len(rec.cleaned_df) == 3
    assert rec.metadata.get("source_type") == "connector"
    assert rec.metadata.get("connector_dialect") == "postgres"
    assert any(l.get("action") == "loaded_from_connector" for l in rec.lineage)
    # DuckDB registration
    tables = ws.list_duckdb_tables()
    assert name in tables
    df, err = ws.execute_sql(f'SELECT region, SUM(amount) AS total FROM "{name}" GROUP BY region')
    assert err is None
    assert len(df) == 2


# ---------------------------------------------------------------------------
# Live (optional)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.getenv("INSIGHTFORGE_LIVE_DB") != "1",
    reason="Set INSIGHTFORGE_LIVE_DB=1 and POSTGRES_* env to run live tests",
)
def test_live_postgres_roundtrip():
    from app.core.connectors.postgres import from_env

    conn = from_env("live")
    if conn is None:
        pytest.skip("No POSTGRES_* / DATABASE_URL configured")
    ok, msg = conn.test_connection()
    assert ok, msg
    tables = conn.list_tables()
    assert isinstance(tables, list)
    if tables:
        t = tables[0]
        preview = conn.preview_table(t.name, limit=5, schema=t.schema)
        assert isinstance(preview, pd.DataFrame)


def test_supported_dialects():
    assert "postgres" in SUPPORTED_DIALECTS
    assert "mysql" in SUPPORTED_DIALECTS
