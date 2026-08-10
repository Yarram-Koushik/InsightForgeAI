"""
Thin helpers used by the Streamlit UI for Phase 4.1 connectors.
Keeps password handling and form state out of the core driver modules.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import ConnectionConfig
from . import create_connector, register_table_as_dataset, SUPPORTED_DIALECTS


def build_config_from_form(
    *,
    name: str,
    dialect: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    schema: Optional[str] = None,
    sslmode: Optional[str] = None,
) -> ConnectionConfig:
    return ConnectionConfig(
        name=(name or f"{dialect}_{host}").strip() or "connection",
        dialect=dialect.lower().strip(),
        host=host.strip(),
        port=int(port),
        database=database.strip(),
        user=user.strip(),
        password=password if password else None,
        schema=(schema or "").strip() or None,
        sslmode=(sslmode or "").strip() or None,
    )


def test_and_list(
    config: ConnectionConfig,
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """
    Test connection and return table list as plain dicts for Streamlit.
    Never raises; returns (ok, message, tables).
    """
    try:
        connector = create_connector(config)
        ok, msg = connector.test_connection()
        if not ok:
            return False, msg, []
        tables = connector.list_tables(schema=config.schema)
        out = [t.to_dict() for t in tables]
        return True, msg, out
    except Exception as e:
        return False, str(e), []


def load_selected_tables(
    workspace,
    config: ConnectionConfig,
    selected: List[Dict[str, Any]],
    *,
    limit: Optional[int] = 100_000,
    run_cleaning: bool = True,
) -> List[Dict[str, Any]]:
    """
    Load each selected table into the workspace.
    selected items: {"name": ..., "schema": ...}
    Returns list of result dicts: {ok, dataset_name, table, error?}
    """
    results = []
    try:
        connector = create_connector(config)
    except Exception as e:
        return [{"ok": False, "table": None, "error": str(e)}]

    for item in selected:
        tname = item.get("name") or item.get("table")
        tschema = item.get("schema")
        if not tname:
            continue
        try:
            ds_name = register_table_as_dataset(
                workspace=workspace,
                connector=connector,
                table=tname,
                schema=tschema,
                limit=limit,
                run_cleaning=run_cleaning,
            )
            results.append(
                {
                    "ok": True,
                    "dataset_name": ds_name,
                    "table": tname,
                    "schema": tschema,
                    "rows": len(workspace.get(ds_name).cleaned_df) if workspace.get(ds_name) else 0,
                }
            )
        except Exception as e:
            results.append(
                {
                    "ok": False,
                    "table": tname,
                    "schema": tschema,
                    "error": str(e),
                }
            )
    return results


__all__ = [
    "build_config_from_form",
    "test_and_list",
    "load_selected_tables",
    "SUPPORTED_DIALECTS",
]
