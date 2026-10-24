"""DBA approval workflow. All state lives in PostgreSQL; the client's idea of 'approved' is never consulted."""
from __future__ import annotations

import logging
from typing import Any

from ..audit.logger import log_event
from ..database import app_conn
from ..database.postgres import ExecutionError, execute_validated
from ..database.sql_validator import ValidationResult, validate_sql
from ..models import ApprovalStatus, AuditAction, CurrentUser, Decision, Role

log = logging.getLogger("approvals")


class ApprovalError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ───────── notifications ─────────
def notify(conn, user_id: int, title: str, message: str, approval_id: int | None = None) -> None:
    conn.execute("INSERT INTO notifications (user_id, title, message, approval_id) VALUES (%s,%s,%s,%s)",
                 (user_id, title, message, approval_id))


def list_notifications(user_id: int, limit: int = 30) -> dict[str, Any]:
    with app_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, message, approval_id, is_read, created_at FROM notifications "
            "WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s", (user_id, limit)).fetchall()
        unread = conn.execute("SELECT count(*) AS c FROM notifications WHERE user_id=%s AND NOT is_read",
                              (user_id,)).fetchone()["c"]
    return {"notifications": rows, "unread": unread}


def mark_notifications_read(user_id: int, ids: list[int] | None = None) -> None:
    with app_conn() as conn:
        if ids:
            conn.execute("UPDATE notifications SET is_read = TRUE WHERE user_id=%s AND id = ANY(%s)", (user_id, ids))
        else:
            conn.execute("UPDATE notifications SET is_read = TRUE WHERE user_id=%s", (user_id,))


# ───────── requests ─────────
_SELECT = """SELECT r.id, r.requester_id, u.username AS requester_name, r.requester_role,
                    r.natural_language_request, r.generated_sql, r.operation_type, r.target_table,
                    r.status, r.reviewed_by, rv.username AS reviewed_by_name, r.reviewed_at,
                    r.rejection_reason, r.execution_message, r.created_at, r.updated_at
               FROM approval_requests r
               JOIN users u ON u.id = r.requester_id
               LEFT JOIN users rv ON rv.id = r.reviewed_by"""


def create_request(user: CurrentUser, question: str | None, v: ValidationResult) -> dict[str, Any]:
    """Create a PENDING request for a validated, approval-requiring statement and notify every active DBA."""
    if v.decision != Decision.APPROVAL or not v.valid:
        raise ApprovalError("This statement does not require approval.")
    with app_conn() as conn:
        existing = conn.execute(
            _SELECT + " WHERE r.requester_id=%s AND r.status='PENDING' AND r.generated_sql=%s",
            (user.id, v.sql)).fetchone()
        if existing:
            return existing
        row = conn.execute(
            """INSERT INTO approval_requests (requester_id, requester_role, natural_language_request,
                                              generated_sql, operation_type, target_table)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (user.id, user.role.value, question, v.sql, v.operation.value, v.target_table)).fetchone()
        rid = row["id"]
        dbas = conn.execute("SELECT id FROM users WHERE role='DBA' AND is_active").fetchall()
        for d in dbas:
            notify(conn, d["id"], "New Database Change Request",
                   f"{user.role.label} {user.username} requested a {v.operation.value} operation"
                   f"{' on ' + v.target_table if v.target_table else ''}. DBA review required.", rid)
        out = conn.execute(_SELECT + " WHERE r.id=%s", (rid,)).fetchone()
    log_event(AuditAction.APPROVAL_REQUESTED, user_id=user.id, role=user.role.value, operation_type=v.operation.value,
              sql=v.sql, target_table=v.target_table, ip=user.ip, status="PENDING",
              metadata={"approval_id": rid, "dangerous": v.dangerous})
    return out


def list_requests(user: CurrentUser, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """DBA sees every request; others only their own."""
    where, params = [], []
    if user.role != Role.DBA:
        where.append("r.requester_id = %s"); params.append(user.id)
    if status:
        where.append("r.status = %s"); params.append(status.upper())
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with app_conn() as conn:
        rows = conn.execute(f"{_SELECT} {clause} ORDER BY r.created_at DESC LIMIT %s", (*params, limit)).fetchall()
    return rows


def _annotate(row: dict[str, Any]) -> dict[str, Any]:
    """Add live validation info (danger warnings) so the DBA review screen can show them."""
    v = validate_sql(row["generated_sql"], Role(row["requester_role"]))
    row["dangerous"] = v.dangerous
    row["danger_reasons"] = v.danger_reasons
    row["operations"] = [o.value for o in v.operations]
    row["statement_count"] = len(v.statements)
    return row


def list_pending(user: CurrentUser) -> list[dict[str, Any]]:
    return [_annotate(r) for r in list_requests(user, status="PENDING")]


def approve(request_id: int, dba: CurrentUser, *, confirm_dangerous: bool = False) -> dict[str, Any]:
    if dba.role != Role.DBA:  # defence in depth — routes also enforce this
        raise ApprovalError("You do not have permission to perform this operation.", 403)

    # 1) atomically claim the request (row lock prevents double approval / approve-vs-reject races)
    with app_conn() as conn:
        row = conn.execute("SELECT * FROM approval_requests WHERE id=%s FOR UPDATE", (request_id,)).fetchone()
        if not row:
            raise ApprovalError("Approval request not found.", 404)
        if row["status"] != ApprovalStatus.PENDING.value:
            raise ApprovalError(f"This request is already {row['status']}.", 409)
        if row["requester_id"] == dba.id:
            raise ApprovalError("You cannot approve your own request.", 403)
        requester = conn.execute("SELECT id, username, role, is_active FROM users WHERE id=%s",
                                 (row["requester_id"],)).fetchone()

        # 2) re-validate the *stored* SQL against the requester's role right now
        v = validate_sql(row["generated_sql"], Role(row["requester_role"]))
        if not v.valid or v.decision == Decision.DENY or not requester or not requester["is_active"]:
            conn.execute("UPDATE approval_requests SET status='FAILED', reviewed_by=%s, reviewed_at=NOW(), "
                         "execution_message=%s, updated_at=NOW() WHERE id=%s",
                         (dba.id, "The SQL no longer passes validation.", request_id))
            notify(conn, row["requester_id"], "Change request failed",
                   "Your database change request could not be executed because it failed validation.", request_id)
            failed = True
        else:
            failed = False
            if v.dangerous and not confirm_dangerous:
                raise ApprovalError("This operation is dangerous. Tick the confirmation box to approve it.", 400)
            conn.execute("UPDATE approval_requests SET status='APPROVED', reviewed_by=%s, reviewed_at=NOW(), "
                         "updated_at=NOW() WHERE id=%s", (dba.id, request_id))
    if failed:
        log_event(AuditAction.SQL_REJECTED, user_id=dba.id, role=dba.role.value, sql=row["generated_sql"],
                  status="FAILED", ip=dba.ip, metadata={"approval_id": request_id, "reason": "validation"})
        raise ApprovalError("The generated SQL failed validation.", 422)

    log_event(AuditAction.APPROVAL_APPROVED, user_id=dba.id, role=dba.role.value, operation_type=v.operation.value,
              sql=v.sql, target_table=v.target_table, ip=dba.ip,
              metadata={"approval_id": request_id, "requester_id": row["requester_id"],
                        "dangerous_confirmed": bool(v.dangerous and confirm_dangerous)})

    # 3) execute (status APPROVED is now committed in the DB)
    try:
        result = execute_validated(v)
    except ExecutionError as exc:
        with app_conn() as conn:
            conn.execute("UPDATE approval_requests SET status='FAILED', execution_message=%s, updated_at=NOW() "
                         "WHERE id=%s", (exc.user_message, request_id))
            notify(conn, row["requester_id"], "Change request failed",
                   f"Your approved request could not be executed: {exc.user_message}", request_id)
        log_event(AuditAction.DATABASE_ERROR, user_id=dba.id, role=dba.role.value, operation_type=v.operation.value,
                  sql=v.sql, target_table=v.target_table, status="FAILED", ip=dba.ip,
                  metadata={"approval_id": request_id, "error": (exc.technical or exc.user_message)[:500]})
        return {"success": False, "status": ApprovalStatus.FAILED.value, "message": exc.user_message,
                "approval_id": request_id}

    with app_conn() as conn:
        conn.execute("UPDATE approval_requests SET status='EXECUTED', execution_message=%s, updated_at=NOW() "
                     "WHERE id=%s", (result.message, request_id))
        notify(conn, row["requester_id"], "Change request approved",
               f"Your database change request was approved. {result.message}", request_id)
    log_event(AuditAction.SQL_EXECUTED, user_id=dba.id, role=dba.role.value, operation_type=v.operation.value,
              sql=v.sql, target_table=v.target_table, ip=dba.ip,
              metadata={"approval_id": request_id, "requested_by": row["requester_id"], "message": result.message})
    return {"success": True, "status": ApprovalStatus.EXECUTED.value, "message": result.message,
            "approval_id": request_id, **result.to_payload()}


def reject(request_id: int, dba: CurrentUser, reason: str) -> dict[str, Any]:
    if dba.role != Role.DBA:
        raise ApprovalError("You do not have permission to perform this operation.", 403)
    with app_conn() as conn:
        row = conn.execute("SELECT * FROM approval_requests WHERE id=%s FOR UPDATE", (request_id,)).fetchone()
        if not row:
            raise ApprovalError("Approval request not found.", 404)
        if row["status"] != ApprovalStatus.PENDING.value:
            raise ApprovalError(f"This request is already {row['status']}.", 409)
        conn.execute("UPDATE approval_requests SET status='REJECTED', reviewed_by=%s, reviewed_at=NOW(), "
                     "rejection_reason=%s, updated_at=NOW() WHERE id=%s", (dba.id, reason, request_id))
        notify(conn, row["requester_id"], "Change request rejected",
               f"Your database change request was rejected. Reason: {reason}", request_id)
    log_event(AuditAction.APPROVAL_REJECTED, user_id=dba.id, role=dba.role.value, operation_type=row["operation_type"],
              sql=row["generated_sql"], target_table=row["target_table"], status="REJECTED", ip=dba.ip,
              metadata={"approval_id": request_id, "reason": reason, "requester_id": row["requester_id"]})
    return {"success": True, "status": ApprovalStatus.REJECTED.value, "approval_id": request_id}
