"""
InsightForgeAI – Live Data Connectors (Phase 4.1)

Public API:
  - ConnectionConfig, TableInfo, BaseConnector
  - create_connector(config) → PostgresConnector | MySQLConnector
  - register_table_as_dataset(...) → integrates with existing Workspace path
"""

from .base import BaseConnector, ConnectionConfig, TableInfo, safe_identifier
from .postgres import PostgresConnector, from_env as postgres_from_env
from .mysql import MySQLConnector, from_env as mysql_from_env

SUPPORTED_DIALECTS = ("postgres", "mysql")


def create_connector(config: ConnectionConfig) -> BaseConnector:
    dialect = (config.dialect or "").lower().strip()
    if dialect in ("postgres", "postgresql", "pg"):
        config.dialect = "postgres"
        return PostgresConnector(config)
    if dialect in ("mysql", "mariadb"):
        config.dialect = "mysql"
        return MySQLConnector(config)
    raise ValueError(
        f"Unsupported dialect '{config.dialect}'. Supported: {', '.join(SUPPORTED_DIALECTS)}"
    )


def connector_from_env(dialect: str = "postgres", name: str = "default") -> BaseConnector | None:
    dialect = (dialect or "postgres").lower()
    if dialect in ("postgres", "postgresql", "pg"):
        return postgres_from_env(name=name)
    if dialect in ("mysql", "mariadb"):
        return mysql_from_env(name=name)
    return None


def register_table_as_dataset(
    workspace,
    connector: BaseConnector,
    table: str,
    *,
    schema: str | None = None,
    limit: int | None = None,
    dataset_name: str | None = None,
    run_cleaning: bool = True,
) -> str:
    """
    Load a remote table and register it into the existing Workspace using the
    same DatasetRecord + optional cleaning + DuckDB registration path as file uploads.

    Returns the final dataset name used in the workspace.
    """
    from datetime import datetime

    # Local imports to avoid circulars and keep connectors independent of Streamlit
    try:
        from app.core.data_manager import DatasetRecord  # type: ignore
    except Exception:
        # Fallback for test / loose import paths
        import importlib.util
        from pathlib import Path

        dm_path = Path(__file__).resolve().parents[1] / "data_manager.py"
        if not dm_path.exists():
            raise RuntimeError("Cannot import DatasetRecord / data_manager")
        spec = importlib.util.spec_from_file_location("data_manager", dm_path)
        dm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dm)  # type: ignore
        DatasetRecord = dm.DatasetRecord

    df = connector.load_table(table=table, limit=limit, schema=schema)
    if df is None or df.empty:
        # Still allow empty frames so schema is visible
        df = df if df is not None else __import__("pandas").DataFrame()

    source_label = connector.config.source_label(table, schema)
    base_name = dataset_name or safe_identifier(f"{connector.config.name}_{table}")
    final_name = workspace.add_dataset(
        name=base_name,
        raw_df=df,
        source_filename=source_label,
    )
    record = workspace.get(final_name)
    if record is None:
        raise RuntimeError(f"Failed to add dataset '{final_name}'")

    # Lineage: mark origin as live connector
    record.lineage.append(
        {
            "action": "loaded_from_connector",
            "dialect": connector.dialect,
            "connection": connector.config.name,
            "table": table,
            "schema": schema or connector.config.schema,
            "rows": len(df),
            "columns": list(df.columns),
            "limit": limit,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    record.metadata["source_type"] = "connector"
    record.metadata["connector_dialect"] = connector.dialect
    record.metadata["connector_name"] = connector.config.name
    record.metadata["source_table"] = table
    record.metadata["source_schema"] = schema or connector.config.schema
    record.metadata["file_type"] = connector.dialect

    if run_cleaning:
        try:
            from app.core.cleaning import detect_cleaning_issues, apply_safe_cleaning  # type: ignore
        except Exception:
            try:
                import importlib.util
                from pathlib import Path

                cl_path = Path(__file__).resolve().parents[1] / "cleaning.py"
                spec = importlib.util.spec_from_file_location("cleaning", cl_path)
                cl = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(cl)  # type: ignore
                detect_cleaning_issues = cl.detect_cleaning_issues
                apply_safe_cleaning = cl.apply_safe_cleaning
            except Exception:
                detect_cleaning_issues = None
                apply_safe_cleaning = None

        if detect_cleaning_issues and apply_safe_cleaning:
            issues = detect_cleaning_issues(df)
            cleaned_df, change_log = apply_safe_cleaning(df, issues)
            record.apply_cleaning(cleaned_df, issues, change_log)
        else:
            record.cleaned_df = df.copy()
            record.metadata["cleaned_rows"] = len(df)
            record.metadata["cleaned_columns"] = len(df.columns)
    else:
        record.cleaned_df = df.copy()
        record.metadata["cleaned_rows"] = len(df)
        record.metadata["cleaned_columns"] = len(df.columns)

    workspace.register_in_duckdb(final_name)
    connector.mark_synced()
    return final_name


try:
    from .ui_helpers import build_config_from_form, test_and_list, load_selected_tables  # noqa: F401
except Exception:  # pragma: no cover
    pass

__all__ = [
    "BaseConnector",
    "ConnectionConfig",
    "TableInfo",
    "safe_identifier",
    "PostgresConnector",
    "MySQLConnector",
    "create_connector",
    "connector_from_env",
    "register_table_as_dataset",
    "SUPPORTED_DIALECTS",
    "postgres_from_env",
    "mysql_from_env",
    "build_config_from_form",
    "test_and_list",
    "load_selected_tables",
]
