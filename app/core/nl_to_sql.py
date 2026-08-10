"""
InsightForgeAI – Natural Language → SQL Engine (Industry-grade)

Responsibilities:
- Build rich but compact schema context from Phase-1 semantic detection + DuckDB
- Generate safe, DuckDB-compatible SELECT SQL
- Validate + auto-repair common LLM mistakes
- Execute against Workspace DuckDB connection
- Return structured result with evidence (SQL, attempts, warnings)

Phase 3: metric compiler preferred path + time intelligence + JOINs across tables.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

_CORE_DIR = Path(__file__).resolve().parent
_ROOT = _CORE_DIR.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import importlib.util


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_llm = _load("llm_client", _CORE_DIR / "llm_client.py")
get_llm_client = _llm.get_llm_client

try:
    _schema = _load("schema", _CORE_DIR / "schema.py")
    detect_schema_semantic = _schema.detect_schema_semantic
except Exception:
    detect_schema_semantic = None


@dataclass
class NL2SQLResult:
    success: bool = False
    generated_sql: Optional[str] = None
    final_sql: Optional[str] = None
    result_df: Optional[pd.DataFrame] = None
    error: Optional[str] = None
    attempts: int = 0
    warnings: List[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    explanation: Optional[str] = None


def _schema_context(workspace, table_name: str) -> str:
    record = workspace.get(table_name)
    if record is None:
        return f"Table {table_name} not found."
    df = record.cleaned_df if record.cleaned_df is not None else record.raw_df
    lines = [f'Table: "{table_name}"', f"Rows (approx): {len(df)}", "Columns:"]
    try:
        if detect_schema_semantic is not None:
            schema_df = detect_schema_semantic(df)
            for _, row in schema_df.iterrows():
                lines.append(
                    f'  - "{row["column"]}" | semantic={row.get("semantic_type")} | '
                    f'physical={row.get("physical_type")} | unique={row.get("unique_count")} | '
                    f'missing%={row.get("missing_pct")}'
                )
        else:
            for c in df.columns:
                lines.append(f'  - "{c}" | dtype={df[c].dtype}')
    except Exception as e:
        lines.append(f"(schema detect failed: {e})")
        for c in df.columns:
            lines.append(f'  - "{c}"')
    try:
        _rel_path = _CORE_DIR / "relationships.py"
        if _rel_path.exists() and len(workspace.list_datasets()) >= 2:
            _rel = _load("_rel_ctx", _rel_path)
            _graph = _rel.build_workspace_relationship_graph(workspace)
            lines.append(_rel.relationships_prompt_block(_graph, primary_table=table_name))
    except Exception:
        pass
    try:
        _sl_path = _CORE_DIR / "semantic_layer.py"
        if _sl_path.exists():
            _sl = _load("_sl_ctx", _sl_path)
            model = _sl.build_semantic_model(workspace, table_name)
            if model and model.metrics:
                lines.append("SEMANTIC METRICS (prefer these expressions when the question matches):")
                for m in model.metrics[:20]:
                    lines.append(f'  - {m.name} ({m.label}): {m.sql_expression()}')
    except Exception:
        pass
    return "\n".join(lines)


SYSTEM_PROMPT = """You are an expert DuckDB SQL generator for a business intelligence product called InsightForgeAI.

RULES (strict):
1. Output ONLY a single valid DuckDB SELECT (or WITH) statement. No markdown, no explanation, no comments outside the SQL.
2. Always double-quote identifiers that contain spaces, special characters, or mixed case: "Column Name"
3. Use only columns that exist in the provided schema. Never invent columns.
4. Prefer clear, readable SQL. Use meaningful aliases.
5. For aggregations always use explicit GROUP BY when required.
6. Never use DELETE, UPDATE, INSERT, DROP, CREATE, ALTER, or any DDL/DML.
7. If the question cannot be answered with the given table, return exactly:
   SELECT 'UNSUPPORTED: <short reason>' AS message;
8. Keep result sets reasonable — add LIMIT 100 unless the user explicitly asks for all rows or an aggregate that returns few rows.
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
13. LIST / SHOW SPECIFIC COLUMNS (do NOT aggregate):
    - If the user says "list", "show only", "just", "columns X and Y", or asks for specific fields
      without words like total/count/sum/average/unique → return a ROW-level SELECT of those columns.
    - Example: "list order_id and customer_name" →
        SELECT o."order_id", c."customer_name"
        FROM "orders" o JOIN "customers" c ON o."customer_id" = c."customer_id"
        LIMIT 100
      NEVER answer that with COUNT(DISTINCT customer_id).
    - Only use COUNT/SUM/AVG when the user clearly asks for a total, count, average, or unique metric.

You will receive:
- The table schema (physical + semantic types)
- SEMANTIC METRICS (governed definitions) when available
- RELATIONSHIPS between loaded tables when available
- A natural language question

Respond with pure SQL only.
"""


def _clean_sql(text: str) -> str:
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:sql)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    s = s.strip().rstrip(";") + ";"
    return s


def generate_sql(workspace, table_name: str, question: str, previous_error: str = None, previous_sql: str = None):
    client = get_llm_client()
    schema = _schema_context(workspace, table_name)
    user_parts = [schema, "", f"Question: {question}"]
    if previous_error:
        user_parts.append(f"\nPrevious SQL failed with error:\n{previous_error}")
        if previous_sql:
            user_parts.append(f"Previous SQL:\n{previous_sql}")
        user_parts.append("Fix the SQL. Output only the corrected SELECT.")
    user_prompt = "\n".join(user_parts)
    resp = client.chat(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)
    if not getattr(resp, "success", True):
        return "", resp
    sql = _clean_sql(resp.content or "")
    return sql, resp


def _get_semantic_model(workspace, table_name: str):
    try:
        _sl = _load("_sl_ask", _CORE_DIR / "semantic_layer.py")
        model = _sl.build_semantic_model(workspace, table_name)
        return model, _sl
    except Exception:
        return None, None


def ask(workspace, table_name: str, question: str, max_retries: int = 2) -> NL2SQLResult:
    result = NL2SQLResult()
    if not question or not question.strip():
        result.error = "Empty question"
        return result
    if table_name not in workspace.list_datasets():
        result.error = f"Table '{table_name}' not in workspace"
        return result

    # Phase 3.2 – metric compiler preferred path
    try:
        _mc_path = _CORE_DIR / "metric_compiler.py"
        if _mc_path.exists():
            _mc = _load("_mc_ask", _mc_path)
            model, _ = _get_semantic_model(workspace, table_name)
            if model is not None:
                _compiled = _mc.try_compile_from_question(question, model)
                if _compiled.success and _compiled.sql:
                    df, exec_error = workspace.execute_sql(_compiled.sql)
                    if exec_error is None:
                        result.success = True
                        result.generated_sql = _compiled.sql
                        result.final_sql = _compiled.sql
                        result.result_df = df
                        result.attempts = 0
                        result.explanation = f"Metric compiler (Phase 3.2). {_compiled.explanation}"
                        if _compiled.warnings:
                            result.warnings.extend(_compiled.warnings)
                        if df is not None and len(df) == 0:
                            result.warnings.append("Query executed successfully but returned 0 rows.")
                        return result
                    else:
                        result.warnings.append(f"Metric compiler SQL failed ({exec_error}); falling back to NL→SQL.")
    except Exception as _e:
        result.warnings.append(f"Metric compiler skipped: {_e}")

    # Phase 3.4 – time intelligence preferred path
    try:
        _ti_path = _CORE_DIR / "time_intelligence.py"
        if _ti_path.exists():
            _model, _sl = _get_semantic_model(workspace, table_name)
            _ti = _load("_ti_ask", _ti_path)
            _ti_res = _ti.try_compile_time_intel_from_question(question, _model, table_name)
            if _ti_res.success and _ti_res.sql:
                df, exec_error = workspace.execute_sql(_ti_res.sql)
                if exec_error is None:
                    result.success = True
                    result.generated_sql = _ti_res.sql
                    result.final_sql = _ti_res.sql
                    result.result_df = df
                    result.attempts = 0
                    result.explanation = f"Time intelligence (Phase 3.4). {_ti_res.explanation}"
                    if _ti_res.warnings:
                        result.warnings.extend(_ti_res.warnings)
                    if df is not None and len(df) == 0:
                        result.warnings.append("Query executed successfully but returned 0 rows.")
                    return result
                else:
                    result.warnings.append(f"Time-intel SQL failed ({exec_error}); falling back to NL→SQL.")
    except Exception as _e:
        result.warnings.append(f"Time intelligence skipped: {_e}")

    current_sql = None
    last_error = None
    for attempt in range(1, max_retries + 2):
        result.attempts = attempt
        sql, llm_resp = generate_sql(
            workspace=workspace,
            table_name=table_name,
            question=question,
            previous_error=last_error,
            previous_sql=current_sql,
        )
        result.provider = getattr(llm_resp, "provider", None)
        result.model = getattr(llm_resp, "model", None)
        if not getattr(llm_resp, "success", True) and getattr(llm_resp, "error", None):
            result.error = llm_resp.error
            # Don't retry forever on missing API key
            if "API key" in str(llm_resp.error) or "No LLM" in str(llm_resp.error):
                return result
        current_sql = sql
        result.generated_sql = sql
        if not sql or not sql.lower().lstrip().startswith(("select", "with")):
            last_error = getattr(llm_resp, "error", None) or "Model did not return a SELECT/WITH statement"
            result.error = last_error
            continue
        df, exec_error = workspace.execute_sql(sql)
        if exec_error is None:
            result.success = True
            result.final_sql = sql
            result.result_df = df
            result.error = None
            if df is not None and len(df) == 0:
                result.warnings.append("Query executed successfully but returned 0 rows.")
            return result
        last_error = exec_error
        result.error = exec_error
    return result
