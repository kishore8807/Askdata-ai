"""Connection pools. Two pools so a slow user query can never starve authentication/metadata calls."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import settings

log = logging.getLogger("db")

_app_pool: ConnectionPool | None = None
_exec_pool: ConnectionPool | None = None


def init_pools() -> None:
    """Open both pools. Raises if PostgreSQL is unreachable."""
    global _app_pool, _exec_pool
    if _app_pool is None:
        _app_pool = ConnectionPool(
            settings.conninfo, min_size=settings.pool_min, max_size=settings.pool_max,
            kwargs={"row_factory": dict_row}, open=False, timeout=settings.connect_timeout, name="app",
        )
        _app_pool.open(wait=True, timeout=settings.connect_timeout + 2)
    if _exec_pool is None:
        _exec_pool = ConnectionPool(
            settings.exec_conninfo, min_size=1, max_size=max(2, settings.pool_max // 2),
            open=False, timeout=settings.connect_timeout, name="exec",
        )
        _exec_pool.open(wait=True, timeout=settings.connect_timeout + 2)


def close_pools() -> None:
    global _app_pool, _exec_pool
    for p in (_app_pool, _exec_pool):
        if p is not None:
            p.close()
    _app_pool = _exec_pool = None


def app_pool() -> ConnectionPool:
    if _app_pool is None:
        init_pools()
    assert _app_pool is not None
    return _app_pool


def exec_pool() -> ConnectionPool:
    if _exec_pool is None:
        init_pools()
    assert _exec_pool is not None
    return _exec_pool


@contextmanager
def app_conn() -> Iterator[psycopg.Connection]:
    """Connection for application metadata. Commits on success, rolls back on error."""
    with app_pool().connection() as conn:
        yield conn


def db_is_healthy() -> bool:
    try:
        with app_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        log.exception("database health check failed")
        return False
