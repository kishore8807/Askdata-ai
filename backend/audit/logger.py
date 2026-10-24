"""Audit logging. Never raises (a logging failure must not break the request) and never stores secrets."""
from __future__ import annotations

import json
import logging
from typing import Any

from psycopg.types.json import Jsonb

from ..database import app_conn
from ..models import AuditAction

log = logging.getLogger("audit")

_SENSITIVE = ("password", "secret", "token", "authorization", "cookie", "hash")


def _clean(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not meta:
        return {}
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if any(s in k.lower() for s in _SENSITIVE):
            continue
        try:
            json.dumps(v)
            out[k] = v
        except TypeError:
            out[k] = str(v)
    return out


def log_event(
    action: AuditAction | str,
    *,
    user_id: int | None = None,
    role: str | None = None,
    operation_type: str | None = None,
    sql: str | None = None,
    target_table: str | None = None,
    status: str = "SUCCESS",
    ip: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    action_v = action.value if isinstance(action, AuditAction) else str(action)
    try:
        with app_conn() as conn:
            conn.execute(
                """INSERT INTO audit_logs (user_id, role, action, operation_type, sql_query,
                                           target_table, status, ip_address, metadata)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (user_id, role, action_v, operation_type, sql[:20000] if sql else None,
                 target_table, status, ip, Jsonb(_clean(metadata))),
            )
    except Exception:  # noqa: BLE001
        log.exception("failed to write audit log (%s)", action_v)


def list_logs(*, limit: int = 100, offset: int = 0, action: str | None = None, user_id: int | None = None):
    where, params = [], []
    if action:
        where.append("a.action = %s"); params.append(action)
    if user_id:
        where.append("a.user_id = %s"); params.append(user_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with app_conn() as conn:
        rows = conn.execute(
            f"""SELECT a.id, a.user_id, u.username, a.role, a.action, a.operation_type, a.sql_query,
                       a.target_table, a.status, a.ip_address, a.created_at, a.metadata
                  FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
                  {clause} ORDER BY a.created_at DESC, a.id DESC LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        ).fetchall()
        total = conn.execute(f"SELECT count(*) AS c FROM audit_logs a {clause}", params).fetchone()["c"]
    return rows, total
