-- Least-privilege role for executing user/AI SQL.   Applied by:  python -m backend.scripts.init_db --least-privilege
-- (the role itself, with its password, is created by that script from POSTGRES_EXEC_USER / POSTGRES_EXEC_PASSWORD)
--
-- Model
--   * business tables (employees, departments, projects, tasks) are OWNED by the exec role, so a DBA can ALTER / DROP
--     them and new tables created through the SQL console belong to it as well;
--   * credential / internal tables (users, revoked_tokens, notifications, import_staging) are invisible to it;
--   * audit_logs and approval_requests are READ-ONLY for it: even a validator bypass cannot tamper with them.
--
-- :exec_role is substituted by init_db.py (identifier-quoted).

GRANT USAGE, CREATE ON SCHEMA public TO :exec_role;

ALTER TABLE employees   OWNER TO :exec_role;
ALTER TABLE departments OWNER TO :exec_role;
ALTER TABLE projects    OWNER TO :exec_role;
ALTER TABLE tasks       OWNER TO :exec_role;

-- the application account keeps the access it needs for Excel import and the employee directory
GRANT SELECT, INSERT, UPDATE, DELETE ON employees, departments, projects, tasks TO CURRENT_USER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO CURRENT_USER;

REVOKE ALL ON users, revoked_tokens, notifications, import_staging FROM :exec_role;
REVOKE ALL ON audit_logs, approval_requests FROM :exec_role;
GRANT SELECT ON audit_logs, approval_requests TO :exec_role;

-- never let the exec role create objects in other schemas / read system secrets
REVOKE ALL ON DATABASE :db_name FROM :exec_role;
GRANT CONNECT ON DATABASE :db_name TO :exec_role;
