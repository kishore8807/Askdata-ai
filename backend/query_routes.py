"""Natural-language → SQL generation, and the guarded execution pipeline."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .ai import agent, slm
from .ai.schema_retriever import schema_for_ui
from .approvals import service as approvals
from .audit.logger import log_event
from .auth.authentication import get_current_user
from .database import db_is_healthy
from .database.postgres import ExecutionError, execute_validated
from .database.sql_validator import validate_sql
from .models import AuditAction, CurrentUser, Decision
from .schemas import ExecuteRequest, GenerateRequest

log = logging.getLogger("query")
router = APIRouter(prefix="/api", tags=["query"])


@router.post("/query/generate")
def generate(body: GenerateRequest, user: CurrentUser = Depends(get_current_user)):
    """Generate SQL with the local SLM. NOTHING is executed here."""
    try:
        return agent.generate_sql(user, body.question.strip(), body.attachment_id)
    except agent.AttachmentMissing as exc:
        raise HTTPException(404, str(exc))
    except slm.AIUnavailable as exc:
        raise HTTPException(503, str(exc))
    except ExecutionError as exc:
        raise HTTPException(503, exc.user_message)
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__module__.startswith("psycopg"):
            log.exception("database error while retrieving schema")
            raise HTTPException(503, "Database connection failed. Please try again later.")
        raise


@router.post("/query/validate")
def validate(body: ExecuteRequest, user: CurrentUser = Depends(get_current_user)):
    """Dry validation of (possibly user-edited) SQL — used by the review screen. Executes nothing."""
    v = validate_sql(body.sql, user.role)
    return {"success": True, **v.summary(), "sql": v.sql if v.valid else body.sql,
            "requires_dba_approval": v.valid and v.decision == Decision.APPROVAL,
            "requires_confirmation": True}


@router.post("/query/execute")
def execute(body: ExecuteRequest, user: CurrentUser = Depends(get_current_user)):
    """Execution pipeline — every step is re-checked server-side, regardless of what the UI did.

    1 authenticate (dependency) → 2 identify role (from DB) → 3 parse → 4 identify operation → 5 RBAC
    → 6 approval requirement → 7 verify approval → 8 validate → 9 execute → 10 commit/rollback
    → 11 audit → 12 return
    """
    role = user.role.value
    v = validate_sql(body.sql, user.role)                      # steps 3-5, 8
    log_event(AuditAction.SQL_EXECUTION_REQUESTED, user_id=user.id, role=role, operation_type=v.operation.value,
              sql=body.sql, target_table=v.target_table, ip=user.ip, metadata={"decision": v.decision.value})

    if not v.valid:
        if v.decision == Decision.DENY and v.permission_message:
            log_event(AuditAction.PERMISSION_DENIED, user_id=user.id, role=role, operation_type=v.operation.value,
                      sql=body.sql, target_table=v.target_table, status="DENIED", ip=user.ip)
            return JSONResponse(status_code=403, content={"success": False, "code": "PERMISSION_DENIED",
                                                         "message": v.permission_message})
        log_event(AuditAction.SQL_REJECTED, user_id=user.id, role=role, operation_type=v.operation.value,
                  sql=body.sql, target_table=v.target_table, status="REJECTED", ip=user.ip,
                  metadata={"errors": v.errors})
        return JSONResponse(status_code=422, content={
            "success": False, "code": "VALIDATION_FAILED",
            "message": "The generated SQL failed validation.", "errors": v.errors})

    # step 6/7 — sensitive changes by non-DBA roles never execute here; they become approval requests
    if v.decision == Decision.APPROVAL:
        try:
            req = approvals.create_request(user, body.question, v)
        except approvals.ApprovalError as exc:
            raise HTTPException(exc.status_code, exc.message)
        return JSONResponse(status_code=202, content=_json({
            "success": True, "approval_required": True, "approval_id": req["id"], "status": req["status"],
            "operation_type": v.operation.value, "target_table": v.target_table,
            "message": "DBA approval is required before this query can execute. Your request has been sent to the DBA.",
            "dangerous": v.dangerous, "danger_reasons": v.danger_reasons}))

    # dangerous DBA operations need explicit confirmation
    if v.dangerous and not body.confirm_dangerous:
        return JSONResponse(status_code=409, content={
            "success": False, "code": "DANGEROUS_CONFIRMATION_REQUIRED", "requires_dangerous_confirmation": True,
            "message": "This is a dangerous operation. Please confirm to proceed.",
            "danger_reasons": v.danger_reasons})

    # steps 9-12
    try:
        result = execute_validated(v)
    except ExecutionError as exc:
        log_event(AuditAction.DATABASE_ERROR, user_id=user.id, role=role, operation_type=v.operation.value,
                  sql=v.sql, target_table=v.target_table, status="FAILED", ip=user.ip,
                  metadata={"error": (exc.technical or exc.user_message)[:500]})
        status = 503 if exc.connection_failed else 400
        return JSONResponse(status_code=status, content={"success": False, "code": "EXECUTION_FAILED",
                                                         "message": exc.user_message})
    log_event(AuditAction.SQL_EXECUTED, user_id=user.id, role=role, operation_type=v.operation.value, sql=v.sql,
              target_table=v.target_table, ip=user.ip,
              metadata={"message": result.message, "dangerous_confirmed": bool(v.dangerous)})
    return _json({"success": True, "operation_type": v.operation.value, "target_table": v.target_table,
                  "executed_sql": v.sql, **result.to_payload()})


@router.get("/query/history")
def history(limit: int = 50, user: CurrentUser = Depends(get_current_user)):
    from .database import app_conn
    limit = max(1, min(limit, 200))
    with app_conn() as conn:
        rows = conn.execute(
            """SELECT id, action, operation_type, sql_query, target_table, status, created_at,
                      metadata->>'question' AS question
                 FROM audit_logs
                WHERE user_id = %s AND action IN ('SQL_GENERATED','SQL_EXECUTED','APPROVAL_REQUESTED')
                  AND sql_query IS NOT NULL
                ORDER BY created_at DESC, id DESC LIMIT %s""", (user.id, limit)).fetchall()
    return _json({"success": True, "history": rows})


@router.get("/schema")
def schema(user: CurrentUser = Depends(get_current_user)):
    return {"success": True, "tables": schema_for_ui(user.role)}


@router.get("/status")
def status(user: CurrentUser = Depends(get_current_user)):
    from .config import settings
    return {"success": True, "database": {"online": db_is_healthy(), "name": settings.postgres_db},
            "ai": slm.status()}


def _json(data):
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(data)
