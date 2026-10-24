"""Execution of every SQL category on real PostgreSQL, through the HTTP API."""
from backend.ai import slm


def run(client, sql, **kw):
    return client.post("/api/query/execute", json={"sql": sql, **kw})


def test_select_family(analyst):
    for sql in ("SELECT * FROM employees ORDER BY salary DESC",
                "SELECT e.name, d.department_name FROM employees e JOIN departments d ON e.department_id = d.id",
                "SELECT department, COUNT(*) AS n FROM employees GROUP BY department HAVING COUNT(*) > 1 ORDER BY n DESC",
                "WITH t AS (SELECT * FROM employees WHERE status='Active') SELECT count(*) FROM t",
                "SELECT * FROM employees WHERE name ILIKE '%ku%' LIMIT 5"):
        r = run(analyst, sql)
        assert r.status_code == 200, (sql, r.text)
        assert r.json()["columns"] and r.json()["rows"]


def test_select_returns_real_data(analyst):
    j = run(analyst, "SELECT employee_code, salary FROM employees WHERE employee_code = 'EMP002'").json()
    assert j["rows"] == [["EMP002", 45000.0]]


def test_analyst_cannot_write(analyst, db):
    for sql in ("INSERT INTO departments (department_name) VALUES ('Hack')",
                "UPDATE employees SET salary = 1 WHERE employee_code = 'EMP001'",
                "DELETE FROM employees WHERE employee_code = 'EMP001'",
                "CREATE TABLE x (id int)", "DROP TABLE tasks", "TRUNCATE tasks",
                "BEGIN; DELETE FROM tasks WHERE id = 1; COMMIT;"):
        r = run(analyst, sql)
        assert r.status_code == 403 and r.json()["message"] == "You do not have permission to perform this operation."
    assert db.execute("SELECT count(*) AS c FROM departments WHERE department_name='Hack'").fetchone()["c"] == 0


def test_read_only_requests_cannot_write_even_if_validator_is_bypassed(db):
    """Defence in depth: a 'read-only' execution is refused by PostgreSQL itself (cursor + READ ONLY tx)."""
    import pytest
    from backend.database.postgres import ExecutionError, execute_validated
    from backend.database.sql_validator import ValidationResult
    from backend.models import Op
    for stmt in ("INSERT INTO departments (department_name) VALUES ('Sneaky')",
                 "WITH i AS (INSERT INTO departments (department_name) VALUES ('Sneaky') RETURNING *) SELECT * FROM i"):
        v = ValidationResult(valid=True, statements=[stmt], operation=Op.SELECT, operations=[Op.SELECT], read_only=True)
        with pytest.raises(ExecutionError):
            execute_validated(v)
    assert db.execute("SELECT count(*) AS c FROM departments WHERE department_name='Sneaky'").fetchone()["c"] == 0


def test_dba_crud_and_ddl_cycle(dba, db):
    assert run(dba, "INSERT INTO departments (department_name, description) VALUES ('Legal','Legal team')").status_code == 200
    assert db.execute("SELECT 1 FROM departments WHERE department_name='Legal'").fetchone()
    r = run(dba, "UPDATE departments SET description='Legal & Compliance' WHERE department_name='Legal'")
    assert r.status_code == 200 and "1 row" in r.json()["message"]
    assert db.execute("SELECT description FROM departments WHERE department_name='Legal'").fetchone()["description"] == "Legal & Compliance"
    assert run(dba, "DELETE FROM departments WHERE department_name='Legal'").status_code == 200
    assert not db.execute("SELECT 1 FROM departments WHERE department_name='Legal'").fetchone()

    assert run(dba, "CREATE TABLE scratch (id serial PRIMARY KEY, label text)").status_code == 200
    assert run(dba, "ALTER TABLE scratch ADD COLUMN phone VARCHAR(20)").status_code == 200
    cols = [r["column_name"] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='scratch'").fetchall()]
    assert "phone" in cols
    # dangerous ops need explicit confirmation
    r = run(dba, "DROP TABLE scratch")
    assert r.status_code == 409 and r.json()["requires_dangerous_confirmation"]
    assert db.execute("SELECT to_regclass('scratch') AS t").fetchone()["t"] is not None
    assert run(dba, "DROP TABLE scratch", confirm_dangerous=True).status_code == 200
    assert db.execute("SELECT to_regclass('scratch') AS t").fetchone()["t"] is None


def test_dba_truncate_requires_confirmation(dba, db):
    run(dba, "CREATE TABLE trunc_t (id int)"); run(dba, "INSERT INTO trunc_t VALUES (1),(2)")
    assert run(dba, "TRUNCATE TABLE trunc_t").status_code == 409
    assert db.execute("SELECT count(*) AS c FROM trunc_t").fetchone()["c"] == 2
    assert run(dba, "TRUNCATE TABLE trunc_t", confirm_dangerous=True).status_code == 200
    assert db.execute("SELECT count(*) AS c FROM trunc_t").fetchone()["c"] == 0
    run(dba, "DROP TABLE trunc_t", confirm_dangerous=True)


def test_full_table_delete_needs_dba_confirmation(dba, db):
    run(dba, "CREATE TABLE fulldel (id int)"); run(dba, "INSERT INTO fulldel VALUES (1),(2)")
    r = run(dba, "DELETE FROM fulldel")
    assert r.status_code == 409 and db.execute("SELECT count(*) AS c FROM fulldel").fetchone()["c"] == 2
    assert run(dba, "DELETE FROM fulldel", confirm_dangerous=True).status_code == 200
    run(dba, "DROP TABLE fulldel", confirm_dangerous=True)


def test_transaction_commit_and_rollback(dba, db):
    sql = "BEGIN; UPDATE employees SET salary = salary + 5000 WHERE department = 'HR'; COMMIT;"
    before = db.execute("SELECT salary FROM employees WHERE employee_code='EMP002'").fetchone()["salary"]
    assert run(dba, sql).status_code == 200
    after = db.execute("SELECT salary FROM employees WHERE employee_code='EMP002'").fetchone()["salary"]
    assert after == before + 5000
    assert run(dba, "BEGIN; UPDATE employees SET salary = salary - 5000 WHERE department = 'HR'; ROLLBACK;").status_code == 200
    assert db.execute("SELECT salary FROM employees WHERE employee_code='EMP002'").fetchone()["salary"] == after
    run(dba, "UPDATE employees SET salary = 45000 WHERE employee_code = 'EMP002'")


def test_failed_transaction_rolls_back_everything(dba, db):
    sql = ("BEGIN; UPDATE employees SET salary = 1 WHERE employee_code = 'EMP003'; "
           "INSERT INTO employees (employee_code) VALUES ('EMP003'); COMMIT;")
    r = run(dba, sql)
    assert r.status_code == 400 and "message" in r.json()
    assert db.execute("SELECT salary FROM employees WHERE employee_code='EMP003'").fetchone()["salary"] == 48000


def test_database_errors_are_friendly(dba):
    r = run(dba, "SELECT * FROM no_such_table")
    assert r.status_code == 400
    assert "Traceback" not in r.text and "psycopg" not in r.text
    r = run(dba, "INSERT INTO employees (employee_code, name, email, department, designation, salary, joining_date, status) "
                 "VALUES ('EMP001','Dup','kumar@example.com','HR','X',1,'2020-01-01','Active')")
    assert r.status_code == 400 and "already exists" in r.json()["message"]


def test_result_cap_uses_server_side_cursor(dba):
    from backend.config import settings
    object.__setattr__(settings, "max_result_rows", 3)
    try:
        j = run(dba, "SELECT * FROM employees ORDER BY id").json()
        huge = run(dba, "SELECT g FROM generate_series(1, 50000000) AS g").json()   # would exhaust memory client-side
    finally:
        object.__setattr__(settings, "max_result_rows", 1000)
    assert len(j["rows"]) == 3 and j["truncated"] is True
    assert len(huge["rows"]) == 3 and huge["truncated"] is True


def test_audit_trail_for_execution(dba, db):
    run(dba, "SELECT 1 AS x")
    run(dba, "SELECT * FROM users")                      # rejected
    acts = {r["action"] for r in db.execute("SELECT action FROM audit_logs").fetchall()}
    assert {"LOGIN", "SQL_EXECUTION_REQUESTED", "SQL_EXECUTED", "SQL_REJECTED"} <= acts
    row = db.execute("SELECT * FROM audit_logs WHERE action='SQL_EXECUTED' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["role"] == "DBA" and row["sql_query"] and row["ip_address"]


def test_generate_without_ollama_is_clean_503(dev):
    r = dev.post("/api/query/generate", json={"question": "Show all employees"})
    assert r.status_code == 503 and r.json()["message"] == "AI service is unavailable. Please start Ollama."


def test_generate_pipeline_with_stubbed_model(dev, analyst, monkeypatch):
    """Model output is stubbed here only because no Ollama runs in CI; everything else is real."""
    prompts = {}

    def fake(system, user):
        prompts["system"], prompts["user"] = system, user
        return "```sql\nUPDATE employees SET salary = 60000 WHERE employee_code = 'EMP001'\n```"
    monkeypatch.setattr(slm, "generate", fake)
    j = dev.post("/api/query/generate", json={"question": "Change Kumar salary to 60000"}).json()
    assert j["sql"].startswith("UPDATE employees") and j["operation_type"] == "UPDATE"
    assert j["requires_dba_approval"] is True and j["requires_confirmation"] is True
    assert "TABLE employees" in prompts["user"] and "users" not in prompts["user"].lower().replace("requester", "")
    assert "Never bypass database permissions" in prompts["system"]
    j2 = analyst.post("/api/query/generate", json={"question": "Change Kumar salary to 60000"}).json()
    assert j2["permission_denied"] is True and j2["valid"] is False

    monkeypatch.setattr(slm, "generate", lambda s, u: "CLARIFY: Which employee?")
    j3 = dev.post("/api/query/generate", json={"question": "Change the salary"}).json()
    assert j3["needs_clarification"] and j3["sql"] is None

    monkeypatch.setattr(slm, "generate", lambda s, u: "DROP TABLE employees; DROP TABLE tasks;")
    j4 = dev.post("/api/query/generate", json={"question": "clean everything up please"}).json()
    assert j4["valid"] is True and j4["requires_dba_approval"] is True and j4["dangerous"] is True   # never auto-runs
