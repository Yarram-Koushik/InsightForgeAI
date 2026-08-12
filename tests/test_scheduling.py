"""
Phase 4.5 – Scheduling, saved insights, due logic, delivery stubs.
Run: pytest tests/test_scheduling.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.scheduling import (
    SavedInsight,
    Schedule,
    add_insight,
    compute_next_run,
    create_schedule,
    delete_schedule,
    get_schedule,
    is_due,
    load_insights,
    load_schedules,
    remove_insight,
    run_schedule,
    update_schedule,
)


@pytest.fixture(autouse=True)
def _tmp_data(monkeypatch, tmp_path):
    # Isolate all durable paths under tmp
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "workspaces").mkdir(parents=True)
    (tmp_path / "data" / "schedules").mkdir(parents=True)
    yield


def test_compute_next_run_interval():
    after = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    nxt = compute_next_run(interval_minutes=30, after=after)
    assert nxt == "2026-01-01T12:30:00Z"


def test_compute_next_run_daily():
    after = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    nxt = compute_next_run(daily_at="09:00", after=after)
    # already past 09:00 → next day
    assert nxt == "2026-01-02T09:00:00Z"


def test_create_list_delete_schedule():
    s = create_schedule(
        name="Daily revenue",
        workspace_id="default",
        kind="question",
        question="total revenue by region",
        table_name="orders",
        daily_at="08:00",
        channel="log",
        created_by="admin1",
    )
    assert s.id
    assert s.next_run_at
    items = load_schedules("default")
    assert any(x.id == s.id for x in items)
    got = get_schedule("default", s.id)
    assert got is not None
    assert got.name == "Daily revenue"
    ok = delete_schedule("default", s.id)
    assert ok
    assert get_schedule("default", s.id) is None


def test_create_schedule_validation():
    with pytest.raises(ValueError):
        create_schedule(name="x", kind="question", question="", interval_minutes=10)
    with pytest.raises(ValueError):
        create_schedule(name="x", kind="question", question="hi")  # no cadence


def test_is_due_and_update():
    s = create_schedule(
        name="Soon",
        question="count rows",
        table_name="t",
        interval_minutes=60,
        channel="log",
    )
    # Force next_run in the past
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = update_schedule("default", s.id, next_run_at=past, enabled=True)
    assert updated is not None
    assert is_due(updated) is True
    updated2 = update_schedule("default", s.id, enabled=False)
    assert is_due(updated2) is False


def test_saved_insights_roundtrip():
    ins = SavedInsight(
        id="abc12345",
        name="AOV check",
        question="what is aov",
        table_name="orders",
        workspace_id="default",
        created_by="analyst1",
    )
    add_insight(ins)
    items = load_insights("default")
    assert any(i.id == "abc12345" for i in items)
    assert remove_insight("default", "abc12345") is True
    assert remove_insight("default", "abc12345") is False


def test_run_schedule_log_channel_no_workspace():
    s = create_schedule(
        name="Log only",
        question="total revenue",
        table_name="orders",
        interval_minutes=15,
        channel="log",
    )
    # No workspace → still returns structured payload, does not crash
    result = run_schedule(s, workspace=None, orchestrator_run=None)
    assert "success" in result
    assert "summary" in result
    # Persisted last_run
    got = get_schedule("default", s.id)
    assert got is not None
    assert got.run_count >= 1
    assert got.last_run_at is not None


def test_run_schedule_with_mock_orchestrator():
    class _R:
        success = True
        insight = "Revenue is stable"
        message = "ok"
        sql = "SELECT 1"
        result_df = None
        error = None

    def fake_run(**kwargs):
        return _R()

    s = create_schedule(
        name="Mocked",
        question="total revenue",
        table_name="orders",
        interval_minutes=10,
        channel="log",
    )

    class _WS:
        def list_datasets(self):
            return ["orders"]

    result = run_schedule(s, workspace=_WS(), orchestrator_run=fake_run)
    assert result["success"] is True
    assert "Revenue is stable" in (result.get("summary") or "")
    assert result.get("delivery_ok") is True
