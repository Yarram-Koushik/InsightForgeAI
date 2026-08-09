"""
Phase 3.5 – Security, Auth & Audit unit tests.
Run: pytest tests/test_security.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.security import (
    Role,
    authenticate,
    auth_enabled,
    require_role,
    reload_keys,
    AuditEvent,
    audit_log,
    read_audit,
    validate_upload,
    generate_api_key,
    now_iso,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("INSIGHTFORGE_API_KEYS", raising=False)
    reload_keys()
    monkeypatch.chdir(tmp_path)
    yield
    monkeypatch.delenv("INSIGHTFORGE_API_KEYS", raising=False)
    reload_keys()


def test_auth_disabled_by_default():
    assert auth_enabled() is False
    assert authenticate("anything") is None
    assert require_role(None, Role.ADMIN) is True


def test_auth_enabled_with_keys(monkeypatch):
    monkeypatch.setenv(
        "INSIGHTFORGE_API_KEYS",
        "admin1:admin:sk-admin-secret,viewer1:viewer:sk-view-secret",
    )
    reload_keys()
    assert auth_enabled() is True

    admin = authenticate("sk-admin-secret")
    assert admin is not None
    assert admin.role == Role.ADMIN
    assert admin.key_id == "admin1"

    viewer = authenticate("sk-view-secret")
    assert viewer is not None
    assert viewer.role == Role.VIEWER

    assert authenticate("wrong-key") is None


def test_role_hierarchy(monkeypatch):
    monkeypatch.setenv(
        "INSIGHTFORGE_API_KEYS",
        "a:admin:sek-a,an:analyst:sek-an,v:viewer:sek-v",
    )
    reload_keys()
    admin = authenticate("sek-a")
    analyst = authenticate("sek-an")
    viewer = authenticate("sek-v")

    assert require_role(admin, Role.VIEWER)
    assert require_role(admin, Role.ADMIN)
    assert require_role(analyst, Role.ANALYST)
    assert not require_role(analyst, Role.ADMIN)
    assert require_role(viewer, Role.VIEWER)
    assert not require_role(viewer, Role.ANALYST)


def test_validate_upload():
    assert validate_upload("a.csv", "text/csv", 100) is None
    assert validate_upload("a.csv", "text/csv", 0) == "EMPTY_FILE"
    assert validate_upload("a.csv", "text/csv", 60 * 1024 * 1024) == "FILE_TOO_LARGE"
    assert validate_upload("a.exe", "application/octet-stream", 100).startswith("UNSUPPORTED_TYPE")


def test_audit_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    audit_log(AuditEvent(
        timestamp=now_iso(),
        action="ask",
        principal_id="admin1",
        role="admin",
        table_name="orders",
        question="how many?",
        success=True,
        result_rows=3,
    ))
    events = read_audit(limit=10)
    assert len(events) >= 1
    assert events[-1]["action"] == "ask"
    assert events[-1]["principal_id"] == "admin1"


def test_generate_api_key():
    k = generate_api_key()
    assert k.startswith("sk-")
    assert len(k) > 20
