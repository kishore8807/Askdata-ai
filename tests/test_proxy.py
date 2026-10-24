"""Regression tests for the Flask front door (reverse proxy). Runs the real FastAPI app on a local port."""
import socket
import threading
import time

import pytest
import uvicorn


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def flask_client(app, monkeypatch_module):
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    import os
    os.environ["BACKEND_URL"] = f"http://127.0.0.1:{port}"
    import importlib, sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "frontend"))
    import app as flask_app_module
    importlib.reload(flask_app_module)
    flask_app_module.app.testing = True
    yield flask_app_module.app
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture(scope="module")
def monkeypatch_module():
    yield None


def _login(client, user, pw):
    return client.post("/api/auth/login", json={"username": user, "password": pw})


def test_proxy_does_not_leak_sessions_between_visitors(flask_client):
    from tests.conftest import PASSWORDS
    alice = flask_client.test_client()
    r = _login(alice, "dba", PASSWORDS["dba"])
    assert r.status_code == 200
    assert alice.get("/api/auth/me").status_code == 200

    stranger = flask_client.test_client()                       # a different browser, no cookies
    assert stranger.get("/api/auth/me").status_code == 401       # <-- was 200 before the fix
    assert stranger.get("/api/approvals/pending").status_code == 401
    assert stranger.get("/api/audit-logs").status_code == 401

    # a second user logging in must not inherit (or overwrite) the first user's identity
    bob = flask_client.test_client()
    assert _login(bob, "analyst", PASSWORDS["analyst"]).status_code == 200
    assert bob.get("/api/auth/me").get_json()["user"]["role"] == "ANALYST"
    assert alice.get("/api/auth/me").get_json()["user"]["role"] == "DBA"
    assert flask_client.test_client().get("/api/auth/me").status_code == 401


def test_proxy_passes_both_cookies_and_csrf_flow(flask_client):
    from tests.conftest import PASSWORDS
    c = flask_client.test_client()
    r = _login(c, "developer", PASSWORDS["developer"])
    cookies = r.headers.getlist("Set-Cookie")
    assert any(x.startswith("session=") and "HttpOnly" in x for x in cookies)
    assert any(x.startswith("csrf_token=") for x in cookies)
    csrf = c.get_cookie("csrf_token").value
    assert c.post("/api/query/validate", json={"sql": "SELECT 1"}).status_code == 403                # no CSRF header
    assert c.post("/api/query/validate", json={"sql": "SELECT 1"}, headers={"X-CSRF-Token": csrf}).status_code == 200


def test_proxy_serves_ui_with_security_headers(flask_client):
    r = flask_client.test_client().get("/")
    assert r.status_code == 200 and b"AskData Ai" in r.data
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_proxy_upload_size_limit(flask_client):
    from tests.conftest import PASSWORDS
    import io
    c = flask_client.test_client()
    _login(c, "dba", PASSWORDS["dba"])
    big = io.BytesIO(b"PK\x03\x04" + b"0" * (3 * 1024 * 1024))
    r = c.post("/api/employees/upload", data={"file": (big, "big.xlsx")}, headers={"X-CSRF-Token": c.get_cookie("csrf_token").value})
    assert r.status_code == 413 and "too large" in r.get_json()["message"]
