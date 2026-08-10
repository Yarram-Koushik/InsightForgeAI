"""NL → SQL with semantic metrics, time intelligence, and multi-table joins."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_CORE_DIR = Path(__file__).resolve().parent
_ROOT = _CORE_DIR.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import importlib.util as _ilu


def _load_mod(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(name, path)
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _get_semantic_model(workspace, table_name: str):
    _sl_path = _CORE_DIR / "semantic_layer.py"
    if not _sl_path.exists():
        raise FileNotFoundError("semantic_layer.py missing")
    _sl_spec = _ilu.spec_from_file_location("_semantic_layer_shared", _sl_path)
    _sl = _ilu.module_from_spec(_sl_spec)
    sys.modules["_semantic_layer_shared"] = _sl
    _sl_spec.loader.exec_module(_sl)
    _gov_path = _CORE_DIR / "metric_governance.py"
    if _gov_path.exists():
        try:
            _gov_spec = _ilu.spec_from_file_location("_metric_gov_shared", _gov_path)
            _gov = _ilu.module_from_spec(_gov_spec)
            sys.modules["_metric_gov_shared"] = _gov
            _gov_spec.loader.exec_module(_gov)
            model = _gov.build_governed_semantic_model(workspace, table_name)
            return model, _sl
        except Exception:
            pass
    model = _sl.build_semantic_model(workspace, table_name)
    return model, _sl


def _schema_context(workspace, table_name: str) -> str:
    lines = [f"Primary table: {table_name}"]
    try:
        record = workspace.get(table_name)
        if record is None:
            return lines[0] + "\n(no record)"
        df = getattr(record, "cleaned_df", None)
        if df is None:
            return lines[0] + "\n(no data)"
        lines.append(f"Rows: {len(df)}")
        lines.append("Columns:")
        for c in df.columns:
            dtype = str(df[c].dtype)
            lines.append(f"  - {c} ({dtype})")
    except Exception as e:
        lines.append(f"schema error: {e}")
    return "\n".join(lines)


def _relationships_block(workspace, table_name: str) -> str:
    try:
        _rel_path = _CORE_DIR / "relationships.py"
        if not _rel_path.exists():
            return ""
        _rel = _load_mod("_relationships_shared", _rel_path)
        _graph = _rel.build_workspace_relationship_graph(workspace)
        lines = []
        lines.append(_rel.relationships_prompt_block(_graph, primary_table=table_name))
        return "\n".join(lines)
    except Exception:
        return ""


SYSTEM_PROMPT = """You are an expert DuckDB SQL generator for business analytics.

Rules:
1. Output ONLY a single DuckDB SQL statement. No markdown, no explanation.
2. Use double-quoted identifiers for table and column names when needed.
3. Prefer explicit column lists over SELECT *.
4. Always LIMIT large scans (default LIMIT 1000 unless aggregation).
5. Use NULL-safe patterns (NULLIF for division).
6. Do not invent tables or columns that are not in the schema.
7. Read-only: never DDL/DML (no DROP, DELETE, UPDATE, INSERT, CREATE, ALTER).
8. Use DuckDB dialect (not Postgres-only features).
9. Date/time handling: use DuckDB functions (DATE_TRUNC, strftime, etc.).
10. For percentages, ratios, growth: calculate carefully and name the output column clearly.
11. METRIC RULES (Semantic Layer – Phase 3.1):
    - Prefer the exact expressions listed under SEMANTIC METRICS when the question matches.
    - Average Order Value / AOV / any ratio → SUM(measure) / NULLIF(COUNT(DISTINCT entity), 0). NEVER AVG(measure) for AOV.
    - Unique customers / users / orders → COUNT(DISTINCT col). NEVER COUNT(*) for "unique".
    - NEVER SUM or AVG identifier columns (id, uuid, key, code).
    - Ratio metrics are NON-additive: compute the ratio after aggregation, do not average a ratio.
    - Always protect division with NULLIF(..., 0).
12. JOIN RULES (Phase 3.3):
    - Only JOIN tables using relationships listed under RELATIONSHIPS.
    - Always write explicit ON conditions; never Cartesian products.
    - Prefer fact→dimension (MANY_TO_ONE). Avoid 1:N joins when aggregating (fan-out doubles metrics).
    - If a question needs another table and no relationship exists, answer from the primary table only or return UNSUPPORTED.
    - When the user asks for attributes from a joined table (e.g. "customer names", "segment"),
      you MUST include those columns in the SELECT list (e.g. c."customer_name", c."segment").
      Do not only SELECT keys from the fact table when the user asked for dimension attributes.
    - Alias tables (o, c, …) and qualify every column as alias."col".

You will receive:
- The table schema (physical + semantic types)
- SEMANTIC METRICS (governed definitions) when available
- RELATIONSHIPS between loaded tables when available
- A natural language question

Respond with pure SQL only.
"""


def _extract_sql(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    if ";" in text:
        text = text.split(";")[0] + ";"
    return text.strip()


def generate_sql(
    question: str,
    workspace: Any,
    table_name: str,
    llm_client: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    meta: Dict[str, Any] = {"provider": None, "model": None, "warnings": []}
    schema = _schema_context(workspace, table_name)
    rel_block = _relationships_block(workspace, table_name)
    metric_block = ""
    try:
        model, sl = _get_semantic_model(workspace, table_name)
        if hasattr(sl, "metric_prompt_block"):
            metric_block = sl.metric_prompt_block(question, model)
        elif hasattr(sl, "model_prompt_summary"):
            metric_block = sl.model_prompt_summary(model)
    except Exception as e:
        meta["warnings"].append(f"semantic layer unavailable: {e}")

    user_parts = [
        schema,
        "",
        metric_block or "(no semantic metrics)",
        "",
        rel_block or "(no relationships)",
        "",
        f"Question: {question}",
    ]
    user_prompt = "\n".join(user_parts)

    if llm_client is None:
        try:
            from app.core.llm_client import get_llm_client
            llm_client = get_llm_client()
        except Exception:
            try:
                _llm = _load_mod("_llm_client", _CORE_DIR / "llm_client.py")
                llm_client = _llm.get_llm_client()
            except Exception as e:
                meta["warnings"].append(f"no llm: {e}")
                return f'SELECT * FROM "{table_name}" LIMIT 20', meta

    try:
        raw = llm_client.complete(SYSTEM_PROMPT, user_prompt)
        meta["provider"] = getattr(llm_client, "provider", None)
        meta["model"] = getattr(llm_client, "model", None)
        sql = _extract_sql(raw if isinstance(raw, str) else str(raw))
        return sql, meta
    except Exception as e:
        meta["warnings"].append(str(e))
        return f'SELECT * FROM "{table_name}" LIMIT 20', meta
