"""
InsightForgeAI – SQL Security & Structural Validation (Phase 2.7)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Set
import re

FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "GRANT", "REVOKE", "ATTACH", "DETACH", "COPY", "EXPORT",
    "IMPORT", "INSTALL", "LOAD", "PRAGMA", "CALL", "EXECUTE", "EXEC",
    "VACUUM", "CHECKPOINT", "SET", "RESET", "BEGIN", "COMMIT", "ROLLBACK",
    "MERGE", "UPSERT", "INTO",
}
ALLOWED_HEADS = {"SELECT", "WITH", "DESCRIBE", "DESC", "SHOW", "EXPLAIN", "SUMMARIZE"}


@dataclass
class GuardResult:
    ok: bool
    sql: str
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    referenced_tables: List[str] = field(default_factory=list)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--.*?$", " ", sql, flags=re.MULTILINE)
    return sql


def _normalize(sql: str) -> str:
    return _strip_comments(sql or "").strip().rstrip(";").strip()


def validate_readonly(sql: str) -> GuardResult:
    cleaned = _normalize(sql)
    if not cleaned:
        return GuardResult(ok=False, sql=cleaned, error="Empty SQL.")
    if ";" in cleaned:
        return GuardResult(ok=False, sql=cleaned, error="Multiple SQL statements are not allowed.")
    upper = cleaned.upper()
    head = upper.split(None, 1)[0] if upper else ""
    if head not in ALLOWED_HEADS:
        return GuardResult(ok=False, sql=cleaned, error=f"Only read-only statements allowed. Got: {head}")
    tokens = set(re.findall(r"[A-Z_][A-Z0-9_]*", upper))
    bad = tokens & FORBIDDEN_KEYWORDS
    if bad:
        return GuardResult(ok=False, sql=cleaned, error=f"Forbidden SQL keyword(s): {', '.join(sorted(bad))}")
    if re.search(r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE)\b", upper):
        return GuardResult(ok=False, sql=cleaned, error="Chained destructive statement detected.")
    return GuardResult(ok=True, sql=cleaned)


def extract_table_names(sql: str) -> List[str]:
    cleaned = _normalize(sql)
    names = []
    for m in re.finditer(r'\b(?:FROM|JOIN)\s+("?[\w\.]+"?|\[[\w\.]+\])', cleaned, flags=re.IGNORECASE):
        raw = m.group(1).strip().strip('"').strip("`").strip("[]")
        if raw and raw.upper() not in {"SELECT", "WHERE", "GROUP", "ORDER", "LIMIT"}:
            names.append(raw.split(".")[-1])
    return list(dict.fromkeys(names))


def validate_against_schema(sql: str, allowed_tables: Optional[Set[str]] = None, allowed_columns: Optional[Set[str]] = None) -> GuardResult:
    base = validate_readonly(sql)
    if not base.ok:
        return base
    tables = extract_table_names(base.sql)
    base.referenced_tables = tables
    warnings = list(base.warnings)
    if allowed_tables is not None and tables:
        unknown = [t for t in tables if t not in allowed_tables and t.lower() not in {x.lower() for x in allowed_tables}]
        if unknown:
            return GuardResult(ok=False, sql=base.sql, error=f"SQL references unknown table(s): {', '.join(unknown)}", referenced_tables=tables)
    upper = base.sql.upper()
    if " JOIN " in upper and " ON " not in upper and " USING " not in upper:
        warnings.append("JOIN without ON/USING detected — possible cartesian product.")
    return GuardResult(ok=True, sql=base.sql, warnings=warnings, referenced_tables=tables)


def sanitize_for_llm_prompt(user_text: str) -> str:
    t = (user_text or "")
    blocked = ["ignore previous instructions", "ignore all instructions", "system prompt", "you are now", "drop table", "delete from"]
    low = t.lower()
    for b in blocked:
        if b in low:
            t = re.sub(re.escape(b), "[filtered]", t, flags=re.IGNORECASE)
    if len(t) > 200:
        t = t[:200] + "…"
    return t
