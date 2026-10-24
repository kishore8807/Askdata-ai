"""Database-level enforcement (independent of the SQL validator). Runs when TEST_EXEC_USER is set."""
import os

import psycopg
import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_EXEC_USER"), reason="set TEST_EXEC_USER/TEST_EXEC_PASSWORD to run")


def conn_as_exec():
    from backend.config import settings
    return psycopg.connect(settings.exec_conninfo, autocommit=True)


@pytest.mark.parametrize("stmt", [
    "SELECT * FROM users", "SELECT password_hash FROM users", "UPDATE users SET role='DBA'",
    "SELECT * FROM revoked_tokens", "SELECT * FROM notifications", "SELECT * FROM import_staging",
    "DELETE FROM audit_logs", "UPDATE audit_logs SET status='SUCCESS'", "TRUNCATE audit_logs", "DROP TABLE audit_logs",
    "INSERT INTO audit_logs (action) VALUES ('FORGED')", "UPDATE approval_requests SET status='APPROVED'",
    "DROP TABLE approval_requests", "CREATE ROLE evil SUPERUSER", "ALTER ROLE postgres PASSWORD 'x'",
])
def test_postgres_itself_refuses(stmt):
    with conn_as_exec() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(stmt)


def test_exec_role_can_do_its_job():
    with conn_as_exec() as c:
        assert c.execute("SELECT count(*) FROM employees").fetchone()[0] >= 8
        assert c.execute("SELECT count(*) FROM audit_logs").fetchone()[0] >= 0      # read-only access to audit data
        c.execute("CREATE TABLE lp_probe (id int)")
        c.execute("DROP TABLE lp_probe")
