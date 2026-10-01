"""Thin PostgreSQL helpers around a psycopg connection pool."""

import atexit
import logging
import os

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

_pool = None
SCHEMA_LOCK_ID = 0x5A17C0D  # arbitrary key for pg_advisory_lock

__all__ = ["init_db", "query", "query_one", "execute", "Jsonb", "available"]


def init_db(app):
    global _pool
    _pool = ConnectionPool(
        app.config["DATABASE_URL"],
        min_size=1,
        max_size=int(os.environ.get("CONDUCTOR_DB_POOL", "8")),
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=False,
    )
    atexit.register(_pool.close)
    try:
        _pool.open(wait=True, timeout=10)
        schema = os.path.join(os.path.dirname(__file__), "schema.sql")
        with open(schema, encoding="utf-8") as fh, _pool.connection() as conn:
            # several gunicorn workers start together: IF NOT EXISTS alone is not race-safe
            conn.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_ID,))
            try:
                conn.execute(fh.read())
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_ID,))
    except Exception as exc:  # pylint: disable=broad-except
        log.error("database initialisation failed: %s", exc)


def available():
    try:
        query_one("SELECT 1 AS ok")
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def query(sql, params=None):
    with _pool.connection() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    with _pool.connection() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount
