"""
InsightForgeAI – Security, Auth & Audit (Phase 3.5)

Simple, production-minded auth for the free-stack API:

- API keys via env (INSIGHTFORGE_API_KEYS) or header
- Roles: viewer | analyst | admin
- Fail-closed when auth is enabled
- Append-only audit log (JSONL) under data/audit/
- Upload hardening helpers (size, content-type)

Not full OAuth / multi-tenant IAM — deliberate MVP that is real and enforceable.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class Role(str, Enum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    ADMIN = "admin"


ROLE_RANK = {
    Role.VIEWER: 1,
    Role.ANALYST: 2,
    Role.ADMIN: 3,
}

ROUTE_PERMISSIONS: Dict[str, Role] = {
    "GET /health": Role.VIEWER,
    "GET /datasets": Role.VIEWER,
    "GET /datasets/{name}/schema": Role.VIEWER,
    "POST /sql": Role.VIEWER,
    "POST /ask": Role.ANALYST,
    "POST /datasets/upload": Role.ANALYST,
    "GET /audit": Role.ADMIN,
}


@dataclass
class Principal:
    key_id: str
    role: Role
    label: str = ""
    raw_key_hash: str = ""

    def can(self, required: Role) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK.get(required, 99)


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _parse_keys_from_env() -> Dict[str, Principal]:
    raw = (os.getenv("INSIGHTFORGE_API_KEYS") or "").strip()
    principals: Dict[str, Principal] = {}
    if not raw:
        return principals
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        if len(bits) != 3:
            continue
        key_id, role_s, secret = bits[0].strip(), bits[1].strip().lower(), bits[2].strip()
        if not key_id or not secret:
            continue
        try:
            role = Role(role_s)
        except ValueError:
            role = Role.VIEWER
        h = _hash_key(secret)
        principals[h] = Principal(key_id=key_id, role=role, label=key_id, raw_key_hash=h)
    return principals


_KEY_STORE: Dict[str, Principal] = _parse_keys_from_env()


def reload_keys() -> None:
    global _KEY_STORE
    _KEY_STORE = _parse_keys_from_env()


def auth_enabled() -> bool:
    return bool(_KEY_STORE)


def authenticate(api_key: Optional[str]) -> Optional[Principal]:
    if not api_key:
        return None
    h = _hash_key(api_key.strip())
    return _KEY_STORE.get(h)


def require_role(principal: Optional[Principal], required: Role) -> bool:
    if not auth_enabled():
        return True
    if principal is None:
        return False
    return principal.can(required)


def _audit_root() -> Path:
    candidates = [
        Path.cwd() / "data" / "audit",
        Path(__file__).resolve().parents[2] / "data" / "audit",
        Path("/tmp") / "insightforge_audit",
    ]
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            return c
        except Exception:
            continue
    return candidates[-1]


@dataclass
class AuditEvent:
    timestamp: str
    action: str
    principal_id: str = "anonymous"
    role: str = "none"
    table_name: Optional[str] = None
    question: Optional[str] = None
    sql: Optional[str] = None
    success: Optional[bool] = None
    intent: Optional[str] = None
    result_rows: Optional[int] = None
    error_code: Optional[str] = None
    ip: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None and v != {} and v != ""}


def audit_log(event: AuditEvent) -> None:
    try:
        root = _audit_root()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = root / f"audit-{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def read_audit(limit: int = 100, day: Optional[str] = None) -> List[Dict[str, Any]]:
    root = _audit_root()
    if day:
        paths = [root / f"audit-{day}.jsonl"]
    else:
        paths = sorted(root.glob("audit-*.jsonl"), reverse=True)
    events: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            continue
        if len(events) >= limit:
            break
    return events[-limit:]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".parquet"}
ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/json",
    "application/octet-stream",
    "text/plain",
}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def validate_upload(filename: str, content_type: Optional[str], size: int) -> Optional[str]:
    if size <= 0:
        return "EMPTY_FILE"
    if size > MAX_UPLOAD_BYTES:
        return "FILE_TOO_LARGE"
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return f"UNSUPPORTED_TYPE:{ext or 'unknown'}"
    return None


def generate_api_key(prefix: str = "sk") -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


__all__ = [
    "Role",
    "Principal",
    "auth_enabled",
    "authenticate",
    "require_role",
    "reload_keys",
    "AuditEvent",
    "audit_log",
    "read_audit",
    "now_iso",
    "validate_upload",
    "MAX_UPLOAD_BYTES",
    "ALLOWED_UPLOAD_EXTENSIONS",
    "generate_api_key",
    "ROUTE_PERMISSIONS",
]
