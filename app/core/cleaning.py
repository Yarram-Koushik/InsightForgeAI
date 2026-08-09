import pandas as pd
import numpy as np
from typing import Dict, List, Any, Tuple
import re


def detect_cleaning_issues(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Detects data quality issues without modifying the data.
    Returns a list of issues with recommended actions and confidence.
    """
    issues = []

    # 1. Completely empty columns
    for col in df.columns:
        if df[col].isna().all():
            issues.append({
                "column": col,
                "issue_type": "Empty Column",
                "severity": "High",
                "description": "Column contains only missing values",
                "recommended_action": "Drop Column",
                "confidence": 0.98,
                "safe_to_auto_apply": True
            })

    # 2. Duplicate rows
    dup_count = df.duplicated().sum()
    if dup_count > 0:
        issues.append({
            "column": " entIRE_DATASET",
            "issue_type": "Duplicate Rows",
            "severity": "High",
            "description": f"{dup_count} completely duplicate rows found",
            "recommended_action": "Remove Duplicate Rows",
            "confidence": 0.95,
            "safe_to_auto_apply": True
        })

    # 3. Missing values analysis
    for col in df.columns:
        missing_count = df[col].isna().sum()
        missing_pct = (missing_count / len(df)) * 100 if len(df) > 0 else 0

        if missing_count == 0:
            continue

        if missing_pct > 70:
            action = "Consider Dropping Column"
            severity = "High"
            auto = False
        elif pd.api.types.is_numeric_dtype(df[col]):
            action = "Fill with Median"
            severity = "Medium"
            auto = True if missing_pct < 30 else False
        else:
            action = "Fill with Mode / 'Unknown'"
            severity = "Medium"
            auto = True if missing_pct < 20 else False

        issues.append({
            "column": col,
            "issue_type": "Missing Values",
            "severity": severity,
            "description": f"{missing_count} missing values ({missing_pct:.1f}%)",
            "recommended_action": action,
            "confidence": 0.85,
            "safe_to_auto_apply": auto
        })

    # 4. Possible DateTime stored as text
    for col in df.columns:
        if df[col].dtype == "object":
            sample = df[col].dropna().head(40)
            try:
                converted = pd.to_datetime(sample, errors="coerce")
                success_rate = converted.notna().mean()
                if success_rate > 0.8:
                    issues.append({
                        "column": col,
                        "issue_type": "DateTime as Text",
                        "severity": "Medium",
                        "description": f"Looks like dates stored as text ({success_rate*100:.0f}% match)",
                        "recommended_action": "Convert to DateTime",
                        "confidence": round(success_rate, 2),
                        "safe_to_auto_apply": success_rate > 0.9
                    })
            except Exception:
                pass

    # 5. High cardinality text that might be IDs
    for col in df.columns:
        if df[col].dtype == "object":
            unique_ratio = df[col].nunique() / len(df) if len(df) > 0 else 0
            if unique_ratio > 0.95:
                issues.append({
                    "column": col,
                    "issue_type": "Possible Identifier",
                    "severity": "Low",
                    "description": f"Very high uniqueness ({unique_ratio:.1%}) – likely an ID",
                    "recommended_action": "Mark as Identifier (do not aggregate)",
                    "confidence": 0.80,
                    "safe_to_auto_apply": False
                })

    return issues


def apply_safe_cleaning(df: pd.DataFrame, issues: List[Dict]) -> Tuple[pd.DataFrame, List[Dict]]:
    """
    Applies only safe, high-confidence cleaning actions.
    Returns cleaned dataframe + list of applied changes (lineage).
    """
    df_clean = df.copy()
    change_log = []

    for issue in issues:
        if not issue.get("safe_to_auto_apply", False):
            continue

        col = issue["column"]
        action = issue["recommended_action"]

        try:
            if action == "Drop Column" and col in df_clean.columns:
                df_clean = df_clean.drop(columns=[col])
                change_log.append({
                    "column": col,
                    "action": "Dropped empty column",
                    "reason": issue["description"],
                    "confidence": issue["confidence"]
                })

            elif action == "Remove Duplicate Rows":
                before = len(df_clean)
                df_clean = df_clean.drop_duplicates()
                removed = before - len(df_clean)
                if removed > 0:
                    change_log.append({
                        "column": "ENTIRE_DATASET",
                        "action": f"Removed {removed} duplicate rows",
                        "reason": "Exact duplicate rows",
                        "confidence": issue["confidence"]
                    })

            elif action == "Fill with Median" and col in df_clean.columns:
                if pd.api.types.is_numeric_dtype(df_clean[col]):
                    median_val = df_clean[col].median()
                    missing_before = df_clean[col].isna().sum()
                    df_clean[col] = df_clean[col].fillna(median_val)
                    change_log.append({
                        "column": col,
                        "action": f"Filled {missing_before} missing values with median ({median_val})",
                        "reason": "Numeric column with missing values",
                        "confidence": issue["confidence"]
                    })

            elif action == "Fill with Mode / 'Unknown'" and col in df_clean.columns:
                missing_before = df_clean[col].isna().sum()
                try:
                    mode_val = df_clean[col].mode(dropna=True)
                    fill_value = mode_val.iloc[0] if not mode_val.empty else "Unknown"
                except Exception:
                    fill_value = "Unknown"
                df_clean[col] = df_clean[col].fillna(fill_value)
                change_log.append({
                    "column": col,
                    "action": f"Filled {missing_before} missing values with '{fill_value}'",
                    "reason": "Categorical/Text column with missing values",
                    "confidence": issue["confidence"]
                })

            elif action == "Convert to DateTime" and col in df_clean.columns:
                df_clean[col] = pd.to_datetime(df_clean[col], errors="coerce")
                change_log.append({
                    "column": col,
                    "action": "Converted to DateTime",
                    "reason": issue["description"],
                    "confidence": issue["confidence"]
                })

        except Exception as e:
            change_log.append({
                "column": col,
                "action": "Failed",
                "reason": str(e),
                "confidence": 0.0
            })

    return df_clean, change_log