import pandas as pd
from typing import Dict, Any, Optional, List
from datetime import datetime
import uuid


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
            "file_type": source_filename.split('.')[-1].lower() if '.' in source_filename else "unknown"
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
            "created_at": self.created_at
        }


class Workspace:
    """
    Manages all datasets in the current session (industry-style workspace)
    """
    def __init__(self):
        self.datasets: Dict[str, DatasetRecord] = {}

    def add_dataset(self, name: str, raw_df: pd.DataFrame, source_filename: str) -> str:
        """Add a new dataset and return its name"""
        # Ensure unique name
        base_name = name
        counter = 1
        while name in self.datasets:
            name = f"{base_name}_{counter}"
            counter += 1

        record = DatasetRecord(name=name, raw_df=raw_df, source_filename=source_filename)
        self.datasets[name] = record
        return name

    def get(self, name: str) -> Optional[DatasetRecord]:
        return self.datasets.get(name)

    def list_datasets(self) -> List[str]:
        return list(self.datasets.keys())

    def get_summary_table(self) -> pd.DataFrame:
        rows = [ds.get_summary() for ds in self.datasets.values()]
        return pd.DataFrame(rows)