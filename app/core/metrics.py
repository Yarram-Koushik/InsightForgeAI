"""InsightForgeAI – Metric definitions & resolution (Phase 2.7)"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import re

@dataclass
class MetricDef:
    key: str
    label: str
    description: str
    kind: str
    preferred_columns: List[str] = field(default_factory=list)
    sql_template: str = ""

METRIC_CATALOG: List[MetricDef] = [
    MetricDef("revenue_sum", "Total revenue", "Sum of amount/revenue/sales", "sum", ["revenue", "amount", "sales", "total", "price", "value"], "SUM({value})"),
    MetricDef("order_count", "Order count", "Count of orders/transactions", "count", ["order", "orders", "transaction", "txn"], "COUNT(*)"),
    MetricDef("unique_customers", "Unique customers", "Distinct customers/users", "count_distinct", ["customer", "user", "client", "buyer", "student", "name"], "COUNT(DISTINCT {entity})"),
    MetricDef("avg_value", "Average value", "Simple average of a measure", "avg", ["rating", "score", "amount", "price", "duration"], "AVG({value})"),
    MetricDef("aov", "Average order value", "SUM(amount)/COUNT(DISTINCT order)", "ratio", ["amount", "revenue", "order"], "SUM({value}) / NULLIF(COUNT(DISTINCT {entity}), 0)"),
]

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def match_columns(columns: List[str], hints: List[str]) -> List[str]:
    out = []
    for c in columns or []:
        nc = _norm(c)
        for h in hints:
            if h in nc:
                out.append(c)
                break
    return out

def resolve_metrics_for_question(question: str, columns: List[str]) -> List[Dict[str, Any]]:
    q = _norm(question)
    cols = list(columns or [])
    suggestions = []
    wants_unique = any(w in q for w in ["unique", "distinct", "how many different", "number of students", "number of customers"])
    wants_avg = any(w in q for w in ["average", "avg", "mean"])
    wants_sum = any(w in q for w in ["total", "sum", "revenue", "sales"])
    wants_count = any(w in q for w in ["how many", "count", "number of"])
    wants_aov = "average order" in q or "aov" in q
    value_cols = match_columns(cols, ["revenue", "amount", "sales", "total", "price", "value", "rating", "score"])
    entity_cols = match_columns(cols, ["customer", "user", "client", "order", "student", "name", "id"])
    people = match_columns(cols, ["student", "customer", "name", "user"])
    if wants_aov and value_cols and entity_cols:
        suggestions.append({"key": "aov", "label": "Average order value", "sql_hint": f'SUM("{value_cols[0]}") / NULLIF(COUNT(DISTINCT "{entity_cols[0]}"), 0)', "reason": "AOV is usually SUM/COUNT DISTINCT, not AVG."})
    if wants_unique and (people or entity_cols):
        ent = people[0] if people else entity_cols[0]
        suggestions.append({"key": "unique_entities", "label": f"Unique {ent}", "sql_hint": f'COUNT(DISTINCT "{ent}")', "reason": "Prefer COUNT(DISTINCT) for unique counts."})
    if wants_avg and value_cols:
        suggestions.append({"key": "avg_value", "label": f"Average {value_cols[0]}", "sql_hint": f'AVG("{value_cols[0]}")', "reason": "Simple average of a numeric measure."})
    if wants_sum and value_cols:
        suggestions.append({"key": "sum_value", "label": f"Total {value_cols[0]}", "sql_hint": f'SUM("{value_cols[0]}")', "reason": "Total is usually SUM of a measure."})
    if wants_count and not wants_unique:
        suggestions.append({"key": "row_count", "label": "Row count", "sql_hint": "COUNT(*)", "reason": "Generic count of records."})
    return suggestions

def metric_prompt_block(question: str, columns: List[str]) -> str:
    tips = resolve_metrics_for_question(question, columns)
    if not tips:
        return "Metric rules:\n- unique → COUNT(DISTINCT col)\n- total revenue → SUM(col)\n- AOV → SUM/COUNT DISTINCT\n- Do not SUM id columns\n"
    lines = ["Metric suggestions for this question:"]
    for t in tips[:4]:
        lines.append(f"- {t['label']}: {t['sql_hint']}  ({t['reason']})")
    lines.append("Never use an ID column as a summed measure unless explicitly asked.")
    return "\n".join(lines)
