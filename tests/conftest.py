"""Test fixtures. Requires a reachable PostgreSQL (see README → Testing).

Environment (defaults suit the docker-compose database):  TEST_POSTGRES_* variables.
The test database is wiped and re-created from database/schema.sql + seed.sql on every session.
"""
import io
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.update({
    "POSTGRES_HOST": os.getenv("TEST_POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.getenv("TEST_POSTGRES_PORT", "5432"),
    "POSTGRES_DB": os.getenv("TEST_POSTGRES_DB", "ai_database_test"),
    "POSTGRES_USER": os.getenv("TEST_POSTGRES_USER", "postgres"),
    "POSTGRES_PASSWORD": os.getenv("TEST_POSTGRES_PASSWORD", "testpw"),
    "SECRET_KEY": "test-secret-key-test-secret-key-test-secret-key",
    "SESSION_EXPIRE_MINUTES": "60",
    "LOGIN_MAX_FAILURES": "50",
    "OLLAMA_BASE_URL": "http://127.0.0.1:9",   # closed port → "Ollama down" path is real
    "MAX_UPLOAD_MB": "1",
})
if os.getenv("TEST_EXEC_USER"):
    os.environ["POSTGRES_EXEC_USER"] = os.environ["TEST_EXEC_USER"]
    os.environ["POSTGRES_EXEC_PASSWORD"] = os.environ["TEST_EXEC_PASSWORD"]


import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PASSWORDS = {"dba": "DbaPassw0rd!x", "developer": "DevPassw0rd!x", "analyst": "AnaPassw0rd!x"}


def _reset_db():
    from backend.config import settings
    from backend.scripts.init_db import apply_schema, apply_least_privilege
    with psycopg.connect(settings.conninfo, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        apply_schema(conn, seed=True)
        if settings.exec_user and settings.exec_password:
            apply_least_privilege(conn)


@pytest.fixture(scope="session")
def app():
    _reset_db()
    from backend.auth.authentication import hash_password
    from backend.config import settings
    with psycopg.connect(settings.conninfo, autocommit=True) as conn:
        for name, role in (("dba", "DBA"), ("developer", "DEVELOPER"), ("analyst", "ANALYST")):
            conn.execute("INSERT INTO users (username,email,password_hash,role) VALUES (%s,%s,%s,%s)",
                         (name, f"{name}@company.local", hash_password(PASSWORDS[name]), role))
    from backend.main import app as fastapi_app
    with TestClient(fastapi_app) as _:
        yield fastapi_app


class Client:
    """TestClient wrapper that applies the CSRF header like the browser UI does."""

    def __init__(self, app):
        self.c = TestClient(app)

    def login(self, who, password=None):
        r = self.c.post("/api/auth/login", json={"username": who, "password": password or PASSWORDS[who]})
        return r

    def _h(self):
        t = self.c.cookies.get("csrf_token")
        return {"X-CSRF-Token": t} if t else {}

    def get(self, url, **kw): return self.c.get(url, **kw)
    def post(self, url, **kw): return self.c.post(url, headers={**self._h(), **kw.pop("headers", {})}, **kw)


def make(app, who):
    c = Client(app)
    assert c.login(who).status_code == 200
    return c


@pytest.fixture()
def dba(app): return make(app, "dba")
@pytest.fixture()
def dev(app): return make(app, "developer")
@pytest.fixture()
def analyst(app): return make(app, "analyst")
@pytest.fixture()
def anon(app): return Client(app)


@pytest.fixture()
def db():
    from backend.config import settings
    with psycopg.connect(settings.conninfo, autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
        yield conn


def xlsx_bytes(rows, columns=None):
    import pandas as pd
    cols = columns or ["employee_code", "name", "email", "department", "designation", "salary", "joining_date", "status"]
    buf = io.BytesIO()
    pd.DataFrame(rows, columns=cols).to_excel(buf, index=False)
    return buf.getvalue()
