-- AI Database Work Assistant — PostgreSQL schema (PostgreSQL 14+; targeted at 18)
-- Idempotent: safe to re-run.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ───────────── users ─────────────
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    email         VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT         NOT NULL,
    role          VARCHAR(20)  NOT NULL CHECK (role IN ('DBA', 'DEVELOPER', 'ANALYST')),
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ───────────── departments ─────────────
CREATE TABLE IF NOT EXISTS departments (
    id              SERIAL PRIMARY KEY,
    department_name VARCHAR(100) NOT NULL UNIQUE,
    description     TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ───────────── employees ─────────────
CREATE TABLE IF NOT EXISTS employees (
    id            SERIAL PRIMARY KEY,
    employee_code VARCHAR(30)  NOT NULL UNIQUE,
    name          VARCHAR(150) NOT NULL,
    email         VARCHAR(255) NOT NULL UNIQUE,
    department    VARCHAR(100) NOT NULL,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    designation   VARCHAR(100) NOT NULL,
    salary        NUMERIC(12,2) NOT NULL CHECK (salary >= 0),
    joining_date  DATE         NOT NULL,
    status        VARCHAR(20)  NOT NULL DEFAULT 'Active'
                  CHECK (status IN ('Active', 'Inactive', 'On Leave', 'Terminated')),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_employees_department    ON employees (department);
CREATE INDEX IF NOT EXISTS idx_employees_department_id ON employees (department_id);
CREATE INDEX IF NOT EXISTS idx_employees_status        ON employees (status);

-- Keep employees.department_id in sync with the department name when it matches.
CREATE OR REPLACE FUNCTION employees_sync_department() RETURNS trigger AS $$
BEGIN
    IF NEW.employee_code IS NULL OR NEW.employee_code = '' THEN
        NEW.employee_code := 'EMP' || lpad(cast(nextval('employees_id_seq') as text), 3, '0');
    END IF;
    IF NEW.department_id IS NULL OR
       (TG_OP = 'UPDATE' AND NEW.department IS DISTINCT FROM OLD.department
                         AND NEW.department_id IS NOT DISTINCT FROM OLD.department_id) THEN
        SELECT d.id INTO NEW.department_id
          FROM departments d
         WHERE lower(d.department_name) = lower(NEW.department)
         LIMIT 1;
    END IF;
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_employees_sync_department ON employees;
CREATE TRIGGER trg_employees_sync_department
    BEFORE INSERT OR UPDATE ON employees
    FOR EACH ROW EXECUTE FUNCTION employees_sync_department();

-- ───────────── projects ─────────────
CREATE TABLE IF NOT EXISTS projects (
    id            SERIAL PRIMARY KEY,
    project_name  VARCHAR(150) NOT NULL,
    description   TEXT,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    start_date    DATE,
    end_date      DATE,
    status        VARCHAR(20) NOT NULL DEFAULT 'Planned'
                  CHECK (status IN ('Planned', 'Active', 'On Hold', 'Completed', 'Cancelled')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS idx_projects_department ON projects (department_id);

-- ───────────── tasks ─────────────
CREATE TABLE IF NOT EXISTS tasks (
    id          SERIAL PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id)  ON DELETE CASCADE,
    employee_id INTEGER          REFERENCES employees(id) ON DELETE SET NULL,
    task_name   VARCHAR(200) NOT NULL,
    description TEXT,
    status      VARCHAR(20) NOT NULL DEFAULT 'Todo'
                CHECK (status IN ('Todo', 'In Progress', 'Blocked', 'Done')),
    priority    VARCHAR(10) NOT NULL DEFAULT 'Medium'
                CHECK (priority IN ('Low', 'Medium', 'High', 'Critical')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tasks_project  ON tasks (project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_employee ON tasks (employee_id);

-- ───────────── approval_requests ─────────────
CREATE TABLE IF NOT EXISTS approval_requests (
    id                       SERIAL PRIMARY KEY,
    requester_id             INTEGER     NOT NULL REFERENCES users(id),
    requester_role           VARCHAR(20) NOT NULL,
    natural_language_request TEXT,
    generated_sql            TEXT        NOT NULL,
    operation_type           VARCHAR(30) NOT NULL,
    target_table             VARCHAR(200),
    status                   VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                             CHECK (status IN ('PENDING','APPROVED','REJECTED','EXECUTED','FAILED')),
    reviewed_by              INTEGER REFERENCES users(id),
    reviewed_at              TIMESTAMPTZ,
    rejection_reason         TEXT,
    execution_message        TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_approvals_status    ON approval_requests (status, created_at);
CREATE INDEX IF NOT EXISTS idx_approvals_requester ON approval_requests (requester_id);

-- ───────────── audit_logs ─────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id             BIGSERIAL PRIMARY KEY,
    user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    role           VARCHAR(20),
    action         VARCHAR(50) NOT NULL,
    operation_type VARCHAR(30),
    sql_query      TEXT,
    target_table   VARCHAR(200),
    status         VARCHAR(20) NOT NULL DEFAULT 'SUCCESS',
    ip_address     VARCHAR(64),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user    ON audit_logs (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action  ON audit_logs (action);

-- ───────────── supporting tables (not in the base spec, required by features) ─────────────
-- In-app notifications (approval workflow)
CREATE TABLE IF NOT EXISTS notifications (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(200) NOT NULL,
    message     TEXT NOT NULL,
    approval_id INTEGER REFERENCES approval_requests(id) ON DELETE CASCADE,
    is_read     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications (user_id, is_read, created_at DESC);

-- Logout / token revocation
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti        VARCHAR(64) PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL
);

-- Server-side storage for files attached in the AI Query Studio (one active attachment per user).
-- The browser only ever holds the id; file data never round-trips through the client.
CREATE TABLE IF NOT EXISTS import_staging (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename   VARCHAR(255) NOT NULL,
    rows       JSONB NOT NULL,
    summary    JSONB NOT NULL,
    status     VARCHAR(20) NOT NULL DEFAULT 'STAGED' CHECK (status IN ('STAGED','IMPORTED','DISCARDED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE import_staging IS 'Files attached to the AI Query Studio (xlsx/xls/csv), stored as JSON rows';
