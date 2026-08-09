"""
InsightForgeAI – Metric Governance (Phase 3.5)

Persistent, user-overridable metric catalog on top of the auto Semantic Layer.

- Browse / override / disable auto-discovered metrics
- Add custom metrics (including free-form expressions)
- Save / load as JSON under data/metric_catalog/{table}.json
- Governed model is what NL→SQL, Metric Compiler and Time Intelligence consume

Design:
- Auto model remains pure (tests & discovery unchanged)
- Catalog is additive + override by metric name
- Disabled list removes names from the final model
- Free-stack: stdlib json only; no extra dependencies
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.core.semantic_layer import (
    AggType,
    Additivity,
    Metric,
    SemanticModel,
    build_semantic_model,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _default_catalog_root() -> Path:
    """Prefer project data/ next to app/, fall back to CWD."""
    here = Path(__file__).resolve()
    # app/core/metric_governance.py → project_root/data/metric_catalog
    candidates = [
        here.parents[2] / "data" / "metric_catalog",
        Path.cwd() / "data" / "metric_catalog",
        Path("/tmp") / "insightforge_metric_catalog",
    ]
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            return c
        except Exception:
            continue
    return candidates[-1]


CATALOG_ROOT = _default_catalog_root()


def catalog_path(table_name: str, root: Optional[Path] = None) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_\-]+", "_", (table_name or "unknown").strip()) or "unknown"
    return (root or CATALOG_ROOT) / f"{safe}.json"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def metric_to_dict(m: Metric) -> Dict[str, Any]:
    d = asdict(m)
    d["agg"] = m.agg.value if isinstance(m.agg, AggType) else str(m.agg)
    d["additivity"] = m.additivity.value if isinstance(m.additivity, Additivity) else str(m.additivity)
    # Keep a computed SQL for transparency in the UI / catalog
    try:
        d["sql_preview"] = m.sql_expression()
    except Exception:
        d["sql_preview"] = None
    return d


def metric_from_dict(d: Dict[str, Any]) -> Metric:
    """Reconstruct a Metric; tolerant of extra keys and string enums."""
    agg_raw = d.get("agg", "sum")
    add_raw = d.get("additivity", "full")
    try:
        agg = AggType(str(agg_raw).lower())
    except Exception:
        agg = AggType.EXPRESSION if d.get("expr") else AggType.SUM
    try:
        additivity = Additivity(str(add_raw).lower())
    except Exception:
        additivity = Additivity.NON if agg == AggType.RATIO else Additivity.FULL

    return Metric(
        name=str(d.get("name") or "custom_metric").strip() or "custom_metric",
        label=str(d.get("label") or d.get("name") or "Custom metric"),
        description=str(d.get("description") or ""),
        agg=agg,
        additivity=additivity,
        measure_column=d.get("measure_column"),
        entity_column=d.get("entity_column"),
        numerator=d.get("numerator"),
        denominator=d.get("denominator"),
        expr=d.get("expr"),
        filters=list(d.get("filters") or []),
        preferred=bool(d.get("preferred", False)),
        confidence=float(d.get("confidence", 0.85) or 0.85),
        tags=list(d.get("tags") or ["user"]),
        reason=str(d.get("reason") or "User-defined / overridden metric"),
    )


# ---------------------------------------------------------------------------
# Catalog I/O
# ---------------------------------------------------------------------------

def empty_catalog() -> Dict[str, Any]:
    return {
        "version": 1,
        "disabled": [],
        "metrics": [],
        "notes": "",
    }


def load_catalog(table_name: str, root: Optional[Path] = None) -> Dict[str, Any]:
    path = catalog_path(table_name, root=root)
    if not path.exists():
        return empty_catalog()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return empty_catalog()
        data.setdefault("version", 1)
        data.setdefault("disabled", [])
        data.setdefault("metrics", [])
        data.setdefault("notes", "")
        return data
    except Exception:
        return empty_catalog()


def save_catalog(table_name: str, catalog: Dict[str, Any], root: Optional[Path] = None) -> Path:
    path = catalog_path(table_name, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": int(catalog.get("version", 1)),
        "disabled": sorted(set(str(x) for x in (catalog.get("disabled") or []))),
        "metrics": list(catalog.get("metrics") or []),
        "notes": str(catalog.get("notes") or ""),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def delete_catalog(table_name: str, root: Optional[Path] = None) -> bool:
    path = catalog_path(table_name, root=root)
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Apply overrides
# ---------------------------------------------------------------------------

def apply_overrides(model: SemanticModel, catalog: Optional[Dict[str, Any]] = None) -> SemanticModel:
    """
    Return a new SemanticModel with catalog applied.

    - Metrics whose names appear in `disabled` are removed.
    - Catalog metrics replace auto metrics of the same name (or are appended).
    - Model.source becomes "user" when any override is present.
    """
    if catalog is None:
        catalog = empty_catalog()

    disabled = set(str(x) for x in (catalog.get("disabled") or []))
    user_metrics_raw: List[Dict[str, Any]] = list(catalog.get("metrics") or [])

    # Start from auto metrics, drop disabled
    kept: List[Metric] = [m for m in model.metrics if m.name not in disabled]

    by_name = {m.name: i for i, m in enumerate(kept)}

    for raw in user_metrics_raw:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name or name in disabled:
            continue
        try:
            um = metric_from_dict(raw)
            # Mark as user-origin for transparency
            if not um.reason or um.reason == "User-defined / overridden metric":
                um = replace(um, reason="User override / custom metric")
            if name in by_name:
                kept[by_name[name]] = um
            else:
                by_name[name] = len(kept)
                kept.append(um)
        except Exception:
            # Skip malformed entries rather than failing the whole model
            continue

    has_user = bool(user_metrics_raw) or bool(disabled)
    new_model = SemanticModel(
        table_name=model.table_name,
        entities=list(model.entities),
        dimensions=list(model.dimensions),
        metrics=kept,
        time_dimension=model.time_dimension,
        row_count=model.row_count,
        warnings=list(model.warnings),
        source="user" if has_user else model.source,
    )
    if has_user and "User metric overrides applied" not in new_model.warnings:
        new_model.warnings.append("User metric overrides applied from catalog")
    return new_model


def build_governed_semantic_model(
    workspace: Any,
    table_name: str,
    max_dim_cardinality: int = 200,
    catalog_root: Optional[Path] = None,
) -> SemanticModel:
    """
    Preferred entry point for agents and UI.

    1. Build the pure auto model
    2. Load + apply the persistent catalog for this table
    """
    model = build_semantic_model(workspace, table_name, max_dim_cardinality=max_dim_cardinality)
    cat = load_catalog(table_name, root=catalog_root)
    return apply_overrides(model, cat)


# ---------------------------------------------------------------------------
# High-level mutation helpers (used by UI)
# ---------------------------------------------------------------------------

def set_metric_override(
    table_name: str,
    metric: Metric,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Upsert a single metric into the catalog and persist."""
    cat = load_catalog(table_name, root=root)
    metrics = [m for m in (cat.get("metrics") or []) if str(m.get("name")) != metric.name]
    metrics.append(metric_to_dict(metric))
    cat["metrics"] = metrics
    # If it was disabled, re-enable it
    cat["disabled"] = [d for d in (cat.get("disabled") or []) if d != metric.name]
    save_catalog(table_name, cat, root=root)
    return cat


def disable_metric(table_name: str, metric_name: str, root: Optional[Path] = None) -> Dict[str, Any]:
    cat = load_catalog(table_name, root=root)
    disabled = set(cat.get("disabled") or [])
    disabled.add(metric_name)
    cat["disabled"] = sorted(disabled)
    # Also remove from explicit overrides so it does not re-appear
    cat["metrics"] = [m for m in (cat.get("metrics") or []) if str(m.get("name")) != metric_name]
    save_catalog(table_name, cat, root=root)
    return cat


def enable_metric(table_name: str, metric_name: str, root: Optional[Path] = None) -> Dict[str, Any]:
    cat = load_catalog(table_name, root=root)
    cat["disabled"] = [d for d in (cat.get("disabled") or []) if d != metric_name]
    save_catalog(table_name, cat, root=root)
    return cat


def reset_catalog(table_name: str, root: Optional[Path] = None) -> bool:
    """Delete the catalog file → fall back to pure auto model."""
    return delete_catalog(table_name, root=root)


def catalog_summary(table_name: str, root: Optional[Path] = None) -> Dict[str, Any]:
    cat = load_catalog(table_name, root=root)
    return {
        "table": table_name,
        "path": str(catalog_path(table_name, root=root)),
        "override_count": len(cat.get("metrics") or []),
        "disabled_count": len(cat.get("disabled") or []),
        "disabled": list(cat.get("disabled") or []),
        "notes": cat.get("notes") or "",
    }


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "CATALOG_ROOT",
    "catalog_path",
    "empty_catalog",
    "load_catalog",
    "save_catalog",
    "delete_catalog",
    "metric_to_dict",
    "metric_from_dict",
    "apply_overrides",
    "build_governed_semantic_model",
    "set_metric_override",
    "disable_metric",
    "enable_metric",
    "reset_catalog",
    "catalog_summary",
]
