import pandas as pd
import numpy as np
from typing import Dict, Any, List


def calculate_completeness(df: pd.DataFrame) -> Dict[str, Any]:
    total_cells = df.shape[0] * df.shape[1]
    missing_cells = int(df.isna().sum().sum())
    completeness = ((total_cells - missing_cells) / total_cells * 100) if total_cells > 0 else 100.0

    return {
        "score": round(completeness, 1),
        "total_cells": total_cells,
        "missing_cells": missing_cells,
        "details": f"{missing_cells:,} missing cells out of {total_cells:,}"
    }


def calculate_uniqueness(df: pd.DataFrame) -> Dict[str, Any]:
    if len(df) == 0:
        return {"score": 100.0, "duplicate_rows": 0, "details": "Empty dataset"}

    duplicate_rows = int(df.duplicated().sum())
    uniqueness = ((len(df) - duplicate_rows) / len(df)) * 100

    return {
        "score": round(uniqueness, 1),
        "duplicate_rows": duplicate_rows,
        "details": f"{duplicate_rows:,} duplicate rows found"
    }


def calculate_validity(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Basic validity checks:
    - Negative values in columns that should be positive (amount, quantity, age...)
    - Extremely long text (possible data errors)
    """
    total_checks = 0
    failed_checks = 0
    problems = []

    for col in df.columns:
        col_lower = col.lower()

        # Check numeric columns that should not be negative
        if pd.api.types.is_numeric_dtype(df[col]):
            total_checks += 1
            negative_count = (df[col] < 0).sum()
            if negative_count > 0 and any(x in col_lower for x in ["amount", "price", "qty", "quantity", "age", "count"]):
                failed_checks += negative_count
                problems.append(f"{col}: {negative_count} negative values")

        # Very long text strings (possible errors)
        if df[col].dtype == "object":
            total_checks += 1
            long_values = df[col].astype(str).str.len() > 300
            long_count = long_values.sum()
            if long_count > 0:
                failed_checks += long_count
                problems.append(f"{col}: {long_count} unusually long values")

    if total_checks == 0:
        validity_score = 100.0
    else:
        validity_score = max(0, 100 - (failed_checks / max(total_checks, 1) * 10))

    return {
        "score": round(validity_score, 1),
        "problems": problems,
        "details": f"{len(problems)} potential validity issues found" if problems else "No major validity issues"
    }


def generate_quality_report(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Generates a transparent, multi-dimensional quality report
    """
    completeness = calculate_completeness(df)
    uniqueness = calculate_uniqueness(df)
    validity = calculate_validity(df)

    # Weighted Overall Score
    # Completeness is most important, then Uniqueness, then Validity
    overall = (
        completeness["score"] * 0.50 +
        uniqueness["score"] * 0.30 +
        validity["score"] * 0.20
    )

    return {
        "overall_score": round(overall, 1),
        "completeness": completeness,
        "uniqueness": uniqueness,
        "validity": validity,
        "row_count": len(df),
        "column_count": len(df.columns),
        "memory_mb": round(df.memory_usage(deep=True).sum() / (1024 ** 2), 2)
    }


def column_level_profile(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detailed profile for every column
    """
    rows = []

    for col in df.columns:
        col_data = df[col]
        missing_pct = round((col_data.isna().sum() / len(df)) * 100, 2) if len(df) > 0 else 0
        unique_count = col_data.nunique()

        dtype = str(col_data.dtype)
        sample = col_data.dropna().head(3).tolist()

        rows.append({
            "Column": col,
            "Data Type": dtype,
            "Missing %": missing_pct,
            "Unique Values": unique_count,
            "Sample Values": str(sample)[:60]
        })

    return pd.DataFrame(rows)