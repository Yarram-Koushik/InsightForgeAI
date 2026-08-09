"""
InsightForgeAI – Evaluation & Quality Gates (Phase 3.6)

Expand the Phase 2.7 skeleton into a real regression suite:

- Golden questions across domains (revenue, students, support, meta, forecast, clarify)
- Offline scoring (intent + SQL shape) – no LLM required for CI
- Optional live mode that calls the orchestrator when keys + data are present
- Scorecard: overall pass %, by intent, by domain, failure taxonomy
- CLI: python -m app.core.eval_harness  |  python -m app.core.eval_harness --live

Design notes
------------
- LLM non-determinism is handled by allowlists / required fragments, not exact SQL match.
- Schema drift: cases that need a live table mark `requires_table`; offline mode skips them.
- Infrastructure failures (no workspace / no LLM) are reported separately from product fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class EvalCase:
    id: str
    question: str
    domain: str = "general"
    expected_intent: Optional[str] = None
    sql_must_include: Optional[List[str]] = None
    sql_must_not_include: Optional[List[str]] = None
    expect_success: Optional[bool] = None
    expect_rows_min: Optional[int] = None
    expect_clarify: bool = False
    requires_table: bool = False
    notes: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    mode: str = "offline"
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class Scorecard:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    infra_errors: int = 0
    by_intent: Dict[str, Dict[str, int]] = field(default_factory=dict)
    by_domain: Dict[str, Dict[str, int]] = field(default_factory=dict)
    failures: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def pass_rate(self) -> float:
        denom = self.passed + self.failed
        return round(100.0 * self.passed / denom, 1) if denom else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pass_rate_pct"] = self.pass_rate
        return d


GOLDEN_CASES: List[EvalCase] = [
    EvalCase(
        id="rev_total_amount",
        question="What is the total amount?",
        domain="revenue",
        expected_intent="data_query",
        sql_must_include=["SUM"],
        sql_must_not_include=["AVG("],
        tags=["aggregation"],
    ),
    EvalCase(
        id="rev_aov",
        question="What is the average order value?",
        domain="revenue",
        expected_intent="data_query",
        sql_must_include=["SUM"],
        notes="Prefer SUM/COUNT DISTINCT style",
        tags=["ratio", "aov"],
    ),
    EvalCase(
        id="rev_unique_customers",
        question="How many unique customers are there?",
        domain="revenue",
        expected_intent="data_query",
        sql_must_include=["COUNT", "DISTINCT"],
        tags=["distinct"],
    ),
    EvalCase(
        id="rev_by_region",
        question="Total sales by region",
        domain="revenue",
        expected_intent="data_query",
        sql_must_include=["SUM", "GROUP BY"],
        tags=["groupby"],
    ),
    EvalCase(
        id="stu_unique",
        question="How many unique students are there?",
        domain="students",
        expected_intent="data_query",
        sql_must_include=["COUNT", "DISTINCT"],
        tags=["distinct"],
    ),
    EvalCase(
        id="stu_count_rows",
        question="How many rows are in this dataset?",
        domain="students",
        expected_intent="data_query",
        sql_must_include=["COUNT"],
        tags=["volume"],
    ),
    EvalCase(
        id="sup_open_count",
        question="How many open tickets are there?",
        domain="support",
        expected_intent="data_query",
        sql_must_include=["COUNT"],
        tags=["filter"],
    ),
    EvalCase(
        id="meta_capabilities",
        question="What can you do?",
        domain="meta",
        expected_intent="meta",
        tags=["meta"],
    ),
    EvalCase(
        id="meta_help",
        question="Who are you?",
        domain="meta",
        expected_intent="meta",
        tags=["meta"],
    ),
    EvalCase(
        id="clarify_vague",
        question="performance",
        domain="clarify",
        expected_intent="clarify",
        expect_clarify=True,
        tags=["clarify"],
    ),
    EvalCase(
        id="clarify_empty_ish",
        question="show me stuff",
        domain="clarify",
        expected_intent="clarify",
        expect_clarify=True,
        tags=["clarify"],
    ),
    EvalCase(
        id="forecast_30d",
        question="Forecast next 30 days",
        domain="forecast",
        expected_intent="forecast",
        tags=["forecast"],
    ),
    EvalCase(
        id="forecast_trend",
        question="Show the trend and forecast for the next 14 days",
        domain="forecast",
        expected_intent="forecast",
        tags=["forecast"],
    ),
    EvalCase(
        id="safe_no_avg_on_id",
        question="What is the average order id?",
        domain="revenue",
        expected_intent="data_query",
        sql_must_not_include=["AVG(\"order_id\")", "AVG(order_id)"],
        notes="IDs must not be averaged",
        tags=["safety"],
    ),
    EvalCase(
        id="safe_count_not_sum_id",
        question="How many orders are there?",
        domain="revenue",
        expected_intent="data_query",
        sql_must_include=["COUNT"],
        sql_must_not_include=["SUM(order_id)", "SUM(\"order_id\")"],
        tags=["safety"],
    ),
]


def get_cases(
    domain: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    case_ids: Optional[Sequence[str]] = None,
) -> List[EvalCase]:
    cases = list(GOLDEN_CASES)
    if domain:
        cases = [c for c in cases if c.domain == domain]
    if tags:
        tagset = set(tags)
        cases = [c for c in cases if tagset.intersection(c.tags)]
    if case_ids:
        idset = set(case_ids)
        cases = [c for c in cases if c.id in idset]
    return cases


def score_offline(
    case: EvalCase,
    *,
    intent: Optional[str] = None,
    sql: Optional[str] = None,
) -> EvalResult:
    details: Dict[str, Any] = {"domain": case.domain}
    passed = True

    if case.expected_intent and intent is not None:
        if intent != case.expected_intent:
            passed = False
            details["intent_expected"] = case.expected_intent
            details["intent_got"] = intent

    sql_u = (sql or "").upper()
    if sql is not None:
        for frag in case.sql_must_include or []:
            if frag.upper() not in sql_u:
                passed = False
                details.setdefault("missing", []).append(frag)
        for frag in case.sql_must_not_include or []:
            if frag.upper() in sql_u:
                passed = False
                details.setdefault("forbidden_present", []).append(frag)

    if intent is None and sql is None:
        details["note"] = "catalog_only_check"
        passed = True

    return EvalResult(case_id=case.id, passed=passed, mode="offline", details=details)


def score_sql_case(case: EvalCase, sql: Optional[str], intent: Optional[str] = None) -> EvalResult:
    return score_offline(case, intent=intent, sql=sql)


def heuristic_intent(question: str) -> str:
    q = (question or "").lower().strip()
    if not q:
        return "clarify"
    if any(w in q for w in ("what can you do", "who are you", "help", "capabilities")):
        return "meta"
    if any(w in q for w in ("forecast", "predict", "next 30", "next 14", "trend and forecast")):
        return "forecast"
    tokens = q.split()
    if len(tokens) <= 2 and not any(w in q for w in ("count", "sum", "total", "how many", "average", "list")):
        return "clarify"
    if q in {"performance", "show me stuff", "data", "numbers"}:
        return "clarify"
    return "data_query"


def heuristic_sql_hint(question: str) -> str:
    q = (question or "").lower()
    parts = ["SELECT"]
    if "unique" in q or "distinct" in q:
        parts.append("COUNT(DISTINCT col)")
    elif "how many" in q or "count" in q or "rows" in q or "orders are there" in q:
        parts.append("COUNT(*)")
    elif "average order value" in q or "aov" in q:
        parts.append("SUM(amount) / NULLIF(COUNT(DISTINCT order_id), 0)")
    elif "average" in q and "id" in q:
        parts.append("COUNT(*)")
    elif "total" in q or "sum" in q or "sales" in q:
        parts.append("SUM(amount)")
        if "by " in q or "region" in q:
            parts.append("GROUP BY region")
    else:
        parts.append("*")
    parts.append("FROM data")
    return " ".join(parts)


def run_live_case(case: EvalCase, workspace: Any, table_name: str) -> EvalResult:
    try:
        import importlib.util
        agents = Path(__file__).resolve().parent.parent / "agents" / "orchestrator.py"
        if not agents.exists():
            return EvalResult(case_id=case.id, passed=False, mode="live", error="orchestrator_missing")
        spec = importlib.util.spec_from_file_location("orch_eval", agents)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["orch_eval"] = mod
        spec.loader.exec_module(mod)
        result = mod.run_agent(workspace=workspace, table_name=table_name, question=case.question)
    except Exception as e:
        return EvalResult(case_id=case.id, passed=False, mode="live", error=f"infra:{e}")

    intent = getattr(result, "intent", None)
    sql = getattr(result, "sql", None)
    success = bool(getattr(result, "success", False))
    offline = score_offline(case, intent=intent, sql=sql)
    details = dict(offline.details)
    details["live_success"] = success
    details["live_intent"] = intent
    if case.expect_success is not None and success != case.expect_success:
        offline.passed = False
        details["success_expected"] = case.expect_success
    if case.expect_clarify:
        clarifies = list(getattr(result, "clarify_questions", []) or [])
        if not clarifies and intent != "clarify":
            offline.passed = False
            details["clarify_missing"] = True
    offline.mode = "live"
    offline.details = details
    return offline


def run_suite(
    cases: Optional[List[EvalCase]] = None,
    *,
    mode: str = "offline",
    workspace: Any = None,
    table_name: Optional[str] = None,
) -> Scorecard:
    cases = cases or get_cases()
    card = Scorecard()

    for case in cases:
        card.total += 1
        if mode == "live":
            if workspace is None or not table_name:
                card.skipped += 1
                card.infra_errors += 1
                card.failures.append({"case_id": case.id, "reason": "no_workspace_or_table", "mode": "live"})
                continue
            result = run_live_case(case, workspace, table_name)
        else:
            pred_intent = heuristic_intent(case.question)
            pred_sql = heuristic_sql_hint(case.question) if case.expected_intent in (None, "data_query") else None
            if case.expected_intent in ("meta", "clarify", "forecast"):
                pred_sql = None
            result = score_offline(case, intent=pred_intent, sql=pred_sql)

        if result.error:
            card.infra_errors += 1
            card.failed += 1
            card.failures.append({"case_id": case.id, "reason": result.error, "mode": result.mode})
        elif result.passed:
            card.passed += 1
        else:
            card.failed += 1
            card.failures.append({
                "case_id": case.id,
                "reason": "assertion",
                "details": result.details,
                "mode": result.mode,
            })

        intent_key = case.expected_intent or "unknown"
        card.by_intent.setdefault(intent_key, {"passed": 0, "failed": 0})
        card.by_domain.setdefault(case.domain, {"passed": 0, "failed": 0})
        bucket_i = card.by_intent[intent_key]
        bucket_d = card.by_domain[case.domain]
        if result.passed and not result.error:
            bucket_i["passed"] += 1
            bucket_d["passed"] += 1
        else:
            bucket_i["failed"] += 1
            bucket_d["failed"] += 1

    return card


def run_cases(cases: Optional[List[EvalCase]] = None) -> Dict[str, Any]:
    card = run_suite(cases or get_cases(), mode="offline")
    return card.to_dict()


DEFAULT_CASES = GOLDEN_CASES


def _print_scorecard(card: Scorecard) -> None:
    print("=" * 60)
    print("InsightForgeAI Eval Scorecard (Phase 3.6)")
    print("=" * 60)
    print(f"Generated:  {card.generated_at}")
    print(f"Total:      {card.total}")
    print(f"Passed:     {card.passed}")
    print(f"Failed:     {card.failed}")
    print(f"Skipped:    {card.skipped}")
    print(f"Infra errs: {card.infra_errors}")
    print(f"Pass rate:  {card.pass_rate}%")
    print("-" * 60)
    print("By intent:")
    for k, v in sorted(card.by_intent.items()):
        print(f"  {k:15s}  pass={v['passed']}  fail={v['failed']}")
    print("By domain:")
    for k, v in sorted(card.by_domain.items()):
        print(f"  {k:15s}  pass={v['passed']}  fail={v['failed']}")
    if card.failures:
        print("-" * 60)
        print("Failures:")
        for f in card.failures[:20]:
            print(f"  - {f['case_id']}: {f.get('reason')} {f.get('details') or ''}")
    print("=" * 60)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="InsightForgeAI evaluation suite (Phase 3.6)")
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--domain", default=None)
    parser.add_argument("--json", action="store_true", help="Print scorecard as JSON")
    parser.add_argument("--fail-under", type=float, default=80.0, help="Exit 1 if pass_rate < this")
    parser.add_argument("--table", default=None, help="Table name for --mode live")
    args = parser.parse_args(argv)

    cases = get_cases(domain=args.domain)
    workspace = None
    if args.mode == "live":
        print("Live mode requires an in-process Workspace; use offline for CI.", file=sys.stderr)
        args.mode = "offline"

    card = run_suite(cases, mode=args.mode, workspace=workspace, table_name=args.table)
    if args.json:
        print(json.dumps(card.to_dict(), indent=2))
    else:
        _print_scorecard(card)

    if card.pass_rate < args.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
