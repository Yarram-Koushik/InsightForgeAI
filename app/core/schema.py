import pandas as pd
import numpy as np
import re
from typing import Dict, List, Any


def is_email(series: pd.Series) -> float:
    """Returns confidence that the column contains emails"""
    sample = series.dropna().astype(str).head(100)
    if len(sample) == 0:
        return 0.0
    pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    matches = sample.str.match(pattern, na=False).mean()
    return round(matches, 2)


def is_url(series: pd.Series) -> float:
    sample = series.dropna().astype(str).head(100)
    if len(sample) == 0:
        return 0.0
    pattern = r'^https?://|^www\.'
    matches = sample.str.match(pattern, na=False).mean()
    return round(matches, 2)


def is_phone(series: pd.Series) -> float:
    sample = series.dropna().astype(str).head(100)
    if len(sample) == 0:
        return 0.0
    # Simple phone pattern (digits, spaces, +, -, ())
    cleaned = sample.str.replace(r'[\s\-\(\)\+]', '', regex=True)
    matches = cleaned.str.match(r'^\d{8,15}$', na=False).mean()
    return round(matches, 2)


def is_currency(series: pd.Series) -> float:
    sample = series.dropna().astype(str).head(100)
    if len(sample) == 0:
        return 0.0
    pattern = r'^[$₹€£]\s?[\d,]+\.?\d*$|^[\d,]+\.?\d*\s?[$₹€£]$'
    matches = sample.str.match(pattern, na=False).mean()
    return round(matches, 2)


def is_percentage(series: pd.Series) -> float:
    sample = series.dropna().astype(str).head(100)
    if len(sample) == 0:
        return 0.0
    pattern = r'^\d+\.?\d*\s?%$'
    matches = sample.str.match(pattern, na=False).mean()
    return round(matches, 2)


def detect_semantic_type(col_name: str, series: pd.Series) -> Dict[str, Any]:
    """
    Detects the semantic type of a column with confidence.
    """
    col_lower = col_name.lower().strip()
    unique_count = series.nunique(dropna=True)
    total_count = len(series)
    missing_pct = round((series.isna().sum() / total_count) * 100, 2) if total_count > 0 else 0
    unique_ratio = unique_count / total_count if total_count > 0 else 0

    result = {
        "column": col_name,
        "physical_type": str(series.dtype),
        "semantic_type": "Unknown",
        "confidence": 0.0,
        "unique_count": unique_count,
        "unique_ratio": round(unique_ratio, 3),
        "missing_pct": missing_pct,
        "recommendation": ""
    }

    # ---------- 1. DateTime ----------
    if pd.api.types.is_datetime64_any_dtype(series):
        result["semantic_type"] = "DateTime"
        result["confidence"] = 0.95
        result["recommendation"] = "Ready for time-based analysis"
        return result

    # ---------- 2. Boolean ----------
    if pd.api.types.is_bool_dtype(series):
        result["semantic_type"] = "Boolean"
        result["confidence"] = 0.98
        return result

    # ---------- 3. Numerical ----------
    if pd.api.types.is_numeric_dtype(series):
        # Check if it looks like an ID
        if unique_ratio > 0.95 or any(x in col_lower for x in ["id", "code", "key", "number", "no"]):
            result["semantic_type"] = "Identifier"
            result["confidence"] = 0.85
            result["recommendation"] = "Likely an ID – do not use for aggregation"
        elif unique_count < 15:
            result["semantic_type"] = "Categorical (Numeric)"
            result["confidence"] = 0.80
            result["recommendation"] = "Low cardinality – treat as category"
        else:
            result["semantic_type"] = "Continuous Numerical"
            result["confidence"] = 0.90
            result["recommendation"] = "Good for aggregation, trends, forecasting"
        return result

    # ---------- 4. Object / String columns ----------
    sample = series.dropna().astype(str)

    # Email
    email_conf = is_email(series)
    if email_conf > 0.7:
        result["semantic_type"] = "Email"
        result["confidence"] = email_conf
        result["recommendation"] = "Contains email addresses"
        return result

    # URL
    url_conf = is_url(series)
    if url_conf > 0.7:
        result["semantic_type"] = "URL"
        result["confidence"] = url_conf
        result["recommendation"] = "Contains web links"
        return result

    # Phone
    phone_conf = is_phone(series)
    if phone_conf > 0.7:
        result["semantic_type"] = "Phone"
        result["confidence"] = phone_conf
        result["recommendation"] = "Contains phone numbers"
        return result

    # Currency
    currency_conf = is_currency(series)
    if currency_conf > 0.6:
        result["semantic_type"] = "Currency"
        result["confidence"] = currency_conf
        result["recommendation"] = "Contains money values – needs cleaning before analysis"
        return result

    # Percentage
    pct_conf = is_percentage(series)
    if pct_conf > 0.6:
        result["semantic_type"] = "Percentage"
        result["confidence"] = pct_conf
        result["recommendation"] = "Contains percentage values"
        return result

    # Possible Date stored as text
    try:
        converted = pd.to_datetime(sample.head(50), errors="coerce")
        if converted.notna().mean() > 0.8:
            result["semantic_type"] = "DateTime (Text)"
            result["confidence"] = 0.75
            result["recommendation"] = "Should be converted to proper DateTime"
            return result
    except Exception:
        pass

    # Identifier by name or uniqueness
    if unique_ratio > 0.9 or any(x in col_lower for x in ["id", "uuid", "guid", "code", "key"]):
        result["semantic_type"] = "Identifier"
        result["confidence"] = 0.80
        result["recommendation"] = "High uniqueness – likely an identifier"
        return result

    # Categorical vs Free Text
    if unique_count <= 20:
        result["semantic_type"] = "Categorical"
        result["confidence"] = 0.85
        result["recommendation"] = "Low cardinality – good for grouping and filters"
    elif unique_ratio < 0.05:
        result["semantic_type"] = "Categorical"
        result["confidence"] = 0.70
        result["recommendation"] = "Relatively low uniqueness – can be treated as category"
    else:
        result["semantic_type"] = "Free Text"
        result["confidence"] = 0.75
        result["recommendation"] = "High cardinality text – not ideal for grouping"

    return result


def detect_schema_semantic(df: pd.DataFrame) -> pd.DataFrame:
    """
    Full semantic schema detection for a dataframe
    """
    results = []
    for col in df.columns:
        info = detect_semantic_type(col, df[col])
        results.append(info)

    return pd.DataFrame(results)