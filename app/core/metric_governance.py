"""
InsightForgeAI – Metric Governance (Phase 3.5 + 3.1 contract)

Persistent, user-overridable metric catalog on top of the auto Semantic Layer.

- Browse / override / disable auto-discovered metrics
- Add custom metrics (including free-form expressions)
- Save / load as JSON under data/metric_catalog/{table}.json
- Version-aware overrides (history[] + version++) when definition changes
- Contract fields: owner, domain, grain, required_columns
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.semantic_layer import (
    AggType,
    Additivity,
    Metric,
    SemanticModel,
    build_semantic_model,
)


def _default_catalog_root() -> Path:
    here = Path(__file__).resolve()
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


def metric_to_dict(m: Metric) -> Dict[str, Any]:
    d = asdict(m)
    d["agg"] = m.agg.value if isinstance(m.agg, AggType) else str(m.agg)
    d["additivity"] = m.additivity.value if isinstance(m.additivity, Additivity) else str(m.additivity)
    d["version"] = int(getattr(m, "version", 1) or 1)
    d["owner"] = str(getattr(m, "owner", "system") or "system")
    d["grain"] = str(getattr(m, "grain", "row") or "row")
    d["domain"] = str(getattr(m, "domain", "general") or "general")
    d["required_columns"] = list(getattr(m, "required_columns", None) or [])
    try:
        d["sql_preview"] = m.sql_expression()
    except Exception:
        d["sql_preview"] = None
    return d


def metric_from_dict(d: Dict[str, Any]) -> Metric:
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
        version=int(d.get("version", 1) or 1),
        owner=str(d.get("owner") or "user"),
        grain=str(d.get("grain") or "row"),
        domain=str(d.get("domain") or "general"),
        required_columns=list(d.get("required_columns") or []),
    )


def empty_catalog() -> Dict[str, Any]:
    return {"version": 1, "disabled": [], "metrics": [], "notes": ""}


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


def apply_overrides(model: SemanticModel, catalog: Optional[Dict[str, Any]] = None) -> SemanticModel:
    if catalog is None:
        catalog = empty_catalog()

    disabled = set(str(x) for x in (catalog.get("disabled") or []))
    user_metrics_raw: List[Dict[str, Any]] = list(catalog.get("metrics") or [])

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
            if not um.reason or um.reason == "User-defined / overridden metric":
                um = replace(um, reason="User override / custom metric")
            if name in by_name:
                kept[by_name[name]] = um
            else:
                by_name[name] = len(kept)
                kept.append(um)
        except Exception:
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
    model = build_semantic_model(workspace, table_name, max_dim_cardinality=max_dim_cardinality)
    cat = load_catalog(table_name, root=catalog_root)
    return apply_overrides(model, cat)


def set_metric_override(
    table_name: str,
    metric: Metric,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Upsert a single metric into the catalog and persist (version-aware)."""
    cat = load_catalog(table_name, root=root)
    existing = None
    remaining = []
    for m in (cat.get("metrics") or []):
        if str(m.get("name")) == metric.name:
            existing = m
        else:
            remaining.append(m)
    payload = metric_to_dict(metric)
    try:
        from app.core.metric_contract import bump_metric_version
        payload = bump_metric_version(existing, payload)
    except Exception:
        if existing:
            try:
                payload["version"] = int(existing.get("version") or 1) + (
                    1 if (existing.get("expr") or existing.get("sql_preview")) != payload.get("sql_preview") else 0
                )
            except Exception:
                payload["version"] = int(existing.get("version") or 1)
        payload.setdefault("history", list((existing or {}).get("history") or []))
    remaining.append(payload)
    cat["metrics"] = remaining
    cat["disabled"] = [d for d in (cat.get("disabled") or []) if d != metric.name]
    save_catalog(table_name, cat, root=root)
    return cat


def disable_metric(table_name: str, metric_name: str, root: Optional[Path] = None) -> Dict[str, Any]:
    cat = load_catalog(table_name, root=root)
    disabled = set(cat.get("disabled") or [])
    disabled.add(metric_name)
    cat["disabled"] = sorted(disabled)
    cat["metrics"] = [m for m in (cat.get("metrics") or []) if str(m.get("name")) != metric_name]
    save_catalog(table_name, cat, root=root)
    return cat


def enable_metric(table_name: str, metric_name: str, root: Optional[Path] = None) -> Dict[str, Any]:
    cat = load_catalog(table_name, root=root)
    cat["disabled"] = [d for d in (cat.get("disabled") or []) if d != metric_name]
    save_catalog(table_name, cat, root=root)
    return cat


def reset_catalog(table_name: str, root: Optional[Path] = None) -> bool:
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
