"""Password hashing, JWT sessions (httpOnly cookie), CSRF double-submit, login throttling, auth dependencies."""
from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from ..config import settings
from ..database import app_conn
from ..models import CurrentUser, Role

SESSION_COOKIE = "session"
CSRF_COOKIE = "csrf_token"
ALGO = "HS256"
MIN_PASSWORD_LEN = 10
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt(rounds=12))


# ───────── passwords ─────────
def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    """Always performs one bcrypt comparison, even for unknown users (timing-safe)."""
    target = password_hash.encode() if password_hash else _DUMMY_HASH
    try:
        ok = bcrypt.checkpw(password.encode()[:72], target)
    except ValueError:
        return False
    return ok and password_hash is not None


# ───────── login throttling (in-memory, per process) ─────────
_failures: dict[str, list[float]] = defaultdict(list)


def _key(identity: str, ip: str | None) -> str:
    return f"{identity.lower()}|{ip or '-'}"


def is_locked_out(identity: str, ip: str | None) -> bool:
    window = settings.login_lockout_minutes * 60
    now = time.time()
    k = _key(identity, ip)
    _failures[k] = [t for t in _failures[k] if now - t < window]
    return len(_failures[k]) >= settings.login_max_failures


def record_failure(identity: str, ip: str | None) -> None:
    _failures[_key(identity, ip)].append(time.time())


def clear_failures(identity: str, ip: str | None) -> None:
    _failures.pop(_key(identity, ip), None)


# ───────── tokens ─────────
def create_session_token(user_id: int) -> tuple[str, str, datetime]:
    """Returns (jwt, csrf_token, expires_at). The CSRF token is signed inside the JWT so it cannot be forged."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.session_minutes)
    csrf = secrets.token_urlsafe(32)
    payload = {"sub": str(user_id), "jti": secrets.token_hex(16), "iat": now, "exp": exp, "csrf": csrf}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGO), csrf, exp


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[ALGO], options={"require": ["exp", "sub", "jti"]})


def revoke_token(jti: str, expires_at: datetime) -> None:
    with app_conn() as conn:
        conn.execute("INSERT INTO revoked_tokens (jti, expires_at) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (jti, expires_at))
        conn.execute("DELETE FROM revoked_tokens WHERE expires_at < NOW()")


# ───────── request helpers ─────────
def client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    fwd = request.headers.get("x-forwarded-for")
    if fwd and (peer in settings.trusted_proxies or "*" in settings.trusted_proxies):   # only from our own proxy
        return fwd.split(",")[0].strip()[:64]
    return peer


def _unauth(msg: str = "Authentication required.") -> HTTPException:
    return HTTPException(status_code=401, detail=msg)


def get_current_user(request: Request) -> CurrentUser:
    """Authoritative identity. The role is ALWAYS read from the database — never from the token or client."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauth()
    try:
        claims = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise _unauth("Your session has expired. Please sign in again.")
    except jwt.PyJWTError:
        raise _unauth()

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        header = request.headers.get("x-csrf-token", "")
        if not header or not hmac.compare_digest(header, str(claims.get("csrf", ""))):
            raise HTTPException(status_code=403, detail="CSRF validation failed.")

    with app_conn() as conn:
        if conn.execute("SELECT 1 FROM revoked_tokens WHERE jti = %s", (claims["jti"],)).fetchone():
            raise _unauth("Your session has ended. Please sign in again.")
        row = conn.execute(
            "SELECT id, username, email, role, is_active FROM users WHERE id = %s", (int(claims["sub"]),)
        ).fetchone()
    if not row or not row["is_active"]:
        raise _unauth()
    user = CurrentUser(id=row["id"], username=row["username"], email=row["email"],
                       role=Role(row["role"]), ip=client_ip(request))
    request.state.token_claims = claims
    return user


def require_roles(*roles: Role):
    def dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            from ..audit.logger import log_event
            from ..models import AuditAction
            log_event(AuditAction.PERMISSION_DENIED, user_id=user.id, role=user.role.value, status="DENIED",
                      ip=user.ip, metadata={"reason": "role not allowed for endpoint"})
            raise HTTPException(status_code=403, detail="You do not have permission to perform this operation.")
        return user
    return dep
