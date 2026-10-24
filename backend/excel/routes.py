"""Attach a data file (.xlsx / .xls / .csv) to a natural-language request."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..audit.logger import log_event
from ..auth.authentication import get_current_user
from ..config import settings
from ..models import AuditAction, CurrentUser
from . import attachments
from .parser import AttachmentError, parse_table

log = logging.getLogger("attachments")
router = APIRouter(prefix="/api/attachments", tags=["attachments"])


def _describe(att_id: str, filename: str, parsed: dict) -> dict:
    return {"attachment_id": att_id, "filename": filename, "columns": parsed["columns"],
            "row_count": len(parsed["rows"]), "sample": parsed["rows"][:5]}


@router.post("")
async def attach(file: UploadFile = File(...), user: CurrentUser = Depends(get_current_user)):
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)            # never read an unbounded body into memory
    name = (file.filename or "attachment")[-120:]
    try:
        parsed = parse_table(name, data)
    except AttachmentError as exc:
        log_event(AuditAction.FILE_ATTACHED, user_id=user.id, role=user.role.value, status="REJECTED", ip=user.ip,
                  metadata={"filename": name, "reason": str(exc)})
        raise HTTPException(422, str(exc))
    except Exception:  # noqa: BLE001
        log.exception("attachment parsing failed")
        raise HTTPException(500, "The file could not be processed.")
    att_id = attachments.save(user.id, name, parsed)
    log_event(AuditAction.FILE_ATTACHED, user_id=user.id, role=user.role.value, ip=user.ip,
              metadata={"filename": name, "rows": len(parsed["rows"]), "columns": parsed["columns"]})
    return {"success": True, **_describe(att_id, name, parsed),
            "message": f"{len(parsed['rows'])} rows attached."}


@router.get("/{attachment_id}")
def preview(attachment_id: str, user: CurrentUser = Depends(get_current_user)):
    att = attachments.load(user.id, attachment_id)
    if not att:
        raise HTTPException(404, "The attached file is no longer available. Please attach it again.")
    return {"success": True, "attachment_id": attachment_id, "filename": att["filename"],
            "columns": att["columns"], "rows": att["rows"], "row_count": len(att["rows"])}


@router.delete("/{attachment_id}")
def remove(attachment_id: str, user: CurrentUser = Depends(get_current_user)):
    ok = attachments.delete(user.id, attachment_id)
    if ok:
        log_event(AuditAction.FILE_REMOVED, user_id=user.id, role=user.role.value, ip=user.ip)
    return {"success": True, "removed": ok}
