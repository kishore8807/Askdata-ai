"""AI database agent: question (+ optional attached file) → schema retrieval → prompt → Qwen → validated SQL proposal.

The agent never executes anything. It returns a *proposal* that a human must review and confirm."""
from __future__ import annotations

import logging
from typing import Any

from ..audit.logger import log_event
from ..database.sql_validator import validate_sql
from ..excel import attachments
from ..models import AuditAction, CurrentUser
from . import slm
from .batch import BatchError, expand, has_placeholders, try_convert_to_template, used_columns
from .schema_retriever import build_schema_prompt
from .sql_generator import SYSTEM_PROMPT, build_user_prompt, parse_model_reply

log = logging.getLogger("agent")


class AttachmentMissing(Exception):
    pass


def _clarify(user: CurrentUser, base: dict, msg: str, question: str) -> dict[str, Any]:
    log_event(AuditAction.SQL_GENERATED, user_id=user.id, role=user.role.value, status="CLARIFICATION",
              ip=user.ip, metadata={"question": question[:500], "reply": msg[:300]})
    return {**base, "sql": None, "needs_clarification": True, "message": msg, "requires_confirmation": False,
            "requires_dba_approval": False}


def generate_sql(user: CurrentUser, question: str, attachment_id: str | None = None) -> dict[str, Any]:
    att = None
    if attachment_id:
        att = attachments.load(user.id, attachment_id)
        if not att:
            raise AttachmentMissing("The attached file is no longer available. Please attach it again.")

    schema_text, tables = build_schema_prompt(question, user.role)
    reply = slm.generate(SYSTEM_PROMPT, build_user_prompt(question, schema_text, user.role.label, att))
    parsed = parse_model_reply(reply)

    base = {"success": True, "question": question, "tables_considered": tables, "model": slm.settings.ollama_model}

    if parsed["kind"] in ("clarify", "cannot", "empty"):
        msg = parsed.get("message") or "I could not produce SQL for that request. Please rephrase it."
        return _clarify(user, base, msg, question)

    sql, batch = parsed["sql"], None
    if att and not has_placeholders(sql):
        converted = try_convert_to_template(sql, att["columns"])
        if converted:
            sql = converted

    if has_placeholders(sql):
        if not att:
            return _clarify(user, base, "This request refers to a file. Attach an Excel or CSV file first, "
                            "then ask again.", question)
        try:
            expanded = expand(sql, att["columns"], att["rows"])
        except BatchError as exc:
            return _clarify(user, base, str(exc), question)
        batch = {"rows": len(att["rows"]), "filename": att["filename"], "columns": used_columns(sql),
                 "template": sql}
        sql = expanded

    v = validate_sql(sql, user.role)
    log_event(AuditAction.SQL_GENERATED, user_id=user.id, role=user.role.value,
              operation_type=v.operation.value, sql=(batch["template"] if batch else sql), target_table=v.target_table,
              status="SUCCESS" if v.valid else "INVALID", ip=user.ip,
              metadata={"question": question[:500], "valid": v.valid, "decision": v.decision.value,
                        "errors": v.errors, "statements": len(v.statements),
                        **({"batch_file": batch["filename"], "batch_rows": batch["rows"]} if batch else {})})
    return {
        **base,
        "sql": v.sql if v.valid else sql,
        "operation_type": v.operation.value,
        "operations": [o.value for o in v.operations],
        "target_table": v.target_table,
        "valid": v.valid,
        "validation_errors": v.errors,
        "permission_denied": v.permission_denied,
        "requires_confirmation": True,
        "requires_dba_approval": v.valid and v.decision.value == "APPROVAL",
        "dangerous": v.dangerous,
        "danger_reasons": v.danger_reasons,
        "statement_count": len(v.statements),
        "batch": batch,
        "needs_clarification": False,
    }
