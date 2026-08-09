"""
InsightForgeAI – Metric Contract (Phase 3.1 completion)

Closes the remaining enterprise gaps on the semantic metric layer:

1. Versioned definitions – metrics carry version; catalog keeps history
2. Required columns + grain + owner + domain (finance vs ops)
3. Resolver statuses:
     RESOLVED | AMBIGUOUS | CANNOT_COMPUTE | NO_MATCH
4. Conflicting candidates → clarify (do not guess)
5. Missing columns → clear cannot_compute message

Used by NL→SQL, Metric Compiler, and Governance UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import re

try:
    from app.core.semantic_layer import (
        AggType,
        Additivity,
        Metric,
        SemanticModel,
        resolve_metrics_for_question,
        _norm,
        _token_set,
        _q,
    )
except Exception:
    import importlib.util
    import sys
    from pathlib import Path

    _core = Path(__file__).resolve().parent
    if str(_core.parent.parent) not in sys.path:
        sys.path.insert(0, str(_core.parent.parent))
    _spec = importlib.util.spec_from_file_location("semantic_layer", _core / "semantic_layer.py")
    _sl = importlib.util.module_from_spec(_spec)
    sys.modules["semantic_layer"] = _sl
    _spec.loader.exec_module(_sl)
    AggType = _sl.AggType
    Additivity = _sl.Additivity
    Metric = _sl.Metric
    SemanticModel = _sl.SemanticModel
    resolve_metrics_for_question = _sl.resolve_metrics_for_question
    _norm = _sl._norm
    _token_set = _sl._token_set
    _q = _sl._q


class ResolveStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    CANNOT_COMPUTE = "cannot_compute"
    NO_MATCH = "no_match"


@dataclass
class MetricCandidate:
    metric: Metric
    score: float
    reason: str
    missing_columns: List[str] = field(default_factory=list)

    @property
    def computable(self) -> bool:
        return not self.missing_columns


@dataclass
class MetricResolution:
    status: ResolveStatus
    question: str
    primary: Optional[MetricCandidate] = None
    candidates: List[MetricCandidate] = field(default_factory=list)
    clarify_questions: List[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        def _cand(c: MetricCandidate) -> Dict[str, Any]:
            m = c.metric
            return {
                "name": m.name,
                "label": m.label,
                "domain": getattr(m, "domain", "") or "",
                "version": getattr(m, "version", 1),
                "owner": getattr(m, "owner", "") or "",
                "grain": getattr(m, "grain", "") or "",
                "sql": m.sql_expression(),
                "score": round(c.score, 3),
                "reason": c.reason,
                "missing_columns": list(c.missing_columns),
                "computable": c.computable,
            }

        return {
            "status": self.status.value,
            "question": self.question,
            "message": self.message,
            "primary": _cand(self.primary) if self.primary else None,
            "candidates": [_cand(c) for c in self.candidates],
            "clarify_questions": list(self.clarify_questions),
        }


def metric_required_columns(m: Metric) -> List[str]:
    explicit = getattr(m, "required_columns", None) or []
    if explicit:
        return [str(c) for c in explicit if c]

    cols: List[str] = []
    for attr in ("measure_column", "entity_column", "numerator", "denominator"):
        v = getattr(m, attr, None)
        if v and isinstance(v, str) and not v.startswith("metric:"):
            cols.append(v)
    expr = getattr(m, "expr", None) or ""
    if expr:
        cols.extend(re.findall(r'"([^"]+)"', expr))
    seen: Set[str] = set()
    out: List[str] = []
    for c in cols:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def missing_columns_for_metric(m: Metric, available: Sequence[str]) -> List[str]:
    avail = {str(c) for c in available}
    return [c for c in metric_required_columns(m) if c not in avail]


def enrich_metric_contract_fields(m: Metric) -> Metric:
    updates = {}
    if not hasattr(m, "version") or getattr(m, "version", None) is None:
        updates["version"] = 1
    if not hasattr(m, "owner"):
        updates["owner"] = "system"
    if not hasattr(m, "grain"):
        if m.agg == AggType.COUNT:
            updates["grain"] = "row"
        elif m.agg == AggType.COUNT_DISTINCT:
            updates["grain"] = getattr(m, "entity_column", None) or "entity"
        elif m.agg == AggType.RATIO:
            updates["grain"] = getattr(m, "entity_column", None) or "order"
        else:
            updates["grain"] = "row"
    if not hasattr(m, "domain"):
        tags = set(m.tags or [])
        if "revenue" in tags or m.name in ("aov",):
            updates["domain"] = "finance"
        elif "customers" in tags:
            updates["domain"] = "ops"
        else:
            updates["domain"] = "general"
    if not hasattr(m, "required_columns"):
        updates["required_columns"] = metric_required_columns(m)

    if not updates:
        return m
    try:
        known = {f.name for f in m.__dataclass_fields__.values()}  # type: ignore
        replace_kwargs = {k: v for k, v in updates.items() if k in known}
        extra = {k: v for k, v in updates.items() if k not in known}
        nm = replace(m, **replace_kwargs) if replace_kwargs else m
        for k, v in extra.items():
            try:
                object.__setattr__(nm, k, v)
            except Exception:
                setattr(nm, k, v)
        return nm
    except Exception:
        for k, v in updates.items():
            try:
                setattr(m, k, v)
            except Exception:
                object.__setattr__(m, k, v)
        return m


def _score_metric(question: str, m: Metric) -> Tuple[float, str]:
    q = _norm(question)
    if not q:
        return 0.0, "empty question"

    wants_unique = any(w in q for w in ("unique", "distinct", "how many different"))
    wants_avg = any(w in q for w in ("average", "avg", "mean"))
    wants_sum = any(w in q for w in ("total", "sum", "revenue", "sales", "gmv"))
    wants_count = any(w in q for w in ("how many", "count", "number of", "volume"))
    wants_aov = any(w in q for w in ("aov", "average order", "avg order", "order value"))

    score = 0.0
    bits: List[str] = []

    if wants_aov and (m.name == "aov" or "aov" in (m.tags or [])):
        score += 10
        bits.append("AOV match")
    if wants_unique and m.agg == AggType.COUNT_DISTINCT:
        score += 6
        bits.append("distinct count")
    if wants_sum and m.agg == AggType.SUM:
        score += 5
        bits.append("sum/total")
    if wants_avg and m.agg == AggType.AVG and not wants_aov:
        score += 5
        bits.append("average")
    if wants_count and m.agg == AggType.COUNT and not wants_unique:
        score += 4
        bits.append("row count")

    tokens = _token_set(m.label) | _token_set(m.name) | set(m.tags or [])
    domain = getattr(m, "domain", "") or ""
    if domain:
        tokens |= _token_set(domain)
    overlap = tokens & _token_set(q)
    if overlap:
        score += 1.5 * len(overlap)
        bits.append(f"overlap:{','.join(sorted(overlap)[:3])}")

    if m.preferred:
        score += 0.8
    score += float(m.confidence or 0) * 0.5

    if wants_aov and m.agg == AggType.AVG:
        score -= 4
        bits.append("penalised AVG for AOV")

    return score, "; ".join(bits) or (m.reason or "")


def resolve_metric_contract(
    question: str,
    model: SemanticModel,
    *,
    available_columns: Optional[Sequence[str]] = None,
    top_k: int = 8,
    ambiguity_margin: float = 1.5,
    min_score: float = 1.0,
) -> MetricResolution:
    q = (question or "").strip()
    if not q or not model.metrics:
        return MetricResolution(
            status=ResolveStatus.NO_MATCH,
            question=q,
            message="No metrics available for this table.",
        )

    if available_columns is None:
        cols: List[str] = []
        for e in model.entities:
            cols.append(e.column)
        for d in model.dimensions:
            cols.append(d.column)
        for m in model.metrics:
            cols.extend(metric_required_columns(m))
        available_columns = list(dict.fromkeys(cols))

    scored: List[MetricCandidate] = []
    for raw in model.metrics:
        m = enrich_metric_contract_fields(raw)
        score, reason = _score_metric(q, m)
        if score < min_score:
            continue
        missing = missing_columns_for_metric(m, available_columns)
        scored.append(MetricCandidate(metric=m, score=score, reason=reason, missing_columns=missing))

    scored.sort(key=lambda c: -c.score)
    scored = scored[:top_k]

    if not scored:
        return MetricResolution(
            status=ResolveStatus.NO_MATCH,
            question=q,
            message="No metric matched this question.",
            clarify_questions=[
                "Which metric do you mean – total, average, or unique count?",
                "Which column should we aggregate?",
            ],
        )

    best = scored[0]

    ambiguous_peers = []
    for c in scored[1:]:
        if best.score - c.score > ambiguity_margin:
            break
        same_label = _norm(c.metric.label) == _norm(best.metric.label)
        diff_sql = c.metric.sql_expression() != best.metric.sql_expression()
        diff_domain = (getattr(c.metric, "domain", "") or "") != (getattr(best.metric, "domain", "") or "")
        if (same_label and diff_sql) or diff_domain or (diff_sql and abs(best.score - c.score) < 0.75):
            ambiguous_peers.append(c)

    if ambiguous_peers:
        peers = [best] + ambiguous_peers
        clarify = []
        for c in peers:
            dom = getattr(c.metric, "domain", "") or "general"
            owner = getattr(c.metric, "owner", "") or "system"
            ver = getattr(c.metric, "version", 1)
            clarify.append(
                f"Did you mean {c.metric.label} [{dom}/v{ver} by {owner}]: {c.metric.sql_expression()}?"
            )
        return MetricResolution(
            status=ResolveStatus.AMBIGUOUS,
            question=q,
            primary=best,
            candidates=peers,
            clarify_questions=clarify,
            message=(
                f"Multiple metric definitions match this question. "
                f"Please clarify which one to use ({len(peers)} candidates)."
            ),
        )

    if best.missing_columns:
        return MetricResolution(
            status=ResolveStatus.CANNOT_COMPUTE,
            question=q,
            primary=best,
            candidates=scored,
            message=(
                f"Metric `{best.metric.name}` (`{best.metric.label}`) cannot be computed: "
                f"missing columns {best.missing_columns}."
            ),
            clarify_questions=[
                f"Upload or join data that includes: {', '.join(best.missing_columns)}",
            ],
        )

    return MetricResolution(
        status=ResolveStatus.RESOLVED,
        question=q,
        primary=best,
        candidates=scored,
        message=f"Resolved to `{best.metric.name}` v{getattr(best.metric, 'version', 1)}.",
    )


def resolution_prompt_block(resolution: MetricResolution) -> str:
    lines = ["METRIC CONTRACT RESOLUTION:"]
    lines.append(f"Status: {resolution.status.value}")
    lines.append(resolution.message)
    if resolution.status == ResolveStatus.RESOLVED and resolution.primary:
        c = resolution.primary
        m = c.metric
        lines.append(f"USE THIS EXPRESSION (do not invent another): {m.sql_expression()}")
        lines.append(
            f"Metric: {m.name} | domain={getattr(m,'domain','')} | "
            f"v{getattr(m,'version',1)} | owner={getattr(m,'owner','system')} | "
            f"grain={getattr(m,'grain','')}"
        )
    elif resolution.status == ResolveStatus.AMBIGUOUS:
        lines.append("Do NOT guess. Ask the user to pick one of:")
        for q in resolution.clarify_questions:
            lines.append(f"  - {q}")
    elif resolution.status == ResolveStatus.CANNOT_COMPUTE:
        lines.append("Do NOT invent substitute columns. Report cannot compute.")
        for q in resolution.clarify_questions:
            lines.append(f"  - {q}")
    return "\n".join(lines)


def bump_metric_version(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    new = dict(new)
    history = list((old or {}).get("history") or [])
    if old:
        prev_sql = (old.get("expr") or old.get("sql_preview") or "")
        new_sql = (new.get("expr") or new.get("sql_preview") or "")
        changed = (
            prev_sql != new_sql
            or old.get("domain") != new.get("domain")
            or old.get("grain") != new.get("grain")
            or old.get("measure_column") != new.get("measure_column")
        )
        if changed:
            snap = {k: v for k, v in old.items() if k != "history"}
            history.append(snap)
            try:
                new["version"] = int(old.get("version") or 1) + 1
            except Exception:
                new["version"] = 2
        else:
            new["version"] = int(old.get("version") or 1)
    else:
        new.setdefault("version", 1)
    new["history"] = history[-20:]
    return new


__all__ = [
    "ResolveStatus",
    "MetricCandidate",
    "MetricResolution",
    "metric_required_columns",
    "missing_columns_for_metric",
    "enrich_metric_contract_fields",
    "resolve_metric_contract",
    "resolution_prompt_block",
    "bump_metric_version",
]
