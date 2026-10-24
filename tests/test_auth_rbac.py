import time
import jwt
from backend.config import settings
from tests.conftest import PASSWORDS


def test_valid_login_sets_secure_cookie(anon):
    r = anon.login("dba")
    assert r.status_code == 200 and r.json()["user"]["role"] == "DBA"
    assert "password" not in r.text.lower()
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc


def test_login_by_email(anon):
    assert anon.c.post("/api/auth/login", json={"username": "dba@company.local", "password": PASSWORDS["dba"]}).status_code == 200


def test_invalid_password(anon):
    r = anon.login("dba", "wrong-password-123")
    assert r.status_code == 401 and r.json()["message"] == "Invalid username or password."


def test_unknown_user_same_message(anon):
    r = anon.login("nobody", "whatever-123456")
    assert r.status_code == 401 and r.json()["message"] == "Invalid username or password."


def test_me_and_logout_revokes_token(dba):
    assert dba.get("/api/auth/me").json()["user"]["username"] == "dba"
    old_cookie = dba.c.cookies.get("session")
    assert dba.post("/api/auth/logout").status_code == 200
    dba.c.cookies.set("session", old_cookie)           # replay the old token
    assert dba.get("/api/auth/me").status_code == 401


def test_requires_auth(anon):
    for url in ("/api/auth/me", "/api/approvals/pending", "/api/audit-logs", "/api/schema", "/api/employees"):
        assert anon.get(url).status_code == 401
    assert anon.post("/api/query/execute", json={"sql": "SELECT 1"}).status_code == 401


def test_expired_session(anon):
    token = jwt.encode({"sub": "1", "jti": "x", "exp": int(time.time()) - 5, "csrf": "c"}, settings.secret_key, "HS256")
    anon.c.cookies.set("session", token)
    r = anon.get("/api/auth/me")
    assert r.status_code == 401 and "expired" in r.json()["message"].lower()


def test_forged_token_rejected(anon):
    token = jwt.encode({"sub": "1", "jti": "x", "exp": int(time.time()) + 500, "csrf": "c", "role": "DBA"}, "wrong-secret-wrong-secret-wrong-secret!!", "HS256")
    anon.c.cookies.set("session", token)
    assert anon.get("/api/auth/me").status_code == 401


def test_forged_role_claim_ignored(dev):
    """A token that *claims* DBA still gets the role stored in the database."""
    claims = jwt.decode(dev.c.cookies.get("session"), settings.secret_key, algorithms=["HS256"])
    claims["role"] = "DBA"
    forged = jwt.encode(claims, settings.secret_key, "HS256")
    dev.c.cookies.set("session", forged)
    assert dev.get("/api/auth/me").json()["user"]["role"] == "DEVELOPER"
    assert dev.get("/api/approvals/pending").status_code == 403


def test_csrf_required_on_post(dba):
    r = dba.c.post("/api/query/validate", json={"sql": "SELECT 1"})   # no header
    assert r.status_code == 403
    r = dba.c.post("/api/query/validate", json={"sql": "SELECT 1"}, headers={"X-CSRF-Token": "bad"})
    assert r.status_code == 403


def test_password_never_stored_plain(db):
    for row in db.execute("SELECT password_hash FROM users").fetchall():
        assert row["password_hash"].startswith("$2") and row["password_hash"] not in PASSWORDS.values()


def test_role_endpoints(dev, analyst, dba):
    assert dev.get("/api/audit-logs").status_code == 403
    assert analyst.get("/api/audit-logs").status_code == 403
    assert dba.get("/api/audit-logs").status_code == 200
    assert analyst.get("/api/approvals/pending").status_code == 403
