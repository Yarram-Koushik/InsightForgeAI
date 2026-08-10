"""
MySQL / MariaDB connector (Phase 4.1).

Uses PyMySQL (pure Python) + pandas.read_sql.
Read-only: only SELECT / information_schema queries.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from .base import BaseConnector, ConnectionConfig, TableInfo, safe_identifier


class MySQLConnector(BaseConnector):
    dialect = "mysql"

    def __init__(self, config: ConnectionConfig):
        if config.dialect != "mysql":
            config.dialect = "mysql"
        super().__init__(config)
        if not config.port:
            config.port = 3306

    def _connect(self):
        try:
            import pymysql
        except ImportError as e:
            raise RuntimeError(
                "pymysql is required for MySQL connectors. "
                "Install with: pip install pymysql"
            ) from e

        return pymysql.connect(
            host=self.config.host,
            port=int(self.config.port or 3306),
            user=self.config.user,
            password=self.config.password or "",
            database=self.config.database,
            connect_timeout=8,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.Cursor,
        )

    def _quote_ident(self, ident: str) -> str:
        # MySQL uses backticks
        return f"`{safe_identifier(ident)}`"

    def test_connection(self) -> Tuple[bool, str]:
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT VERSION();")
                    ver = cur.fetchone()
                    version_str = (ver[0] if ver else "MySQL")[:80]
                return True, f"Connected. {version_str}"
            finally:
                conn.close()
        except Exception as e:
            return False, self._friendly_error(e)

    def list_tables(self, schema: Optional[str] = None) -> List[TableInfo]:
        # In MySQL, "schema" == database name
        db = schema or self.config.database
        sql = """
            SELECT
                table_schema,
                table_name,
                table_type,
                table_rows AS row_estimate
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
                df = pd.read_sql(sql, conn, params=(db,))
            finally:
                conn.close()
        except Exception as e:
            raise RuntimeError(self._friendly_error(e)) from e

        tables: List[TableInfo] = []
        for _, row in df.iterrows():
            tables.append(
                TableInfo(
                    name=str(row["table_name"]),
                    schema=str(row["table_schema"]),
                    row_estimate=int(row["row_estimate"]) if pd.notna(row["row_estimate"]) else None,
                    table_type=str(row["table_type"]),
                )
            )
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
                    cdf = pd.read_sql(col_sql, conn, params=(db,))
                finally:
                    conn.close()
                col_map = {str(r["table_name"]): int(r["column_count"]) for _, r in cdf.iterrows()}
                for t in tables:
                    t.column_count = col_map.get(t.name)
            except Exception:
                pass
        return tables

    def get_table_schema(self, table: str, schema: Optional[str] = None) -> pd.DataFrame:
        db = schema or self.config.database
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
                return pd.read_sql(sql, conn, params=(db, table))
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
        full = self._full_table_name(table, schema)
        where_sql = ""
        if where:
            cleaned = where.strip().rstrip(";")
            if ";" in cleaned or "--" in cleaned or "/*" in cleaned:
                raise ValueError("Unsafe WHERE clause rejected.")
            where_sql = f" WHERE {cleaned}"

        effective_limit = 500_000 if limit is None else max(1, int(limit))
        sql = f"SELECT * FROM {full}{where_sql} LIMIT {effective_limit}"
        try:
            conn = self._connect()
            try:
                return pd.read_sql(sql, conn)
            finally:
                conn.close()
        except Exception as e:
            raise RuntimeError(self._friendly_error(e)) from e


def from_env(name: str = "mysql_default") -> Optional[MySQLConnector]:
    """
    Build from env:
      MYSQL_URL (mysql://user:pass@host:port/db)
    or MYSQL_HOST, MYSQL_PORT, MYSQL_DB, MYSQL_USER, MYSQL_PASSWORD
    """
    import os
    from urllib.parse import urlparse, unquote

    url = os.getenv("MYSQL_URL")
    if url and url.startswith(("mysql://", "mysql+pymysql://")):
        # strip driver prefix if present
        if url.startswith("mysql+pymysql://"):
            url = "mysql://" + url[len("mysql+pymysql://") :]
        parsed = urlparse(url)
        cfg = ConnectionConfig(
            name=name,
            dialect="mysql",
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            database=(parsed.path or "/").lstrip("/") or "mysql",
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or "") if parsed.password else None,
        )
        return MySQLConnector(cfg)

    host = os.getenv("MYSQL_HOST")
    if not host:
        return None
    cfg = ConnectionConfig(
        name=name,
        dialect="mysql",
        host=host,
        port=int(os.getenv("MYSQL_PORT") or 3306),
        database=os.getenv("MYSQL_DB") or os.getenv("MYSQL_DATABASE") or "mysql",
        user=os.getenv("MYSQL_USER") or "root",
        password=os.getenv("MYSQL_PASSWORD"),
    )
    return MySQLConnector(cfg)


__all__ = ["MySQLConnector", "from_env"]
