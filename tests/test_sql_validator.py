import pytest
from backend.database.sql_validator import validate_sql
from backend.models import Decision, Op, Role

D, V, A = Role.DBA, Role.DEVELOPER, Role.ANALYST


@pytest.mark.parametrize("sql,role,decision,op", [
    ("SELECT * FROM employees", A, Decision.ALLOW, Op.SELECT),
    ("SELECT e.name, d.department_name FROM employees e JOIN departments d ON e.department_id = d.id", A, Decision.ALLOW, Op.SELECT),
    ("SELECT department, COUNT(*) FROM employees GROUP BY department HAVING COUNT(*) > 1 ORDER BY 2 DESC LIMIT 5", A, Decision.ALLOW, Op.SELECT),
    ("WITH x AS (SELECT * FROM employees) SELECT count(*) FROM x", A, Decision.ALLOW, Op.SELECT),
    ("SELECT * FROM employees WHERE salary > (SELECT avg(salary) FROM employees)", A, Decision.ALLOW, Op.SELECT),
    ("INSERT INTO departments (department_name) VALUES ('X')", V, Decision.ALLOW, Op.INSERT),
    ("INSERT INTO departments (department_name) VALUES ('X')", A, Decision.DENY, Op.INSERT),
    ("UPDATE employees SET salary = 60000 WHERE employee_code = 'EMP001'", D, Decision.ALLOW, Op.UPDATE),
    ("UPDATE employees SET salary = 60000 WHERE employee_code = 'EMP001'", V, Decision.APPROVAL, Op.UPDATE),
    ("UPDATE employees SET salary = 60000 WHERE employee_code = 'EMP001'", A, Decision.DENY, Op.UPDATE),
    ("DELETE FROM employees WHERE employee_code = 'EMP001'", V, Decision.APPROVAL, Op.DELETE),
    ("DELETE FROM employees WHERE employee_code = 'EMP001'", A, Decision.DENY, Op.DELETE),
    ("CREATE TABLE t1 (id int)", D, Decision.ALLOW, Op.CREATE),
    ("CREATE TABLE t1 (id int)", V, Decision.ALLOW, Op.CREATE),
    ("CREATE TABLE t1 (id int)", A, Decision.DENY, Op.CREATE),
    ("ALTER TABLE employees ADD COLUMN phone VARCHAR(20)", V, Decision.ALLOW, Op.ALTER),
    ("ALTER TABLE employees DROP COLUMN phone", V, Decision.APPROVAL, Op.ALTER),
    ("ALTER TABLE employees ADD COLUMN phone VARCHAR(20)", A, Decision.DENY, Op.ALTER),
    ("DROP TABLE projects", D, Decision.ALLOW, Op.DROP),
    ("DROP TABLE projects", V, Decision.APPROVAL, Op.DROP),
    ("DROP TABLE projects", A, Decision.DENY, Op.DROP),
    ("TRUNCATE TABLE tasks", D, Decision.ALLOW, Op.TRUNCATE),
    ("TRUNCATE TABLE tasks", V, Decision.APPROVAL, Op.TRUNCATE),
    ("TRUNCATE TABLE tasks", A, Decision.DENY, Op.TRUNCATE),
    ("BEGIN; UPDATE employees SET salary = salary + 5000 WHERE department_id = 2; COMMIT;", D, Decision.ALLOW, Op.TRANSACTION),
    ("BEGIN; UPDATE employees SET salary = salary + 5000 WHERE department_id = 2; COMMIT;", V, Decision.APPROVAL, Op.TRANSACTION),
    ("BEGIN; UPDATE employees SET salary = salary + 5000 WHERE department_id = 2; COMMIT;", A, Decision.DENY, Op.TRANSACTION),
    ("BEGIN; INSERT INTO departments (department_name) VALUES ('Z'); COMMIT;", V, Decision.ALLOW, Op.TRANSACTION),
])
def test_matrix(sql, role, decision, op):
    r = validate_sql(sql, role)
    assert r.decision == decision, r.errors
    assert r.operation == op
    assert r.valid == (decision != Decision.DENY)


@pytest.mark.parametrize("sql,why", [
    ("BEGIN; SELECT 1;", "complete BEGIN"),
    ("COMMIT", "complete BEGIN"),
    ("SELECT 1; COMMIT", "complete BEGIN"),
    ("BEGIN; BEGIN; SELECT 1; COMMIT;", "nested"),
    ("SELECT 1; COPY employees TO '/tmp/x'", "not supported"),
    ("SELECT 1; SELECT * FROM users", "not available"),
    ("COPY employees TO '/tmp/x'", "not supported"),
    ("GRANT ALL ON employees TO PUBLIC", "not supported"),
    ("SET statement_timeout = 0", "not supported"),
    ("DO $$ BEGIN PERFORM 1; END $$", "not supported"),
    ("CREATE EXTENSION dblink", "not supported"),
    ("CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql", "not supported"),
    ("DROP DATABASE ai_database", "not permitted"),
    ("DROP SCHEMA public CASCADE", "not permitted"),
    ("ALTER TABLE employees OWNER TO postgres", "not supported"),
    ("SELECT pg_sleep(30)", "not allowed"),
    ("SELECT lo_import('/etc/passwd')", "not allowed"),
    ("SELECT set_config('statement_timeout','0',false)", "not allowed"),
    ("SELECT * FROM pg_catalog.pg_shadow", "system catalogs"),
    ("SELECT * FROM information_schema.tables", "system catalogs"),
    ("SELECT * FROM pg_user", "system catalogs"),
    ("SELECT * FROM secret.data", "schemas"),
    ("SELECT * FROM users", "not available"),
    ("SELECT password_hash FROM users", "not available"),
    ("SELECT * FROM employees FOR UPDATE", "locking"),
    ("", "empty"),
    ("SELEC * FORM employees", ""),
])
def test_dangerous_and_invalid_rejected_for_everyone(sql, why):
    for role in (D, V, A):
        r = validate_sql(sql, role)
        assert not r.valid, sql
        assert r.errors and why.lower() in r.errors[0].lower() or why == ""


def test_hidden_and_protected_tables():
    assert not validate_sql("SELECT * FROM audit_logs", V).valid
    assert not validate_sql("SELECT * FROM approval_requests", A).valid
    assert validate_sql("SELECT * FROM audit_logs", D).valid                     # DBA may read
    assert not validate_sql("DELETE FROM audit_logs WHERE id = 1", D).valid      # nobody modifies
    assert not validate_sql("UPDATE approval_requests SET status='APPROVED' WHERE id = 1", D).valid
    assert not validate_sql("DROP TABLE audit_logs", D).valid
    assert not validate_sql("TRUNCATE audit_logs", D).valid
    assert not validate_sql("CREATE TABLE users (id int)", D).valid


def test_cte_cannot_hide_a_write_from_analyst():
    r = validate_sql("WITH d AS (DELETE FROM employees WHERE id = 1 RETURNING *) SELECT * FROM d", A)
    assert not r.valid and r.operation in (Op.DELETE, Op.SELECT)
    r = validate_sql("WITH d AS (DELETE FROM employees WHERE id = 1 RETURNING *) SELECT * FROM d", V)
    assert r.decision == Decision.APPROVAL                    # developer still needs the DBA
    r = validate_sql("SELECT * INTO copy_t FROM employees", A)
    assert not r.valid


def test_unsafe_where_rules():
    for sql in ("UPDATE employees SET salary = 1", "DELETE FROM employees", "UPDATE employees SET salary = 1 WHERE 1=1",
                "DELETE FROM employees WHERE true", "DELETE FROM employees WHERE id = 1 OR 1 = 1",
                "DELETE FROM employees WHERE 'a' = 'a'"):
        dev = validate_sql(sql, V)
        assert not dev.valid and dev.decision == Decision.DENY, sql      # developers: never, not even via approval
        dba = validate_sql(sql, D)
        assert dba.valid and dba.dangerous, sql                          # DBA: allowed but flagged
    assert not validate_sql("DELETE FROM employees WHERE employee_code = 'EMP001'", D).dangerous


def test_dangerous_flags():
    for sql in ("DROP TABLE projects", "TRUNCATE TABLE tasks", "ALTER TABLE employees DROP COLUMN phone"):
        assert validate_sql(sql, D).dangerous


def test_injection_strings_are_data_not_code():
    r = validate_sql("SELECT * FROM employees WHERE name = 'x''; DROP TABLE employees; --'", A)
    assert r.valid and len(r.statements) == 1 and r.operation == Op.SELECT
    r = validate_sql("SELECT * FROM employees WHERE name = 'x' -- ; DROP TABLE employees", A)
    assert r.valid and "DROP" not in r.sql.upper()


def test_normalised_sql_is_what_runs():
    r = validate_sql("select  *  from employees /* hide */ where salary>1 -- c", A)
    assert "/*" not in r.sql and "--" not in r.sql


def test_transaction_rollback_block():
    r = validate_sql("BEGIN; DELETE FROM tasks WHERE id = 1; ROLLBACK;", D)
    assert r.valid and r.is_transaction and r.ends_with_rollback


def test_matrix_is_configurable(tmp_path, monkeypatch):
    import json
    from backend.auth import permissions
    f = tmp_path / "perm.json"
    f.write_text(json.dumps({"DEVELOPER": {"DELETE": "DENY"}, "ANALYST": {"INSERT": "ALLOW"}}))
    monkeypatch.setattr(permissions.settings.__class__, "permissions_file", property(lambda s: str(f)), raising=False) \
        if False else None
    from backend.config import Settings
    monkeypatch.setattr(permissions, "settings", Settings.__new__(Settings))
    object.__setattr__(permissions.settings, "permissions_file", str(f))
    permissions.reset_matrix()
    try:
        assert validate_sql("DELETE FROM tasks WHERE id = 1", V).decision == Decision.DENY
        assert validate_sql("INSERT INTO departments (department_name) VALUES ('Q')", A).decision == Decision.DENY  # analyst invariant
    finally:
        monkeypatch.undo()
        permissions.reset_matrix()


def test_script_every_statement_is_validated_strictest_wins():
    sql = "SELECT * FROM employees; UPDATE employees SET salary = 1 WHERE employee_code = 'EMP001'; SELECT 2"
    r = validate_sql(sql, A)
    assert not r.valid and r.permission_denied and r.is_script
    r = validate_sql(sql, V)
    assert r.valid and r.decision == Decision.APPROVAL and r.is_script and len(r.statements) == 3
    r = validate_sql(sql, D)
    assert r.valid and r.decision == Decision.ALLOW and r.operation == Op.UPDATE
    r = validate_sql("SELECT 1; SELECT 2; SELECT 3", A)
    assert r.valid and r.read_only and r.is_script and len(r.statements) == 3


def test_stacked_destructive_statement_is_never_a_free_pass():
    sql = "SELECT * FROM employees; DROP TABLE employees"
    assert validate_sql(sql, A).decision == Decision.DENY
    assert validate_sql(sql, V).decision == Decision.APPROVAL and validate_sql(sql, V).dangerous
    r = validate_sql(sql, D)
    assert r.valid and r.dangerous                                        # DBA must still confirm
    r = validate_sql("SELECT 1; DELETE FROM employees", V)
    assert not r.valid                                                    # developer: no full-table write, ever


def test_statement_limit(monkeypatch):
    from backend.config import settings
    object.__setattr__(settings, "max_statements", 3)
    try:
        assert validate_sql("SELECT 1; SELECT 2; SELECT 3", A).valid
        assert not validate_sql("SELECT 1; SELECT 2; SELECT 3; SELECT 4", A).valid
    finally:
        object.__setattr__(settings, "max_statements", 500)
