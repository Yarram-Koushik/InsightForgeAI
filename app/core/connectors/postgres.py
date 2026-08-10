"""
PostgreSQL connector (Phase 4.1).

Uses psycopg2 (DBAPI) + pandas.read_sql.
Read-only: only SELECT / information_schema queries are issued.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from .base import BaseConnector, ConnectionConfig, TableInfo, safe_identifier


class PostgresConnector(BaseConnector):
    dialect = "postgres"

    def __init__(self, config: ConnectionConfig):
        if config.dialect != "postgres":
            config.dialect = "postgres"
        super().__init__(config)
        if not config.port:
            config.port = 5432
        if not config.schema:
            config.schema = "public"

    def _connect(self):
        try:
            import psycopg2
            from psycopg2 import OperationalError  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "psycopg2 is required for PostgreSQL connectors. "
                "Install with: pip install psycopg2-binary"
            ) from e

        kwargs = {
            "host": self.config.host,
            "port": int(self.config.port or 5432),
            "dbname": self.config.database,
            "user": self.config.user,
            "password": self.config.password or "",
            "connect_timeout": 8,
        }
        if self.config.sslmode:
            kwargs["sslmode"] = self.config.sslmode

        return psycopg2.connect(**kwargs)

    def test_connection(self) -> Tuple[bool, str]:
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT version();")
                    ver = cur.fetchone()
                    version_str = (ver[0] if ver else "PostgreSQL")[:80]
                return True, f"Connected. {version_str}"
            finally:
                conn.close()
        except Exception as e:
            return False, self._friendly_error(e)

    def list_tables(self, schema: Optional[str] = None) -> List[TableInfo]:
        sch = schema or self.config.schema or "public"
        # Prefer richer estimate when pg_catalog is readable; fall back to pure
        # information_schema so managed / locked-down Postgres still works.
        rich_sql = """
            SELECT
                t.table_schema,
                t.table_name,
                t.table_type,
                COALESCE(s.n_live_tup, c.reltuples::bigint, 0) AS row_estimate
            FROM information_schema.tables t
            LEFT JOIN pg_namespace n
                ON n.nspname = t.table_schema
            LEFT JOIN pg_class c
                ON c.relname = t.table_name AND c.relnamespace = n.oid
            LEFT JOIN pg_stat_user_tables s
                ON s.relname = t.table_name AND s.schemaname = t.table_schema
            WHERE t.table_schema = %s
              AND t.table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY
                CASE WHEN t.table_type = 'BASE TABLE' THEN 0 ELSE 1 END,
                t.table_name
        """
        simple_sql = """
            SELECT
                table_schema,
                table_name,
                table_type,
                NULL::bigint AS row_estimate
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY
                CASE WHEN table_type = 'BASE TABLE' THEN 0 ELSE 1 END,
                table_name
        """
        try:
            conn = self._connect()
            try:
                try:
                    df = pd.read_sql(rich_sql, conn, params=(sch,))
                except Exception:
                    df = pd.read_sql(simple_sql, conn, params=(sch,))
            finally:
                conn.close()
        except Exception as e:
            raise RuntimeError(self._friendly_error(e)) from e

        tables: List[TableInfo] = []
        for _, row in df.iterrows():
            est = row.get("row_estimate")
            tables.append(
                TableInfo(
                    name=str(row["table_name"]),
                    schema=str(row["table_schema"]),
                    row_estimate=int(est) if est is not None and pd.notna(est) else None,
                    table_type=str(row["table_type"]),
                )
            )
        # Enrich with column counts (lightweight second query)
        if tables:
            try:
                col_sql = """
                    SELECT table_name, COUNT(*) AS column_count
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    GROUP BY table_name
                """
                conn = self._connect()
                try:
                    cdf = pd.read_sql(col_sql, conn, params=(sch,))
                finally:
                    conn.close()
                col_map = {str(r["table_name"]): int(r["column_count"]) for _, r in cdf.iterrows()}
                for t in tables:
                    t.column_count = col_map.get(t.name)
            except Exception:
                pass
        return tables

    def get_table_schema(self, table: str, schema: Optional[str] = None) -> pd.DataFrame:
        sch = schema or self.config.schema or "public"
        sql = """
            SELECT
                column_name,
                data_type,
                is_nullable,
                column_default,
                character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
        """
        try:
            conn = self._connect()
            try:
                return pd.read_sql(sql, conn, params=(sch, table))
            finally:
                conn.close()
        except Exception as e:
            raise RuntimeError(self._friendly_error(e)) from e

    def preview_table(
        self,
        table: str,
        limit: int = 50,
        schema: Optional[str] = None,
    ) -> pd.DataFrame:
        limit = max(1, min(int(limit or 50), 500))
        full = self._full_table_name(table, schema)
        sql = f"SELECT * FROM {full} LIMIT {limit}"
        try:
            conn = self._connect()
            try:
                return pd.read_sql(sql, conn)
            finally:
                conn.close()
        except Exception as e:
            raise RuntimeError(self._friendly_error(e)) from e

    def load_table(
        self,
        table: str,
        limit: Optional[int] = None,
        schema: Optional[str] = None,
        where: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Load table content.
        Hard safety: if limit is None we still apply a soft ceiling of 500_000 rows
        and surface a warning via metadata (caller can re-request with higher limit).
        WHERE clause is accepted only if it is a simple predicate (no semicolon / comments).
        """
        full = self._full_table_name(table, schema)
        where_sql = ""
        if where:
            cleaned = where.strip().rstrip(";")
            if ";" in cleaned or "--" in cleaned or "/*" in cleaned:
                raise ValueError("Unsafe WHERE clause rejected.")
            where_sql = f" WHERE {cleaned}"

        # Soft upper bound to protect memory / network on first load
        effective_limit = limit
        if effective_limit is None:
            effective_limit = 500_000
        else:
            effective_limit = max(1, int(effective_limit))

        sql = f"SELECT * FROM {full}{where_sql} LIMIT {effective_limit}"
        try:
            conn = self._connect()
            try:
                df = pd.read_sql(sql, conn)
            finally:
                conn.close()
            return df
        except Exception as e:
            raise RuntimeError(self._friendly_error(e)) from e


def from_env(name: str = "postgres_default") -> Optional[PostgresConnector]:
    """
    Build a connector from environment variables (never committed secrets).

    Supports either:
      POSTGRES_URL / DATABASE_URL  (postgresql://user:pass@host:port/db)
    or discrete:
      POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
    """
    import os
    from urllib.parse import urlparse, unquote

    url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
    if url and url.startswith(("postgres://", "postgresql://")):
        parsed = urlparse(url)
        cfg = ConnectionConfig(
            name=name,
            dialect="postgres",
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            database=(parsed.path or "/").lstrip("/") or "postgres",
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or "") if parsed.password else None,
            sslmode=os.getenv("POSTGRES_SSLMODE"),
        )
        return PostgresConnector(cfg)

    host = os.getenv("POSTGRES_HOST")
    if not host:
        return None
    cfg = ConnectionConfig(
        name=name,
        dialect="postgres",
        host=host,
        port=int(os.getenv("POSTGRES_PORT") or 5432),
        database=os.getenv("POSTGRES_DB") or os.getenv("POSTGRES_DATABASE") or "postgres",
        user=os.getenv("POSTGRES_USER") or "postgres",
        password=os.getenv("POSTGRES_PASSWORD"),
        sslmode=os.getenv("POSTGRES_SSLMODE"),
    )
    return PostgresConnector(cfg)


__all__ = ["PostgresConnector", "from_env"]
