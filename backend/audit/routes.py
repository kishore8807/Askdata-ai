from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth.authentication import require_roles
from ..models import CurrentUser, Role
from . import logger

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit-logs")
def audit_logs(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
               action: str | None = Query(default=None, max_length=50), user_id: int | None = None,
               user: CurrentUser = Depends(require_roles(Role.DBA))):
    rows, total = logger.list_logs(limit=limit, offset=offset, action=action, user_id=user_id)
    return {"success": True, "total": total, "logs": rows}
