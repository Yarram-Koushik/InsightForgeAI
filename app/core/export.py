"""
InsightForgeAI – Evidence packs & export helpers (Phase 2.6)

Provides:
  - build_evidence_pack(agent_result, table_name, ...) -> dict
  - evidence_to_markdown / evidence_to_json
  - dataframe_to_csv_bytes
  - chart_to_html_bytes / chart_to_png_bytes (PNG needs kaleido)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Dict
from datetime import datetime, timezone
import json
import io

import pandas as pd


MAX_EXPORT_ROWS = 50_000


@dataclass
class ExportPayload:
    filename: str
    mime: str
    data: bytes
    note: Optional[str] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_evidence_pack(
    *,
    question: str,
    table_name: str,
    agent_result: Any,
    source_filename: Optional[str] = None,
) -> Dict[str, Any]:
    """Serializable evidence pack for auditability / compliance."""
    result_df = getattr(agent_result, "result_df", None)
    row_count = int(len(result_df)) if result_df is not None else 0
    col_count = int(len(result_df.columns)) if result_df is not None else 0

    pack = {
        "product": "InsightForgeAI",
        "version": "0.2.6",
        "generated_at_utc": _utc_now(),
        "question": question,
        "dataset": {
            "table_name": table_name,
            "source_filename": source_filename,
        },
        "route": {
            "intent": getattr(agent_result, "intent", None),
            "intent_reason": getattr(agent_result, "intent_reason", None),
            "success": bool(getattr(agent_result, "success", False)),
        },
        "sql": getattr(agent_result, "sql", None),
        "insight": getattr(agent_result, "insight", None),
        "message": getattr(agent_result, "message", None),
        "pipeline_steps": list(getattr(agent_result, "steps", []) or []),
        "warnings": list(getattr(agent_result, "warnings", []) or []),
        "error": getattr(agent_result, "error", None),
        "model": {
            "provider": getattr(agent_result, "provider", None),
            "model": getattr(agent_result, "model", None),
        },
        "chart": {
            "type": getattr(agent_result, "chart_type", None),
            "reason": getattr(agent_result, "chart_reason", None),
        },
        "forecast": {
            "method": getattr(agent_result, "forecast_method", None),
            "horizon": getattr(agent_result, "forecast_horizon", None),
            "trend_summary": getattr(agent_result, "trend_summary", None),
            "anomaly_count": len(getattr(agent_result, "anomalies", []) or []),
        },
        "result_shape": {
            "rows": row_count,
            "columns": col_count,
        },
    }
    return pack


def evidence_to_json(pack: Dict[str, Any]) -> bytes:
    return json.dumps(pack, indent=2, default=str).encode("utf-8")


def evidence_to_markdown(pack: Dict[str, Any]) -> bytes:
    lines = [
        f"# InsightForgeAI Evidence Pack",
        f"",
        f"**Generated (UTC):** {pack.get('generated_at_utc')}",
        f"**Question:** {pack.get('question')}",
        f"**Dataset:** `{pack.get('dataset', {}).get('table_name')}`",
        f"**Source file:** {pack.get('dataset', {}).get('source_filename') or '—'}",
        f"",
        f"## Route",
        f"- Intent: **{pack.get('route', {}).get('intent')}**",
        f"- Reason: {pack.get('route', {}).get('intent_reason') or '—'}",
        f"- Success: {pack.get('route', {}).get('success')}",
        f"",
        f"## SQL (evidence)",
        f"```sql",
        f"{pack.get('sql') or '-- none --'}",
        f"```",
        f"",
        f"## Insight",
        f"{pack.get('insight') or pack.get('message') or '—'}",
        f"",
        f"## Pipeline steps",
        f"`{' → '.join(pack.get('pipeline_steps') or [])}`",
        f"",
        f"## Model",
        f"- Provider: {pack.get('model', {}).get('provider') or '—'}",
        f"- Model: {pack.get('model', {}).get('model') or '—'}",
        f"",
        f"## Result shape",
        f"- Rows: {pack.get('result_shape', {}).get('rows')}",
        f"- Columns: {pack.get('result_shape', {}).get('columns')}",
    ]
    warns = pack.get("warnings") or []
    if warns:
        lines += ["", "## Warnings"] + [f"- {w}" for w in warns]
    if pack.get("error"):
        lines += ["", f"**Error:** {pack.get('error')}"]
    return "\n".join(lines).encode("utf-8")


def dataframe_to_csv_bytes(df: Optional[pd.DataFrame], max_rows: int = MAX_EXPORT_ROWS) -> ExportPayload:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return ExportPayload(filename="empty.csv", mime="text/csv", data=b"", note="No rows to export.")
    note = None
    out = df
    if len(df) > max_rows:
        out = df.head(max_rows)
        note = f"Truncated to {max_rows:,} rows (original {len(df):,})."
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    return ExportPayload(filename="insightforge_result.csv", mime="text/csv", data=buf.getvalue().encode("utf-8"), note=note)


def chart_to_html_bytes(fig: Any, title: str = "chart") -> Optional[ExportPayload]:
    if fig is None:
        return None
    try:
        html = fig.to_html(include_plotlyjs="cdn", full_html=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:40]
        return ExportPayload(filename=f"insightforge_{safe or 'chart'}.html", mime="text/html", data=html.encode("utf-8"))
    except Exception as e:
        return ExportPayload(filename="chart_error.txt", mime="text/plain", data=f"Chart HTML export failed: {e}".encode("utf-8"), note=str(e))


def chart_to_png_bytes(fig: Any, title: str = "chart") -> Optional[ExportPayload]:
    if fig is None:
        return None
    try:
        png = fig.to_image(format="png", scale=2)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:40]
        return ExportPayload(filename=f"insightforge_{safe or 'chart'}.png", mime="image/png", data=png)
    except Exception:
        return None


def safe_filename_part(text: str, max_len: int = 32) -> str:
    raw = (text or "export").strip().lower().replace(" ", "_")
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw)
    return (cleaned[:max_len] or "export")
