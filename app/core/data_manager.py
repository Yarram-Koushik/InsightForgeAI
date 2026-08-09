import pandas as pd
import duckdb
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import uuid
from pathlib import Path


class DatasetRecord:
    """
    Represents one dataset in the workspace with full industry-level information.
    """
    def __init__(self, name: str, raw_df: pd.DataFrame, source_filename: str):
        self.id = str(uuid.uuid4())[:8]
        self.name = name
        self.source_filename = source_filename
        self.created_at = datetime.now().isoformat()

        # Core data
        self.raw_df = raw_df.copy()
        self.cleaned_df = raw_df.copy()          # starts as copy of raw

        # Metadata
        self.metadata = {
            "source_filename": source_filename,
            "original_rows": len(raw_df),
            "original_columns": len(raw_df.columns),
            "created_at": self.created_at,
            "last_cleaned_at": None,
            "file_type": source_filename.split('.')[-1].lower() if '.' in source_filename else "unknown",
            "duckdb_registered": False
        }

        # Lineage / Change history
        self.lineage: List[Dict[str, Any]] = []

        # Detected issues
        self.issues: List[Dict[str, Any]] = []

    def apply_cleaning(self, cleaned_df: pd.DataFrame, issues: List[Dict], change_log: List[Dict]):
        """Apply cleaning results and record lineage"""
        self.cleaned_df = cleaned_df.copy()
        self.issues = issues
        self.lineage.extend(change_log)
        self.metadata["last_cleaned_at"] = datetime.now().isoformat()
        self.metadata["cleaned_rows"] = len(cleaned_df)
        self.metadata["cleaned_columns"] = len(cleaned_df.columns)

    def get_summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_filename": self.source_filename,
            "original_rows": self.metadata["original_rows"],
            "cleaned_rows": self.metadata.get("cleaned_rows", self.metadata["original_rows"]),
            "columns": self.metadata["original_columns"],
            "issues_count": len(self.issues),
            "changes_applied": len(self.lineage),
            "created_at": self.created_at,
            "duckdb_registered": self.metadata.get("duckdb_registered", False)
        }


class Workspace:
    """
    Manages all datasets in the current session (industry-style workspace).
    Phase 2.1: DuckDB-backed analytical engine for SQL querying.
    """
    def __init__(self, db_path: Optional[str] = None):
        self.datasets: Dict[str, DatasetRecord] = {}
        # In-memory DuckDB by default (fast + isolated per session)
        # Pass a path (e.g. "insightforge.db") for persistence across runs
        if db_path:
            self.con = duckdb.connect(db_path)
        else:
            self.con = duckdb.connect(":memory:")
        self.db_path = db_path
        self._register_helpers()

    def _register_helpers(self):
        """Optional helper views / macros for future agents"""
        pass

    def add_dataset(self, name: str, raw_df: pd.DataFrame, source_filename: str) -> str:
        """Add a new dataset and return its name. Registers cleaned version in DuckDB after cleaning."""
        # Ensure unique name
        base_name = name
        counter = 1
        while name in self.datasets:
            name = f"{base_name}_{counter}"
            counter += 1

        record = DatasetRecord(name=name, raw_df=raw_df, source_filename=source_filename)
        self.datasets[name] = record
        return name

    def register_in_duckdb(self, name: str) -> bool:
        """
        Register (or re-register) the cleaned DataFrame of a dataset as a DuckDB table.
        Table name == dataset name (already sanitized by make_safe_table_name).
        """
        record = self.datasets.get(name)
        if record is None:
            return False

        df = record.cleaned_df
        # DuckDB can register pandas DataFrames directly
        self.con.register(name, df)
        # Also create a persistent table copy for complex queries / joins
        self.con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM "{name}"')
        record.metadata["duckdb_registered"] = True
        return True

    def get(self, name: str) -> Optional[DatasetRecord]:
        return self.datasets.get(name)

    def list_datasets(self) -> List[str]:
        return list(self.datasets.keys())

    def list_duckdb_tables(self) -> List[str]:
        """Return all user tables currently registered in DuckDB"""
        try:
            rows = self.con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    def get_table_schema(self, table_name: str) -> pd.DataFrame:
        """Return column name + type for a DuckDB table (useful for LLM context)"""
        try:
            return self.con.execute(f'DESCRIBE "{table_name}"').df()
        except Exception as e:
            return pd.DataFrame({"error": [str(e)]})

    def execute_sql(self, sql: str, limit: int = 500) -> Tuple[pd.DataFrame, Optional[str]]:
        """
        Execute a read-only SQL query against the workspace DuckDB.
        Returns (result_df, error_message).
        Safety: only SELECT / WITH / DESCRIBE / SHOW / EXPLAIN allowed for now.
        """
        sql_clean = sql.strip().rstrip(";")
        first_word = sql_clean.split(None, 1)[0].upper() if sql_clean else ""

        allowed = {"SELECT", "WITH", "DESCRIBE", "SHOW", "EXPLAIN", "SUMMARIZE", "PRAGMA"}
        if first_word not in allowed:
            return pd.DataFrame(), f"Only read-only statements are allowed (SELECT, WITH, DESCRIBE, SHOW, EXPLAIN). Got: {first_word}"

        try:
            # Soft limit to protect UI
            if first_word in {"SELECT", "WITH"} and "LIMIT" not in sql_clean.upper():
                sql_clean = f"{sql_clean} LIMIT {limit}"

            result = self.con.execute(sql_clean).df()
            return result, None
        except Exception as e:
            return pd.DataFrame(), str(e)

    def get_summary_table(self) -> pd.DataFrame:
        rows = [ds.get_summary() for ds in self.datasets.values()]
        return pd.DataFrame(rows)

    def close(self):
        """Close the DuckDB connection"""
        if self.con:
            self.con.close()
