"""
Phase 3.6 – Evaluation harness unit tests.
Run: pytest tests/test_eval_harness.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.eval_harness import (
    EvalCase,
    get_cases,
    heuristic_intent,
    heuristic_sql_hint,
    run_suite,
    score_offline,
    score_sql_case,
    GOLDEN_CASES,
)


def test_golden_bank_not_empty():
    assert len(GOLDEN_CASES) >= 10


def test_get_cases_filter_domain():
    rev = get_cases(domain="revenue")
    assert rev
    assert all(c.domain == "revenue" for c in rev)


def test_heuristic_intent_meta():
    assert heuristic_intent("What can you do?") == "meta"
    assert heuristic_intent("Who are you?") == "meta"


def test_heuristic_intent_forecast():
    assert heuristic_intent("Forecast next 30 days") == "forecast"


def test_heuristic_intent_clarify():
    assert heuristic_intent("performance") == "clarify"


def test_heuristic_intent_data_query():
    assert heuristic_intent("How many unique students are there?") == "data_query"
    assert heuristic_intent("What is the total amount?") == "data_query"


def test_score_offline_pass():
    case = EvalCase(
        id="t1",
        question="total amount",
        expected_intent="data_query",
        sql_must_include=["SUM"],
    )
    r = score_offline(case, intent="data_query", sql="SELECT SUM(amount) FROM t")
    assert r.passed is True


def test_score_offline_fail_intent():
    case = EvalCase(id="t2", question="x", expected_intent="meta")
    r = score_offline(case, intent="data_query", sql=None)
    assert r.passed is False
    assert r.details["intent_got"] == "data_query"


def test_score_offline_fail_missing_sql():
    case = EvalCase(id="t3", question="x", sql_must_include=["COUNT", "DISTINCT"])
    r = score_offline(case, intent="data_query", sql="SELECT COUNT(*) FROM t")
    assert r.passed is False
    assert "DISTINCT" in r.details.get("missing", [])


def test_score_sql_case_compat():
    case = EvalCase(id="t4", question="x", sql_must_include=["SUM"])
    r = score_sql_case(case, "SELECT SUM(a) FROM t", intent="data_query")
    assert r.passed is True


def test_run_suite_offline_high_pass():
    card = run_suite(mode="offline")
    assert card.total == len(GOLDEN_CASES)
    assert card.pass_rate >= 80.0
    assert card.passed + card.failed == card.total


def test_heuristic_sql_aov_uses_sum():
    sql = heuristic_sql_hint("What is the average order value?")
    assert "SUM" in sql.upper()
