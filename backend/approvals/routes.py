from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.authentication import get_current_user, require_roles
from ..models import CurrentUser, Role
from ..schemas import ApproveRequest, MarkReadRequest, RejectRequest
from . import service

router = APIRouter(prefix="/api", tags=["approvals"])


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except service.ApprovalError as exc:
        raise HTTPException(exc.status_code, exc.message)


@router.get("/approvals/pending")
def pending(user: CurrentUser = Depends(require_roles(Role.DBA))):
    rows = service.list_pending(user)
    return {"success": True, "count": len(rows), "requests": rows}


@router.get("/approvals/mine")
def mine(status: str | None = Query(default=None), user: CurrentUser = Depends(get_current_user)):
    """Requests created by the current user (DBA gets every request, any status)."""
    return {"success": True, "requests": service.list_requests(user, status=status)}


@router.post("/approvals/{request_id}/approve")
def approve(request_id: int, body: ApproveRequest | None = None, user: CurrentUser = Depends(require_roles(Role.DBA))):
    return _wrap(service.approve, request_id, user, confirm_dangerous=bool(body and body.confirm_dangerous))


@router.post("/approvals/{request_id}/reject")
def reject(request_id: int, body: RejectRequest, user: CurrentUser = Depends(require_roles(Role.DBA))):
    return _wrap(service.reject, request_id, user, body.reason.strip())


@router.get("/notifications")
def notifications(user: CurrentUser = Depends(get_current_user)):
    return {"success": True, **service.list_notifications(user.id)}


@router.post("/notifications/read")
def read(body: MarkReadRequest | None = None, user: CurrentUser = Depends(get_current_user)):
    service.mark_notifications_read(user.id, body.ids if body else None)
    return {"success": True}
