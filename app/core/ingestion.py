import re
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple, Optional

def make_safe_table_name(filename: str) -> str:
    """Convert any filename into a safe SQL table name"""
    name = Path(filename).stem.lower()
    name = re.sub(r'[^a-z0-9_]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    if not name:
        name = "unnamed_table"
    if name[0].isdigit():
        name = f"t_{name}"
    return name


def get_excel_sheets_info(file) -> List[Dict]:
    """
    Returns information about all sheets in an Excel file
    """
    try:
        xl = pd.ExcelFile(file)
        sheets_info = []

        for sheet_name in xl.sheet_names:
            try:
                df = pd.read_excel(file, sheet_name=sheet_name, nrows=5)  # only read few rows for speed
                row_count = pd.read_excel(file, sheet_name=sheet_name, usecols=[0]).shape[0]
                col_count = df.shape[1]

                is_empty = row_count == 0 or col_count == 0

                sheets_info.append({
                    "sheet_name": sheet_name,
                    "rows": row_count,
                    "columns": col_count,
                    "is_empty": is_empty,
                    "preview_columns": list(df.columns) if not is_empty else []
                })
            except Exception:
                sheets_info.append({
                    "sheet_name": sheet_name,
                    "rows": 0,
                    "columns": 0,
                    "is_empty": True,
                    "preview_columns": []
                })

        return sheets_info
    except Exception as e:
        raise Exception(f"Could not read Excel file: {str(e)}")


def read_file(file, sheet_name: Optional[str] = None) -> pd.DataFrame:
    """
    Smart file reader with better JSON support
    """
    filename = file.name.lower()

    try:
        if filename.endswith(".csv"):
            return pd.read_csv(file)

        elif filename.endswith((".xlsx", ".xls")):
            if sheet_name:
                return pd.read_excel(file, sheet_name=sheet_name)
            else:
                return pd.read_excel(file)

        elif filename.endswith(".json"):
            import json

            file.seek(0)
            try:
                data = json.load(file)
            except Exception:
                file.seek(0)
                # Try line-delimited JSON
                return pd.read_json(file, lines=True)

            # Normalize
            if isinstance(data, list):
                df = pd.json_normalize(data)
            elif isinstance(data, dict):
                df = pd.json_normalize(data)
            else:
                raise Exception("Unsupported JSON structure")

            # Convert any column that contains lists/dicts into string
            # (prevents "unhashable type: 'list'" errors later)
            for col in df.columns:
                if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
                    df[col] = df[col].astype(str)

            return df

        elif filename.endswith(".parquet"):
            return pd.read_parquet(file)

        else:
            raise Exception("Unsupported file format")

    except Exception as e:
        raise Exception(f"Failed to read file '{file.name}': {str(e)}")