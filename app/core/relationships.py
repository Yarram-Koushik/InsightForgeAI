"""
InsightForgeAI – Multi-table Relationships (Phase 3.3)

Detect, store, and use relationships between cleaned datasets so queries can
join safely without metric fan-out.

Industry behaviours
-------------------
- Auto-detect likely FK links from column names + value overlap
- Infer cardinality (1:1, 1:N, N:1, N:N) from uniqueness ratios
- Build shortest join paths across the relationship graph
- Fan-out guard: warn/block when a join would multiply metric rows
- Emit safe DuckDB JOIN SQL (quoted identifiers, explicit ON)
- Never invent tables/columns; fail closed on ambiguous paths

Single-table workflows (Phase 1–3.2) are unchanged when only one dataset
is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import re
from collections import defaultdict, deque

import pandas as pd


# ---------------------------------------------------------------------------
# Enums & core types
# ---------------------------------------------------------------------------

class Cardinality(str, Enum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"      # left 1 → right N
    MANY_TO_ONE = "many_to_one"      # left N → right 1  (typical fact→dim)
    MANY_TO_MANY = "many_to_many"
    UNKNOWN = "unknown"


class DetectionMethod(str, Enum):
    NAME = "name"
    OVERLAP = "overlap"
    NAME_AND_OVERLAP = "name_and_overlap"
    MANUAL = "manual"


@dataclass
class Relationship:
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    cardinality: Cardinality = Cardinality.UNKNOWN
    confidence: float = 0.5
    method: DetectionMethod = DetectionMethod.NAME
    reason: str = ""
    overlap_ratio: Optional[float] = None  # fraction of left keys found in right

    @property
    def key(self) -> str:
        return (
            f"{self.left_table}.{self.left_column}"
            f"→{self.right_table}.{self.right_column}"
        )

    def reverse(self) -> "Relationship":
        """Flip direction and invert cardinality."""
        inv = {
            Cardinality.ONE_TO_ONE: Cardinality.ONE_TO_ONE,
            Cardinality.ONE_TO_MANY: Cardinality.MANY_TO_ONE,
            Cardinality.MANY_TO_ONE: Cardinality.ONE_TO_MANY,
            Cardinality.MANY_TO_MANY: Cardinality.MANY_TO_MANY,
            Cardinality.UNKNOWN: Cardinality.UNKNOWN,
        }
        return Relationship(
            left_table=self.right_table,
            left_column=self.right_column,
            right_table=self.left_table,
            right_column=self.left_column,
            cardinality=inv[self.cardinality],
            confidence=self.confidence,
            method=self.method,
            reason=f"Reverse of {self.key}",
            overlap_ratio=None,
        )


@dataclass
class JoinStep:
    from_table: str
    to_table: str
    from_column: str
    to_column: str
    cardinality: Cardinality
    join_type: str = "LEFT"  # LEFT | INNER


@dataclass
class JoinPath:
    tables: List[str]
    steps: List[JoinStep] = field(default_factory=list)
    fan_out_risk: bool = False
    fan_out_reason: str = ""
    confidence: float = 1.0

    @property
    def is_empty(self) -> bool:
        return len(self.tables) <= 1


@dataclass
class RelationshipGraph:
    relationships: List[Relationship] = field(default_factory=list)
    tables: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def edges(self) -> List[Relationship]:
        return list(self.relationships)

    def summary_dict(self) -> Dict[str, Any]:
        return {
            "tables": self.tables,
            "relationship_count": len(self.relationships),
            "relationships": [
                {
                    **asdict(r),
                    "cardinality": r.cardinality.value,
                    "method": r.method.value,
                }
                for r in self.relationships
            ],
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _tokens(s: str) -> Set[str]:
    return set(_norm(s).split())


_ID_SUFFIXES = ("id", "key", "code", "uuid", "guid", "no", "num", "number")
_ENTITY_HINTS = (
    "customer", "user", "client", "order", "product", "item", "account",
    "student", "employee", "vendor", "supplier", "invoice", "transaction",
    "category", "region", "store", "department", "member", "subscriber",
)


def _looks_like_join_key(col: str) -> bool:
    t = _tokens(col)
    if not t:
        return False
    if any(x in t for x in _ID_SUFFIXES):
        return True
    # exact entity names sometimes used as keys
    if any(h in t for h in _ENTITY_HINTS) and len(t) <= 2:
        return True
    return False


def _strip_id_suffix(col: str) -> str:
    parts = _norm(col).split()
    while parts and parts[-1] in _ID_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def _name_match_score(col_a: str, table_a: str, col_b: str, table_b: str) -> Tuple[float, str]:
    """
    Score how likely col_a in table_a joins to col_b in table_b by name alone.
    """
    na, nb = _norm(col_a), _norm(col_b)
    ta, tb = _norm(table_a), _norm(table_b)

    # Exact column name match (customer_id = customer_id)
    if na == nb and _looks_like_join_key(col_a):
        return 0.85, f"exact key name '{col_a}'"

    # orders.customer_id ↔ customers.id / customers.customer_id
    a_entity = _strip_id_suffix(col_a)
    b_entity = _strip_id_suffix(col_b)

    # left key references right table entity: orders.customer_id → customers.*
    if a_entity and (a_entity in tb or a_entity == _strip_id_suffix(table_b)):
        if nb in ("id", "key", "code") or b_entity == a_entity or nb == na:
            return 0.8, f"'{col_a}' references entity of table '{table_b}'"

    # symmetric
    if b_entity and (b_entity in ta or b_entity == _strip_id_suffix(table_a)):
        if na in ("id", "key", "code") or a_entity == b_entity or na == nb:
            return 0.8, f"'{col_b}' references entity of table '{table_a}'"

    # shared entity token + id-ish
    if a_entity and a_entity == b_entity and a_entity:
        return 0.7, f"shared entity token '{a_entity}'"

    return 0.0, ""


def _unique_ratio(series: pd.Series) -> float:
    s = series.dropna()
    if len(s) == 0:
        return 0.0
    return float(s.nunique()) / float(len(s))


def _overlap_ratio(left: pd.Series, right: pd.Series, sample: int = 500) -> float:
    """Fraction of non-null left values that appear in right (sampled)."""
    lvals = left.dropna().astype(str)
    rvals = right.dropna().astype(str)
    if lvals.empty or rvals.empty:
        return 0.0
    if len(lvals) > sample:
        lvals = lvals.sample(sample, random_state=42)
    rset = set(rvals.unique().tolist())
    if not rset:
        return 0.0
    hits = sum(1 for v in lvals if v in rset)
    return hits / max(len(lvals), 1)


def _infer_cardinality(left_ur: float, right_ur: float) -> Cardinality:
    """
    Heuristic from uniqueness ratios on the join keys.
    high unique ≈ primary-side.
    """
    left_unique = left_ur >= 0.95
    right_unique = right_ur >= 0.95
    left_low = left_ur < 0.9
    right_low = right_ur < 0.9

    if left_unique and right_unique:
        return Cardinality.ONE_TO_ONE
    if left_unique and right_low:
        return Cardinality.ONE_TO_MANY
    if left_low and right_unique:
        return Cardinality.MANY_TO_ONE
    if left_low and right_low:
        return Cardinality.MANY_TO_MANY
    return Cardinality.UNKNOWN


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_relationships(
    workspace: Any,
    table_names: Optional[Sequence[str]] = None,
    *,
    min_name_score: float = 0.65,
    min_overlap: float = 0.3,
    min_confidence: float = 0.55,
    max_pairs_per_table: int = 8,
) -> RelationshipGraph:
    """
    Auto-detect relationships among loaded datasets.

    Combines name heuristics with sampled value overlap.
    """
    graph = RelationshipGraph()
    if workspace is None:
        graph.warnings.append("Workspace is None.")
        return graph

    names = list(table_names) if table_names else list(workspace.list_datasets())
    graph.tables = names

    if len(names) < 2:
        graph.warnings.append("Need at least two datasets to detect relationships.")
        return graph

    # Cache frames + key-like columns
    frames: Dict[str, pd.DataFrame] = {}
    key_cols: Dict[str, List[str]] = {}
    for t in names:
        rec = workspace.get(t)
        if rec is None or getattr(rec, "cleaned_df", None) is None:
            graph.warnings.append(f"Skipping missing dataset: {t}")
            continue
        df = rec.cleaned_df
        if df is None or df.empty:
            continue
        frames[t] = df
        keys = [c for c in df.columns if _looks_like_join_key(c)]
        # Always consider columns named exactly like other table entities later
        if not keys:
            # fallback: any column with 'id' in name
            keys = [c for c in df.columns if "id" in _norm(c)]
        key_cols[t] = keys[:max_pairs_per_table]

    tables = list(frames.keys())
    seen: Set[str] = set()

    for i, left in enumerate(tables):
        for right in tables[i + 1 :]:
            ldf, rdf = frames[left], frames[right]
            candidates: List[Tuple[float, str, str, str]] = []

            # Name-based candidate pairs
            for lc in key_cols.get(left, list(ldf.columns)[:12]):
                for rc in key_cols.get(right, list(rdf.columns)[:12]):
                    score, reason = _name_match_score(lc, left, rc, right)
                    if score >= min_name_score:
                        candidates.append((score, lc, rc, reason))

            # Also try primary-ish columns of one vs entity keys of the other
            for lc in key_cols.get(left, []):
                for rc in rdf.columns:
                    if rc in key_cols.get(right, []):
                        continue
                    score, reason = _name_match_score(lc, left, rc, right)
                    if score >= min_name_score:
                        candidates.append((score, lc, rc, reason))

            # Dedup by column pair, keep best name score
            best_by_pair: Dict[Tuple[str, str], Tuple[float, str]] = {}
            for score, lc, rc, reason in candidates:
                k = (lc, rc)
                if k not in best_by_pair or score > best_by_pair[k][0]:
                    best_by_pair[k] = (score, reason)

            ranked = sorted(best_by_pair.items(), key=lambda x: -x[1][0])[:max_pairs_per_table]

            for (lc, rc), (name_score, reason) in ranked:
                try:
                    ov = _overlap_ratio(ldf[lc], rdf[rc])
                    ov_rev = _overlap_ratio(rdf[rc], ldf[lc])
                except Exception:
                    ov, ov_rev = 0.0, 0.0

                # Prefer direction with higher overlap from "fact-like" side
                left_ur = _unique_ratio(ldf[lc])
                right_ur = _unique_ratio(rdf[rc])

                conf = name_score
                method = DetectionMethod.NAME
                if ov >= min_overlap or ov_rev >= min_overlap:
                    conf = min(0.98, name_score * 0.6 + max(ov, ov_rev) * 0.5)
                    method = (
                        DetectionMethod.NAME_AND_OVERLAP
                        if name_score >= min_name_score
                        else DetectionMethod.OVERLAP
                    )

                if conf < min_confidence:
                    continue

                # Choose orientation: MANY_TO_ONE preferred (N→1) when clear
                card = _infer_cardinality(left_ur, right_ur)
                use_left, use_right = left, right
                use_lc, use_rc = lc, rc
                use_card = card
                use_ov = ov

                # If right is unique and left is not → already N:1 left→right (good)
                # If left is unique and right is not → 1:N left→right; flip to N:1
                if card == Cardinality.ONE_TO_MANY:
                    use_left, use_right = right, left
                    use_lc, use_rc = rc, lc
                    use_card = Cardinality.MANY_TO_ONE
                    use_ov = ov_rev
                elif card == Cardinality.MANY_TO_ONE:
                    pass
                elif ov_rev > ov + 0.15:
                    # Stronger evidence other direction
                    use_left, use_right = right, left
                    use_lc, use_rc = rc, lc
                    use_card = _infer_cardinality(right_ur, left_ur)
                    use_ov = ov_rev

                rel_key = f"{use_left}.{use_lc}|{use_right}.{use_rc}"
                rev_key = f"{use_right}.{use_rc}|{use_left}.{use_lc}"
                if rel_key in seen or rev_key in seen:
                    continue
                seen.add(rel_key)

                graph.relationships.append(
                    Relationship(
                        left_table=use_left,
                        left_column=use_lc,
                        right_table=use_right,
                        right_column=use_rc,
                        cardinality=use_card,
                        confidence=round(conf, 3),
                        method=method,
                        reason=reason or "heuristic match",
                        overlap_ratio=round(use_ov, 3) if use_ov is not None else None,
                    )
                )

    if not graph.relationships and len(tables) >= 2:
        graph.warnings.append(
            "No relationships detected above confidence threshold. "
            "You can still query single tables."
        )

    # Stable order: higher confidence first
    graph.relationships.sort(key=lambda r: -r.confidence)
    return graph


# ---------------------------------------------------------------------------
# Join path finding
# ---------------------------------------------------------------------------

def _adjacency(graph: RelationshipGraph) -> Dict[str, List[Relationship]]:
    adj: Dict[str, List[Relationship]] = defaultdict(list)
    for r in graph.relationships:
        adj[r.left_table].append(r)
        adj[r.right_table].append(r.reverse())
    return adj


def find_join_path(
    graph: RelationshipGraph,
    start: str,
    end: str,
    *,
    max_hops: int = 4,
) -> Optional[JoinPath]:
    """
    Shortest path of relationships from start table to end table.
    Marks fan-out risk if any hop is 1:N in the traversal direction.
    """
    if start == end:
        return JoinPath(tables=[start], confidence=1.0)

    if start not in graph.tables or end not in graph.tables:
        return None

    adj = _adjacency(graph)
    # BFS: state = (table, path_of_relationships_oriented)
    queue = deque([(start, [])])
    visited = {start}

    while queue:
        node, path = queue.popleft()
        if len(path) >= max_hops:
            continue
        for rel in adj.get(node, []):
            # rel is oriented: left=node side after reverse() handling
            nxt = rel.right_table if rel.left_table == node else rel.left_table
            # After reverse(), left is always the "from" side we appended
            if rel.left_table != node:
                continue
            if nxt in visited and nxt != end:
                continue
            new_path = path + [rel]
            if nxt == end:
                return _path_from_rels(start, new_path)
            visited.add(nxt)
            queue.append((nxt, new_path))

    return None


def _path_from_rels(start: str, rels: List[Relationship]) -> JoinPath:
    tables = [start]
    steps: List[JoinStep] = []
    fan_out = False
    fan_reasons: List[str] = []
    conf = 1.0

    current = start
    for rel in rels:
        # Orient so we join current → other
        if rel.left_table == current:
            frm, to = rel.left_table, rel.right_table
            fc, tc = rel.left_column, rel.right_column
            card = rel.cardinality
        else:
            rev = rel.reverse()
            frm, to = rev.left_table, rev.right_table
            fc, tc = rev.left_column, rev.right_column
            card = rev.cardinality

        # Fan-out: expanding row count relative to metric grain
        if card in (Cardinality.ONE_TO_MANY, Cardinality.MANY_TO_MANY):
            fan_out = True
            fan_reasons.append(
                f"{frm}→{to} is {card.value} on {fc}={tc}"
            )

        steps.append(
            JoinStep(
                from_table=frm,
                to_table=to,
                from_column=fc,
                to_column=tc,
                cardinality=card,
                join_type="LEFT",
            )
        )
        tables.append(to)
        current = to
        conf = min(conf, rel.confidence)

    return JoinPath(
        tables=tables,
        steps=steps,
        fan_out_risk=fan_out,
        fan_out_reason="; ".join(fan_reasons),
        confidence=conf,
    )


def find_paths_from(
    graph: RelationshipGraph,
    start: str,
    *,
    max_hops: int = 2,
) -> List[JoinPath]:
    """All reachable tables within max_hops (for schema context)."""
    paths: List[JoinPath] = []
    for t in graph.tables:
        if t == start:
            continue
        p = find_join_path(graph, start, t, max_hops=max_hops)
        if p is not None:
            paths.append(p)
    paths.sort(key=lambda p: (len(p.steps), -p.confidence))
    return paths


# ---------------------------------------------------------------------------
# SQL emission
# ---------------------------------------------------------------------------

def compile_join_sql(
    path: JoinPath,
    *,
    select_columns: Optional[List[Tuple[str, str]]] = None,
    # list of (table, column) or ("*", "*") 
    where_sql: Optional[str] = None,
    limit: int = 100,
    block_fan_out: bool = False,
) -> Tuple[Optional[str], List[str], Optional[str]]:
    """
    Build a SELECT ... FROM t0 JOIN t1 ON ... SQL string.

    Returns (sql, warnings, error).
    """
    warnings: List[str] = []
    if path is None or not path.tables:
        return None, warnings, "Empty join path."

    if path.fan_out_risk:
        msg = f"Fan-out risk: {path.fan_out_reason}"
        if block_fan_out:
            return None, warnings, msg + " — join blocked to protect metric grain."
        warnings.append(msg + " — results may duplicate rows; prefer aggregating after join carefully.")

    base = path.tables[0]
    lines = ["SELECT"]

    if select_columns:
        parts = []
        for t, c in select_columns:
            if c == "*":
                parts.append(f"{_q(t)}.*")
            else:
                parts.append(f"{_q(t)}.{_q(c)} AS {_q(f'{t}__{c}')}")
        lines.append("  " + ",\n  ".join(parts))
    else:
        # Default: all columns from all tables with prefixes via aliases hard in DuckDB –
        # keep simple: base.* plus joined keys only to avoid explosion
        lines.append(f"  {_q(base)}.*")
        for step in path.steps:
            lines[1] += f",\n  {_q(step.to_table)}.{_q(step.to_column)} AS {_q(step.to_table + '__' + step.to_column)}"

    lines.append(f"FROM {_q(base)}")
    for step in path.steps:
        jt = step.join_type.upper() if step.join_type else "LEFT"
        if jt not in ("LEFT", "INNER", "RIGHT"):
            jt = "LEFT"
        lines.append(
            f"{jt} JOIN {_q(step.to_table)} "
            f"ON {_q(step.from_table)}.{_q(step.from_column)} "
            f"= {_q(step.to_table)}.{_q(step.to_column)}"
        )

    if where_sql:
        lines.append(f"WHERE {where_sql}")

    limit_i = max(1, min(int(limit or 100), 5000))
    lines.append(f"LIMIT {limit_i}")
    return "\n".join(lines), warnings, None


# ---------------------------------------------------------------------------
# Prompt / schema context helpers
# ---------------------------------------------------------------------------

def relationships_prompt_block(graph: RelationshipGraph, primary_table: Optional[str] = None) -> str:
    """Compact block for NL→SQL prompts."""
    if not graph.relationships:
        return "RELATIONSHIPS: none detected (single-table or below confidence)."

    lines = ["RELATIONSHIPS (use only these for JOINs):"]
    for r in graph.relationships[:12]:
        lines.append(
            f"- {_q(r.left_table)}.{_q(r.left_column)} → "
            f"{_q(r.right_table)}.{_q(r.right_column)} "
            f"[{r.cardinality.value}, conf={r.confidence}]"
            f"  // {r.reason}"
        )
    lines.append(
        "JOIN RULES: always use explicit ON; prefer MANY_TO_ONE (fact→dimension); "
        "avoid 1:N joins when aggregating metrics (fan-out)."
    )
    if primary_table:
        paths = find_paths_from(graph, primary_table, max_hops=2)
        if paths:
            lines.append(f"Reachable from {primary_table}:")
            for p in paths[:6]:
                chain = " → ".join(p.tables)
                risk = " [FAN-OUT]" if p.fan_out_risk else ""
                lines.append(f"  - {chain}{risk}")
    return "\n".join(lines)


def build_workspace_relationship_graph(workspace: Any) -> RelationshipGraph:
    """Convenience entry point used by agents / NL→SQL."""
    try:
        return detect_relationships(workspace)
    except Exception as e:
        g = RelationshipGraph()
        g.warnings.append(f"Relationship detection failed: {e}")
        try:
            g.tables = list(workspace.list_datasets())
        except Exception:
            pass
        return g


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "Cardinality",
    "DetectionMethod",
    "Relationship",
    "JoinStep",
    "JoinPath",
    "RelationshipGraph",
    "detect_relationships",
    "find_join_path",
    "find_paths_from",
    "compile_join_sql",
    "relationships_prompt_block",
    "build_workspace_relationship_graph",
]
