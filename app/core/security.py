"""
InsightForgeAI – Security, Auth & Audit (Phase 3.5)

API keys via env INSIGHTFORGE_API_KEYS (id:role:secret,...).
Roles: viewer | analyst | admin. Fail-closed when keys are configured.
Append-only audit log under data/audit/. Upload hardening helpers.
"""

from __future__ import annotations

# Load .env early so INSIGHTFORGE_API_KEYS is visible
try:
    from pathlib import Path as _P
    from dotenv import load_dotenv as _load_dotenv
    _env = _P(__file__).resolve().parents[2] / ".env"
    if _env.exists():
        _load_dotenv(_env, override=False)
    else:
        _load_dotenv(override=False)
except Exception:
    pass

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


_ROLE_RANK = {Role.VIEWER: 1, Role.ANALYST: 2, Role.ADMIN: 3}


@dataclass
class Principal:
    key_id: str
    role: Role
    secret: str = ""

    def can(self, minimum: Role) -> bool:
        return _ROLE_RANK.get(self.role, 0) >= _ROLE_RANK.get(minimum, 99)


@dataclass
class AuditEvent:
    timestamp: str
    action: str
    principal_id: str = "anonymous"
    role: str = "none"
    table_name: Optional[str] = None
    question: Optional[str] = None
    sql: Optional[str] = None
    success: bool = True
    intent: Optional[str] = None
    result_rows: Optional[int] = None
    error_code: Optional[str] = None
    ip: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


_KEY_MAP: Dict[str, Principal] = {}
_KEYS_LOADED = False


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "data" / "audit",
        Path.cwd() / "data" / "audit",
        Path("/tmp") / "insightforge_audit",
    ]
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            return c
        except Exception:
            continue
    return candidates[-1]


def _parse_keys(raw: str) -> Dict[str, Principal]:
    out: Dict[str, Principal] = {}
    if not raw:
        return out
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
        except Exception:
            role = Role.VIEWER
        out[secret] = Principal(key_id=key_id, role=role, secret=secret)
    return out


def reload_keys() -> None:
    global _KEY_MAP, _KEYS_LOADED
    raw = (os.getenv("INSIGHTFORGE_API_KEYS") or "").strip()
    _KEY_MAP = _parse_keys(raw)
    _KEYS_LOADED = True


def _ensure_keys() -> Dict[str, Principal]:
    global _KEYS_LOADED
    if not _KEYS_LOADED:
        reload_keys()
    return _KEY_MAP


def auth_enabled() -> bool:
    return bool(_ensure_keys())


def authenticate(api_key: Optional[str] = None) -> Optional[Principal]:
    keys = _ensure_keys()
    if not keys:
        return None
    if not api_key or not str(api_key).strip():
        return None
    return keys.get(str(api_key).strip())


def require_role(principal: Optional[Principal], minimum: Role) -> bool:
    if not auth_enabled():
        return True
    if principal is None:
        return False
    return principal.can(minimum)


def audit_log(event: AuditEvent) -> None:
    try:
        day = (event.timestamp or now_iso())[:10]
        path = _audit_dir() / f"{day}.jsonl"
        payload = asdict(event)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def read_audit(limit: int = 100, day: Optional[str] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        root = _audit_dir()
        if day:
            files = [root / f"{day}.jsonl"]
        else:
            files = sorted(root.glob("*.jsonl"))
        for fp in files:
            if not fp.exists():
                continue
            try:
                lines = fp.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        if len(rows) > limit:
            rows = rows[-limit:]
    except Exception:
        pass
    return rows


MAX_UPLOAD_BYTES = int(os.getenv("INSIGHTFORGE_MAX_UPLOAD_MB", "50")) * 1024 * 1024
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".parquet"}


def validate_upload(filename: str, content_type: Optional[str], size: int) -> Optional[str]:
    if size is not None and size <= 0:
        return "EMPTY_FILE"
    if size is not None and size > MAX_UPLOAD_BYTES:
        return "FILE_TOO_LARGE"
    name = (filename or "").lower()
    ext = Path(name).suffix
    if ext not in ALLOWED_EXTENSIONS:
        return f"UNSUPPORTED_TYPE:{ext or 'unknown'}"
    return None


def generate_api_key() -> str:
    return "sk-" + secrets.token_urlsafe(24)


reload_keys()


__all__ = [
    "Role", "Principal", "AuditEvent",
    "auth_enabled", "authenticate", "require_role", "reload_keys",
    "audit_log", "read_audit", "validate_upload", "generate_api_key",
    "now_iso", "MAX_UPLOAD_BYTES", "ALLOWED_EXTENSIONS",
]
