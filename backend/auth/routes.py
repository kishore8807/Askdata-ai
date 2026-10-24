from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..audit.logger import log_event
from ..config import settings
from ..database import app_conn
from ..models import AuditAction, CurrentUser
from ..schemas import LoginRequest
from .authentication import (CSRF_COOKIE, SESSION_COOKIE, clear_failures, client_ip, create_session_token,
                             get_current_user, is_locked_out, record_failure, revoke_token, verify_password)
from .permissions import matrix_for_display

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    ip = client_ip(request)
    ident = body.username.strip()
    if is_locked_out(ident, ip):
        log_event(AuditAction.LOGIN_FAILED, status="LOCKED", ip=ip, metadata={"identity": ident[:100]})
        raise HTTPException(429, "Too many failed attempts. Please try again later.")

    with app_conn() as conn:
        row = conn.execute(
            "SELECT id, username, email, role, password_hash, is_active FROM users "
            "WHERE lower(username) = lower(%s) OR lower(email) = lower(%s)", (ident, ident)).fetchone()

    ok = verify_password(body.password, row["password_hash"] if row else None)
    if not ok or not row or not row["is_active"]:
        record_failure(ident, ip)
        log_event(AuditAction.LOGIN_FAILED, user_id=row["id"] if row else None, role=row["role"] if row else None,
                  status="FAILED", ip=ip, metadata={"identity": ident[:100]})
        raise HTTPException(401, "Invalid username or password.")

    clear_failures(ident, ip)
    token, csrf, exp = create_session_token(row["id"])
    max_age = settings.session_minutes * 60
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=settings.cookie_secure,
                        samesite="strict", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=settings.cookie_secure,
                        samesite="strict", path="/")
    log_event(AuditAction.LOGIN, user_id=row["id"], role=row["role"], ip=ip)
    return {"success": True, "expires_at": exp.isoformat(),
            "user": {"id": row["id"], "username": row["username"], "email": row["email"], "role": row["role"]}}


@router.post("/logout")
def logout(request: Request, response: Response, user: CurrentUser = Depends(get_current_user)):
    claims = request.state.token_claims
    revoke_token(claims["jti"], datetime.fromtimestamp(claims["exp"], tz=timezone.utc))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    log_event(AuditAction.LOGOUT, user_id=user.id, role=user.role.value, ip=user.ip)
    return {"success": True}


@router.get("/me")
def me(request: Request, user: CurrentUser = Depends(get_current_user)):
    claims = request.state.token_claims
    return {"success": True, "user": user.public(),
            "session_expires_at": datetime.fromtimestamp(claims["exp"], tz=timezone.utc).isoformat(),
            "permissions": matrix_for_display()[user.role.value]}


@router.get("/permissions")
def permissions(user: CurrentUser = Depends(get_current_user)):
    """The full RBAC matrix (informational — enforcement always happens server-side)."""
    return {"success": True, "matrix": matrix_for_display(), "your_role": user.role.value}
