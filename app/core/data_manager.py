import pandas as pd
import duckdb
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import uuid
from pathlib import Path


class DatasetRecord:
    def __init__(self, name: str, raw_df: pd.DataFrame, source_filename: str):
        self.id = str(uuid.uuid4())[:8]
        self.name = name
        self.source_filename = source_filename
        self.created_at = datetime.now().isoformat()
        self.raw_df = raw_df.copy()
        self.cleaned_df = raw_df.copy()
        self.metadata = {
            "source_filename": source_filename,
            "original_rows": len(raw_df),
            "original_columns": len(raw_df.columns),
            "created_at": self.created_at,
            "last_cleaned_at": None,
            "file_type": source_filename.split('.')[-1].lower() if '.' in source_filename else "unknown",
            "duckdb_registered": False
        }
        self.lineage: List[Dict[str, Any]] = []
        self.issues: List[Dict[str, Any]] = []

    def apply_cleaning(self, cleaned_df: pd.DataFrame, issues: List[Dict], change_log: List[Dict]):
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
    def __init__(self, db_path: Optional[str] = None):
        self.datasets: Dict[str, DatasetRecord] = {}
        if db_path:
            self.con = duckdb.connect(db_path)
        else:
            self.con = duckdb.connect(":memory:")
        self.db_path = db_path

    def add_dataset(self, name: str, raw_df: pd.DataFrame, source_filename: str) -> str:
        base_name = name
        counter = 1
        while name in self.datasets:
            name = f"{base_name}_{counter}"
            counter += 1
        record = DatasetRecord(name=name, raw_df=raw_df, source_filename=source_filename)
        self.datasets[name] = record
        return name

    def register_in_duckdb(self, name: str) -> bool:
        record = self.datasets.get(name)
        if record is None:
            return False
        df = record.cleaned_df
        self.con.register(name, df)
        self.con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM "{name}"')
        record.metadata["duckdb_registered"] = True
        return True

    def get(self, name: str) -> Optional[DatasetRecord]:
        return self.datasets.get(name)

    def list_datasets(self) -> List[str]:
        return list(self.datasets.keys())

    def list_duckdb_tables(self) -> List[str]:
        try:
            rows = self.con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    def get_table_schema(self, table_name: str) -> pd.DataFrame:
        try:
            return self.con.execute(f'DESCRIBE "{table_name}"').df()
        except Exception as e:
            return pd.DataFrame({"error": [str(e)]})

    def execute_sql(self, sql: str, limit: int = 500) -> Tuple[pd.DataFrame, Optional[str]]:
        """Phase 2.7: sql_guard blocklist + multi-statement rejection + optional table check."""
        try:
            import importlib.util
            from pathlib import Path as _P
            _guard_path = _P(__file__).resolve().parent / "sql_guard.py"
            _spec = importlib.util.spec_from_file_location("sql_guard", _guard_path)
            _g = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_g)
            allowed = set(self.list_duckdb_tables() or [])
            gr = _g.validate_against_schema(sql, allowed_tables=allowed if allowed else None)
            if not gr.ok:
                return pd.DataFrame(), gr.error or "SQL blocked by security guard."
            sql_clean = gr.sql
        except Exception:
            sql_clean = (sql or "").strip().rstrip(";")
            first_word = sql_clean.split(None, 1)[0].upper() if sql_clean else ""
            allowed_heads = {"SELECT", "WITH", "DESCRIBE", "SHOW", "EXPLAIN", "SUMMARIZE"}
            if first_word not in allowed_heads:
                return pd.DataFrame(), f"Only read-only statements are allowed. Got: {first_word}"

        try:
            first_word = sql_clean.split(None, 1)[0].upper() if sql_clean else ""
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
        if self.con:
            self.con.close()
