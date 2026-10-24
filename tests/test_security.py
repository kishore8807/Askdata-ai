import pytest


def run(c, sql, **kw): return c.post("/api/query/execute", json={"sql": sql, **kw})


INJECTIONS = [
    "SELECT 1 UNION SELECT password_hash FROM users",
    "SELECT * FROM employees WHERE id = 1; COPY employees TO PROGRAM 'rm -rf /'",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM pg_shadow",
    "SELECT current_setting('server_version')",
    "SELECT * FROM dblink('host=evil','select 1') AS t(x int)",
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity",
    "CREATE ROLE evil SUPERUSER LOGIN",
    "ALTER USER postgres PASSWORD 'x'",
    "GRANT ALL ON ALL TABLES IN SCHEMA public TO PUBLIC",
    "DROP DATABASE postgres",
    "DO $$ BEGIN EXECUTE 'DROP TABLE employees'; END $$",
    "SELECT * FROM employees e, users u",
    "SELECT (SELECT password_hash FROM users LIMIT 1)",
    "WITH u AS (SELECT * FROM users) SELECT * FROM u",
    "UPDATE employees SET salary = 0 WHERE id IN (SELECT id FROM users)",
]


@pytest.mark.parametrize("sql", INJECTIONS)
@pytest.mark.parametrize("who", ["dba", "dev", "analyst"])
def test_dangerous_sql_never_executes(sql, who, request, db):
    c = request.getfixturevalue(who)
    r = run(c, sql, confirm_dangerous=True)
    assert r.status_code in (403, 422), (who, sql, r.status_code, r.text)
    assert r.json()["success"] is False
    assert db.execute("SELECT count(*) AS c FROM employees").fetchone()["c"] >= 8
    assert "Traceback" not in r.text


def test_users_table_never_visible(dba, dev, analyst):
    for c in (dba, dev, analyst):
        assert run(c, "SELECT * FROM users").status_code in (403, 422)
        tables = [t["table"] for t in c.get("/api/schema").json()["tables"]]
        assert "users" not in tables and "revoked_tokens" not in tables and "notifications" not in tables
    assert "audit_logs" in [t["table"] for t in dba.get("/api/schema").json()["tables"]]
    assert "audit_logs" not in [t["table"] for t in dev.get("/api/schema").json()["tables"]]


def test_audit_log_is_tamper_proof_through_sql(dba, db):
    n = db.execute("SELECT count(*) AS c FROM audit_logs").fetchone()["c"]
    for sql in ("DELETE FROM audit_logs WHERE id > 0", "TRUNCATE audit_logs", "DROP TABLE audit_logs",
                "UPDATE audit_logs SET status='SUCCESS' WHERE id > 0"):
        assert run(dba, sql, confirm_dangerous=True).status_code in (403, 422)
    assert db.execute("SELECT count(*) AS c FROM audit_logs").fetchone()["c"] >= n


def test_permission_denied_is_audited(analyst, db):
    run(analyst, "DELETE FROM employees WHERE id = 1")
    assert db.execute("SELECT 1 FROM audit_logs WHERE action='PERMISSION_DENIED' AND role='ANALYST'").fetchone()


def test_audit_logs_contain_no_secrets(db):
    from tests.conftest import PASSWORDS
    blob = " ".join(str(r) for r in db.execute("SELECT * FROM audit_logs").fetchall())
    for pw in PASSWORDS.values():
        assert pw not in blob


def test_audit_api_filters(dba):
    j = dba.get("/api/audit-logs?action=LOGIN&limit=5").json()
    assert j["success"] and all(x["action"] == "LOGIN" for x in j["logs"])


def test_error_messages_hide_internals(dba):
    r = dba.get("/api/does-not-exist")
    assert r.status_code == 404
    r = dba.c.post("/api/query/execute", content=b"{not json", headers={"Content-Type": "application/json", "X-CSRF-Token": dba.c.cookies.get("csrf_token")})
    assert r.status_code == 422 and "Traceback" not in r.text


def test_oversized_sql_rejected(dba):
    from backend.config import settings
    object.__setattr__(settings, "max_sql_length", 20000)
    try:
        assert run(dba, "SELECT " + "1," * 20000 + "1").status_code == 422
    finally:
        object.__setattr__(settings, "max_sql_length", 1_000_000)


def test_validate_endpoint_executes_nothing(dev, db):
    r = dev.post("/api/query/validate", json={"sql": "DELETE FROM employees WHERE employee_code = 'EMP001'"}).json()
    assert r["decision"] == "APPROVAL" and r["requires_dba_approval"] is True
    assert db.execute("SELECT 1 FROM employees WHERE employee_code='EMP001'").fetchone()


def test_security_headers(anon):
    r = anon.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_login_lockout(app):
    from backend.config import Settings
    from backend.auth import authentication as a
    from tests.conftest import Client
    original = a.settings
    a.settings = Settings.__new__(Settings)
    for k in ("login_max_failures", "login_lockout_minutes"):
        object.__setattr__(a.settings, k, {"login_max_failures": 3, "login_lockout_minutes": 5}[k])
    a._failures.clear()
    try:
        c = Client(app)
        for _ in range(3):
            assert c.login("analyst", "bad-password-xx").status_code == 401
        assert c.login("analyst").status_code == 429               # even the right password is blocked now
    finally:
        a.settings = original
        a._failures.clear()


def test_stacked_destructive_statements_are_checked_per_role(analyst, dev, dba, db):
    sql = "SELECT * FROM employees; DROP TABLE employees;--"
    assert run(analyst, sql).status_code == 403
    r = run(dev, sql)
    assert r.status_code == 202 and r.json()["approval_required"]            # nothing runs without a DBA
    r = run(dba, sql)
    assert r.status_code == 409 and r.json()["requires_dangerous_confirmation"]
    assert db.execute("SELECT to_regclass('employees') AS t").fetchone()["t"] is not None
    sql2 = "SELECT 1; DELETE FROM employees"
    assert run(analyst, sql2).status_code == 403
    assert run(dev, sql2).status_code in (403, 422)
    assert run(dba, sql2).status_code == 409
    assert db.execute("SELECT count(*) AS c FROM employees").fetchone()["c"] >= 8
