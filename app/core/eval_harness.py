"""Evaluation harness skeleton (Phase 2.7)"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class EvalCase:
    id: str
    question: str
    expected_intent: Optional[str] = None
    sql_must_include: Optional[List[str]] = None
    sql_must_not_include: Optional[List[str]] = None
    notes: str = ""

@dataclass
class EvalResult:
    case_id: str
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)

DEFAULT_CASES: List[EvalCase] = [
    EvalCase("unique_count", "How many unique students are there?", expected_intent="data_query", sql_must_include=["COUNT", "DISTINCT"]),
    EvalCase("total_sum", "What is the total amount?", expected_intent="data_query", sql_must_include=["SUM"], sql_must_not_include=["AVG("]),
    EvalCase("meta", "What can you do?", expected_intent="meta"),
    EvalCase("forecast", "Forecast next 30 days", expected_intent="forecast"),
    EvalCase("clarify", "performance", expected_intent="clarify"),
]

def score_sql_case(case: EvalCase, sql: Optional[str], intent: Optional[str] = None) -> EvalResult:
    details: Dict[str, Any] = {}
    passed = True
    if case.expected_intent and intent and case.expected_intent != intent:
        passed = False
        details["intent_expected"] = case.expected_intent
        details["intent_got"] = intent
    sql_u = (sql or "").upper()
    for frag in case.sql_must_include or []:
        if frag.upper() not in sql_u:
            passed = False
            details.setdefault("missing", []).append(frag)
    for frag in case.sql_must_not_include or []:
        if frag.upper() in sql_u:
            passed = False
            details.setdefault("forbidden_present", []).append(frag)
    return EvalResult(case_id=case.id, passed=passed, details=details)

def run_cases(cases: Optional[List[EvalCase]] = None) -> Dict[str, Any]:
    cases = cases or DEFAULT_CASES
    return {"n_cases": len(cases), "cases": [c.id for c in cases], "note": "Wire to live run_agent for full scores."}
