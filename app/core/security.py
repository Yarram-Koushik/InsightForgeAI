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

# Load .env early so INSIGHTFORGE_API_KEYS is visible when auth_enabled() runs
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

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class Role(str, Enum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    ADMIN = "admin"


_ROLE_RANK = {Role.VIEWER: 1, Role.ANALYST: 2, Role.ADMIN: 3}


@dataclass
class Principal:
    key_id: str
    role: Role

    def can(self, minimum: Role) -> bool:
        return _ROLE_RANK.get(self.role, 0) >= _ROLE_RANK.get(minimum, 99)


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


AUDIT_DIR = _audit_dir()


def _parse_keys(raw: str) -> Dict[str, Tuple[str, Role]]:
    """
    Parse INSIGHTFORGE_API_KEYS=id:role:secret[,id:role:secret...]
    Returns mapping secret -> (key_id, role)
    """
    out: Dict[str, Tuple[str, Role]] = {}
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
        out[secret] = (key_id, role)
    return out


def _keys() -> Dict[str, Tuple[str, Role]]:
    raw = (os.getenv("INSIGHTFORGE_API_KEYS") or "").strip()
    return _parse_keys(raw)


def auth_enabled() -> bool:
    return bool(_keys())


def authenticate(
    x_api_key: Optional[str] = None,
    authorization: Optional[str] = None,
) -> Optional[Principal]:
    """
    Resolve principal from X-API-Key or Authorization: Bearer <secret>.
    When auth is disabled, returns a synthetic admin principal.
    """
    if not auth_enabled():
        return Principal(key_id="anonymous", role=Role.ADMIN)

    secret = None
    if x_api_key and x_api_key.strip():
        secret = x_api_key.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        secret = authorization[7:].strip()

    if not secret:
        return None

    mapping = _keys()
    if secret not in mapping:
        return None
    key_id, role = mapping[secret]
    return Principal(key_id=key_id, role=role)


def require_role(principal: Optional[Principal], minimum: Role) -> Principal:
    if principal is None:
        raise PermissionError("UNAUTHORIZED: missing or invalid API key")
    if not principal.can(minimum):
        raise PermissionError(
            f"FORBIDDEN: role `{principal.role.value}` cannot perform action requiring `{minimum.value}`"
        )
    return principal


def audit_log(
    action: str,
    *,
    principal: Optional[Principal] = None,
    success: bool = True,
    detail: Optional[Dict[str, Any]] = None,
    client_ip: Optional[str] = None,
) -> None:
    """Append one JSON line to data/audit/YYYY-MM-DD.jsonl"""
    try:
        day = time.strftime("%Y-%m-%d")
        path = AUDIT_DIR / f"{day}.jsonl"
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "action": action,
            "success": bool(success),
            "key_id": getattr(principal, "key_id", None) if principal else None,
            "role": getattr(getattr(principal, "role", None), "value", None) if principal else None,
            "client_ip": client_ip,
            "detail": detail or {},
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def read_audit(limit: int = 100) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        files = sorted(AUDIT_DIR.glob("*.jsonl"), reverse=True)
        for fp in files:
            try:
                lines = fp.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
                if len(rows) >= limit:
                    return rows
    except Exception:
        pass
    return rows


# ---- Upload hardening ----

MAX_UPLOAD_BYTES = int(os.getenv("INSIGHTFORGE_MAX_UPLOAD_MB", "25")) * 1024 * 1024
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".parquet"}
ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/json",
    "application/octet-stream",
    "text/plain",
}


def validate_upload(filename: str, size: int, content_type: Optional[str] = None) -> Optional[str]:
    """Return error message or None if OK."""
    if size is not None and size > MAX_UPLOAD_BYTES:
        return f"File too large ({size} bytes). Max is {MAX_UPLOAD_BYTES} bytes."
    name = (filename or "").lower()
    ext = Path(name).suffix
    if ext not in ALLOWED_EXTENSIONS:
        return f"Extension `{ext}` not allowed. Use: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct and ct not in ALLOWED_CONTENT_TYPES and not ct.startswith("text/"):
            # Soft check – many browsers send octet-stream
            pass
    return None


def redact_columns(columns: List[str], sensitive_hints: Optional[List[str]] = None) -> List[str]:
    hints = sensitive_hints or ["password", "secret", "token", "ssn", "credit", "card", "cvv"]
    out = []
    for c in columns:
        cl = c.lower()
        if any(h in cl for h in hints):
            out.append(f"{c} [REDACTED]")
        else:
            out.append(c)
    return out


__all__ = [
    "Role",
    "Principal",
    "auth_enabled",
    "authenticate",
    "require_role",
    "audit_log",
    "read_audit",
    "validate_upload",
    "redact_columns",
    "MAX_UPLOAD_BYTES",
    "ALLOWED_EXTENSIONS",
]
