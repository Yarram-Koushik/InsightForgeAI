"""
InsightForgeAI – Natural Language → SQL Engine (Industry-grade)

Responsibilities:
- Build rich but compact schema context from Phase-1 semantic detection + DuckDB
- Generate safe, DuckDB-compatible SELECT SQL
- Self-correct on execution errors (max 2 retries)
- Always return transparent results (SQL + explanation + data or error)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field

import pandas as pd

# ------------------------------------------------------------------
# Robust imports – works both as package and when loaded via importlib
# ------------------------------------------------------------------
_CORE_DIR = Path(__file__).resolve().parent
if str(_CORE_DIR.parent.parent) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR.parent.parent))

# Always load llm_client via importlib and register it in sys.modules.
# This is required so that @dataclass works correctly under dynamic loading.
import importlib.util
_llm_path = _CORE_DIR / "llm_client.py"
_llm_spec = importlib.util.spec_from_file_location("llm_client", _llm_path)
llm_mod = importlib.util.module_from_spec(_llm_spec)
sys.modules["llm_client"] = llm_mod          # CRITICAL: must register before exec_module
_llm_spec.loader.exec_module(llm_mod)
get_llm_client = llm_mod.get_llm_client
LLMResponse = llm_mod.LLMResponse


@dataclass
class NL2SQLResult:
    success: bool
    question: str
    generated_sql: Optional[str] = None
    final_sql: Optional[str] = None
    explanation: Optional[str] = None
    result_df: Optional[pd.DataFrame] = None
    error: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    attempts: int = 0
    warnings: List[str] = field(default_factory=list)


def _quote_identifier(name: str) -> str:
    """DuckDB-safe identifier quoting."""
    return '"' + name + '"'


def build_schema_context(
    workspace,
    table_name: str,
    max_sample_rows: int = 5,
) -> str:
    """
    Build a compact, high-signal schema description for the LLM.
    Uses both DuckDB physical types and Phase-1 semantic types when available.
    """
    record = workspace.get(table_name)
    if record is None:
        return f"Table `{table_name}` not found in workspace."

    schema_df = workspace.get_table_schema(table_name)
    if "error" in schema_df.columns:
        return f"Could not retrieve schema for `{table_name}`."

    semantic_map = {}
    try:
        import importlib.util
        schema_path = _CORE_DIR / "schema.py"
        spec = importlib.util.spec_from_file_location("schema_mod", schema_path)
        schema_mod = importlib.util.module_from_spec(spec)
        sys.modules["schema_mod"] = schema_mod   # register before exec
        spec.loader.exec_module(schema_mod)
        semantic_df = schema_mod.detect_schema_semantic(record.cleaned_df)
        semantic_map = {row["column"]: row for _, row in semantic_df.iterrows()}
    except Exception:
        pass

    lines = []
    lines.append(f"TABLE: {_quote_identifier(table_name)}")
    lines.append(f"ROW COUNT: {len(record.cleaned_df):,}")
    lines.append("")
    lines.append("COLUMNS:")

    for _, row in schema_df.iterrows():
        col = row["column_name"]
        dtype = row["column_type"]
        sem = semantic_map.get(col, {})
        semantic_type = sem.get("semantic_type", "Unknown")
        conf = sem.get("confidence", 0)
        missing = sem.get("missing_pct", 0)

        extra = f"  | semantic={semantic_type} (conf={conf})"
        if missing and float(missing) > 0:
            extra += f"  | missing={missing}%"
        lines.append(f"  - {_quote_identifier(col)}  ({dtype}){extra}")

    sample = record.cleaned_df.head(max_sample_rows)
    if not sample.empty:
        lines.append("")
        lines.append(f"SAMPLE ROWS (first {len(sample)}):")
        lines.append(sample.to_string(index=False, max_cols=12))

    # ---- Phase 3.1 Semantic Metric Layer ----
    try:
        import importlib.util as _ilu
        _sl_path = _CORE_DIR / "semantic_layer.py"
        if _sl_path.exists():
            _sl_spec = _ilu.spec_from_file_location("_semantic_layer_nl", _sl_path)
            _sl = _ilu.module_from_spec(_sl_spec)
            import sys as _sys
            _sys.modules["_semantic_layer_nl"] = _sl
            _sl_spec.loader.exec_module(_sl)
            _model = _sl.build_semantic_model(workspace, table_name)
            lines.append("")
            lines.append(_sl.model_prompt_summary(_model, max_metrics=10))
    except Exception as _e:
        lines.append("")
        lines.append(f"SEMANTIC METRICS: (unavailable: {_e})")

    # ---- Phase 3.3 Multi-table Relationships ----
    try:
        import importlib.util as _ilu
        _rel_path = _CORE_DIR / "relationships.py"
        if _rel_path.exists() and len(workspace.list_datasets()) >= 2:
            _rel_spec = _ilu.spec_from_file_location("_rel_nl", _rel_path)
            _rel = _ilu.module_from_spec(_rel_spec)
            import sys as _sys
            _sys.modules["_rel_nl"] = _rel
            _rel_spec.loader.exec_module(_rel)
            _graph = _rel.build_workspace_relationship_graph(workspace)
            lines.append("")
            lines.append(_rel.relationships_prompt_block(_graph, primary_table=table_name))
    except Exception as _e:
        lines.append("")
        lines.append(f"RELATIONSHIPS: (unavailable: {_e})")

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

You will receive:
- The table schema (physical + semantic types)
- SEMANTIC METRICS (governed definitions) when available
- RELATIONSHIPS between loaded tables when available
- A natural language question

Respond with pure SQL only.
"""


def _extract_sql(text: str) -> str:
    """Pull the first SQL statement out of LLM output (handles accidental markdown)."""
    text = text.strip()
    text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    if ";" in text:
        text = text.split(";")[0] + ";"
    return text.strip()


def generate_sql(
    workspace,
    table_name: str,
    question: str,
    previous_error: Optional[str] = None,
    previous_sql: Optional[str] = None,
) -> Tuple[Optional[str], LLMResponse]:
    """
    Ask the LLM to produce SQL. Optionally provide previous error for self-correction.
    """
    client = get_llm_client()
    schema_ctx = build_schema_context(workspace, table_name)

    user_parts = [
        "SCHEMA:",
        schema_ctx,
        "",
        f"QUESTION: {question.strip()}",
    ]

    # Phase 3.1 – question-specific metric resolution
    try:
        import importlib.util as _ilu
        _sl_path = _CORE_DIR / "semantic_layer.py"
        if _sl_path.exists():
            _sl_spec = _ilu.spec_from_file_location("_semantic_layer_gen", _sl_path)
            _sl = _ilu.module_from_spec(_sl_spec)
            import sys as _sys
            _sys.modules["_semantic_layer_gen"] = _sl
            _sl_spec.loader.exec_module(_sl)
            _model = _sl.build_semantic_model(workspace, table_name)
            _block = _sl.metric_prompt_block(question, _model)
            if _block:
                user_parts.extend(["", _block])
    except Exception:
        pass

    if previous_error and previous_sql:
        user_parts.extend([
            "",
            "PREVIOUS SQL (failed):",
            previous_sql,
            "",
            f"ERROR FROM DUCKDB: {previous_error}",
            "",
            "Please fix the SQL so it runs correctly against the schema above. Return only the corrected SQL.",
        ])

    user_prompt = "\n".join(user_parts)

    response = client.chat(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.05,
        max_tokens=800,
    )

    if not response.success:
        return None, response

    sql = _extract_sql(response.content)
    return sql, response


def ask(
    workspace,
    table_name: str,
    question: str,
    max_retries: int = 2,
) -> NL2SQLResult:
    """
    Full NL → SQL → Execute pipeline with self-correction.

    Industry behaviours:
    - Never execute unsafe SQL (Workspace.execute_sql already guards)
    - Max 2 correction attempts on error
    - Always return transparent intermediate SQL
    - Surface clear errors when LLM is not configured
    """
    result = NL2SQLResult(success=False, question=question)

    if not question or not question.strip():
        result.error = "Please enter a question."
        return result

    if table_name not in workspace.list_datasets():
        result.error = f"Dataset `{table_name}` is not loaded in the workspace."
        return result

    record = workspace.get(table_name)
    if record and not record.metadata.get("duckdb_registered"):
        workspace.register_in_duckdb(table_name)

    client = get_llm_client()
    if not client.is_configured():
        result.error = (
            "No LLM API key found. Add GROQ_API_KEY (recommended) or GOOGLE_API_KEY "
            "to your .env file. Free keys available at console.groq.com or aistudio.google.com"
        )
        return result

    # ------------------------------------------------------------------
    # Phase 3.2 – Deterministic metric compiler (preferred when clear)
    # ------------------------------------------------------------------
    try:
        import importlib.util as _ilu
        _sl_path = _CORE_DIR / "semantic_layer.py"
        _mc_path = _CORE_DIR / "metric_compiler.py"
        if _sl_path.exists() and _mc_path.exists():
            _sl_spec = _ilu.spec_from_file_location("_sl_ask", _sl_path)
            _sl = _ilu.module_from_spec(_sl_spec)
            import sys as _sys
            _sys.modules["_sl_ask"] = _sl
            _sl_spec.loader.exec_module(_sl)

            _mc_spec = _ilu.spec_from_file_location("_mc_ask", _mc_path)
            _mc = _ilu.module_from_spec(_mc_spec)
            _sys.modules["_mc_ask"] = _mc
            _mc_spec.loader.exec_module(_mc)

            _model = _sl.build_semantic_model(workspace, table_name)
            _compiled = _mc.try_compile_from_question(question, _model)
            if _compiled.success and _compiled.sql:
                df, exec_error = workspace.execute_sql(_compiled.sql)
                if exec_error is None:
                    result.success = True
                    result.generated_sql = _compiled.sql
                    result.final_sql = _compiled.sql
                    result.result_df = df
                    result.attempts = 0
                    result.explanation = (
                        f"Deterministic metric compile (Phase 3.2). {_compiled.explanation}"
                    )
                    if _compiled.warnings:
                        result.warnings.extend(_compiled.warnings)
                    if df is not None and len(df) == 0:
                        result.warnings.append("Query executed successfully but returned 0 rows.")
                    return result
                else:
                    result.warnings.append(
                        f"Metric compiler SQL failed validation/execution ({exec_error}); "
                        f"falling back to NL→SQL."
                    )
    except Exception as _e:
        result.warnings.append(f"Metric compiler skipped: {_e}")

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

        result.provider = llm_resp.provider
        result.model = llm_resp.model

        if not llm_resp.success:
            result.error = llm_resp.error
            return result

        if not sql:
            result.error = "LLM returned empty SQL."
            return result

        if attempt == 1:
            result.generated_sql = sql
        current_sql = sql
        result.final_sql = sql

        df, exec_error = workspace.execute_sql(sql)

        if exec_error is None:
            result.success = True
            result.result_df = df
            if df is not None and len(df) == 0:
                result.warnings.append("Query executed successfully but returned 0 rows.")
            result.explanation = (
                f"Executed successfully via {llm_resp.provider} ({llm_resp.model}). "
                f"{len(df):,} row(s) returned."
            )
            return result

        last_error = exec_error
        if attempt > max_retries:
            result.error = (
                f"SQL still failed after {max_retries} correction attempt(s).\n"
                f"Last error: {exec_error}"
            )
            result.warnings.append("Self-correction exhausted.")
            return result

    result.error = "Unexpected end of retry loop."
    return result
