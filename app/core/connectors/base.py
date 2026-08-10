"""
InsightForgeAI – Live Data Connectors (Phase 4.1)

Abstract interface and shared models for external database connectors.
Goal: connect → preview → register as dataset (same Workspace / DatasetRecord path as file uploads).

Design principles
-----------------
- Read-only by construction (no INSERT/UPDATE/DDL from this layer)
- Credentials never written to git or durable workspace meta in cleartext
- Huge tables: sample + optional LIMIT / WHERE; never silently pull millions of rows
- Clear, non-stack-trace errors for wrong credentials / network / permission
- Same DatasetRecord + cleaning + DuckDB registration path as CSV/Excel
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_identifier(name: str) -> str:
    """SQL-safe identifier for table/schema names used in generated queries."""
    n = re.sub(r"[^a-zA-Z0-9_]", "_", (name or "").strip())
    n = re.sub(r"_+", "_", n).strip("_")
    if not n:
        n = "unnamed"
    if n[0].isdigit():
        n = f"t_{n}"
    return n


@dataclass
class ConnectionConfig:
    """
    In-memory / session connection definition.
    Password is intentionally NOT persisted by the durable store.
    """

    name: str
    dialect: str  # "postgres" | "mysql"
    host: str
    port: int
    database: str
    user: str
    password: Optional[str] = None  # only held in process memory / session
    sslmode: Optional[str] = None  # postgres: prefer / require / disable
    schema: Optional[str] = None  # default search schema (postgres: public)
    # Operational metadata
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    created_at: str = field(default_factory=_now_iso)
    last_health_at: Optional[str] = None
    last_health_ok: Optional[bool] = None
    last_health_message: Optional[str] = None
    last_sync_at: Optional[str] = None
    status: str = "unknown"  # unknown | healthy | error

    def public_dict(self) -> Dict[str, Any]:
        """Safe for UI / logs – never includes password."""
        d = asdict(self)
        d.pop("password", None)
        d["has_password"] = bool(self.password)
        return d

    def source_label(self, table: str, schema: Optional[str] = None) -> str:
        sch = schema or self.schema or ""
        if sch:
            return f"{self.dialect}://{self.host}/{self.database}.{sch}.{table}"
        return f"{self.dialect}://{self.host}/{self.database}.{table}"


@dataclass
class TableInfo:
    name: str
    schema: Optional[str] = None
    row_estimate: Optional[int] = None
    column_count: Optional[int] = None
    columns: List[str] = field(default_factory=list)
    table_type: str = "BASE TABLE"  # BASE TABLE | VIEW

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BaseConnector(ABC):
    """
    Contract every live connector must implement.

    Implementations must:
    - open short-lived connections (no long-lived pools required for Phase 4.1)
    - never execute mutating SQL
    - return friendly errors (no raw stack traces to the UI layer)
    """

    dialect: str = "base"

    def __init__(self, config: ConnectionConfig):
        self.config = config

    # ------------------------------------------------------------------
    # Required interface
    # ------------------------------------------------------------------

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """
        Returns (ok, message).
        Must not raise; catch all driver errors and return (False, human message).
        """

    @abstractmethod
    def list_tables(self, schema: Optional[str] = None) -> List[TableInfo]:
        """List tables/views the user can load. Prefer BASE TABLE first."""

    @abstractmethod
    def get_table_schema(self, table: str, schema: Optional[str] = None) -> pd.DataFrame:
        """
        Return a DataFrame with at least: column_name, data_type, is_nullable.
        """

    @abstractmethod
    def preview_table(
        self,
        table: str,
        limit: int = 50,
        schema: Optional[str] = None,
    ) -> pd.DataFrame:
        """Sample rows for the UI picker."""

    @abstractmethod
    def load_table(
        self,
        table: str,
        limit: Optional[int] = None,
        schema: Optional[str] = None,
        where: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Load data into a pandas DataFrame.
        If limit is None and the table is huge, implementations should still
        protect with a soft upper bound or require the caller to pass limit.
        """

    def health(self) -> Dict[str, Any]:
        ok, msg = self.test_connection()
        self.config.last_health_at = _now_iso()
        self.config.last_health_ok = ok
        self.config.last_health_message = msg
        self.config.status = "healthy" if ok else "error"
        return {
            "ok": ok,
            "message": msg,
            "checked_at": self.config.last_health_at,
            "status": self.config.status,
            "config": self.config.public_dict(),
        }

    def mark_synced(self) -> None:
        self.config.last_sync_at = _now_iso()

    # ------------------------------------------------------------------
    # Helpers shared by drivers
    # ------------------------------------------------------------------

    def _quote_ident(self, ident: str) -> str:
        """Dialect-aware identifier quoting. Override in subclasses if needed."""
        return f'"{safe_identifier(ident)}"'

    def _full_table_name(self, table: str, schema: Optional[str] = None) -> str:
        sch = schema or self.config.schema
        t = safe_identifier(table)
        if sch:
            return f"{self._quote_ident(sch)}.{self._quote_ident(t)}"
        return self._quote_ident(t)

    def _friendly_error(self, exc: Exception) -> str:
        """Map common driver exceptions to short, actionable messages."""
        text = str(exc).lower()
        if any(x in text for x in ("password", "authentication", "auth failed", "access denied")):
            return "Authentication failed. Check username and password."
        if any(x in text for x in ("could not connect", "connection refused", "timeout", "timed out", "network")):
            return "Could not reach the database host. Check host, port, and network/firewall."
        if any(x in text for x in ("does not exist", "unknown database", "no database")):
            return "Database or schema does not exist. Check the database name."
        if any(x in text for x in ("permission", "privilege", "not allowed")):
            return "Permission denied. The user needs SELECT on the target tables."
        # Truncate long driver messages
        raw = str(exc).strip()
        if len(raw) > 180:
            raw = raw[:177] + "..."
        return f"Connection error: {raw}"


__all__ = [
    "ConnectionConfig",
    "TableInfo",
    "BaseConnector",
    "safe_identifier",
]
