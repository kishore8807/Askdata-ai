# 🤖 AI Database Work Assistant

> A secure, role-based database assistant that turns natural-language requests into reviewable PostgreSQL queries.

AI Database Work Assistant is an application for database teams. Users submit requests in plain English, and a locally hosted language model (Qwen 2.5 3B through Ollama) proposes PostgreSQL statements. The application validates the SQL, applies role-based access control (RBAC), and routes sensitive operations to a database administrator (DBA) for approval before execution. Actions are recorded in an audit log.

## ✨ Key workflow

```text
AI generates → Human reviews → System validates → RBAC checks → DBA approves sensitive changes → PostgreSQL executes → Audit log records
```

## 🎨 Interface

The UI is the exported Stitch design (warm editorial palette, Newsreader / Inter / JetBrains Mono, AskData Ai sidebar shell) wired to real endpoints. Design tokens in frontend/static/tailwind-config.js are copied verbatim from the export.

## 1. Overview

| Capability | Where |
| --- | --- |
| Login, three roles, session expiry, logout | backend/auth/ |
| Natural language → SQL (local Qwen 2.5 3B) | backend/ai/ |
| SQL validation (parser-based allow-list) | backend/database/sql_validator.py |
| RBAC permission matrix (configurable) | backend/auth/permissions.py |
| DBA approval workflow + in-app notifications | backend/approvals/ |
| Safe PostgreSQL execution (pool, timeouts, rollback) | backend/database/postgres.py |
| Attach an Excel/CSV file to a request; bulk update / remove / add driven by the file | backend/excel/, backend/ai/batch.py |
| Multi-query execution and CSV / Excel result download | backend/database/, backend/export_routes.py |
| Audit log | backend/audit/ |
| Stitch UI + Flask front door | frontend/ |

## 2. Architecture

```text
Browser (Stitch UI: HTML/CSS/JS)
        │  same origin → HttpOnly session cookie, CSRF header
        ▼
Flask  (frontend/app.py)  serves the UI, stateless reverse proxy for /api/*
        ▼
FastAPI (backend/main.py)
   ├─ auth ──────────── bcrypt, JWT in HttpOnly cookie, CSRF, lockout, revocation
   ├─ AI agent ──────── schema retrieval → prompt → Ollama (qwen2.5:3b) → SQL proposal
   ├─ SQL validator ─── parse → allow-list → classify → RBAC decision → normalised SQL
   ├─ approvals ─────── PENDING → APPROVED → EXECUTED / FAILED, or REJECTED
   ├─ executor ──────── pooled, READ ONLY for SELECT, timeouts, commit/rollback
   ├─ excel ─────────── parse, validate, server-side staging, explicit import
   └─ audit ─────────── every sensitive action
        ▼
PostgreSQL  (app account + optional least-privilege SQL-execution account)
Two PostgreSQL pools are used: one for application metadata and one for user SQL, so a slow user query cannot starve authentication.
```

Layout note. The specification lists both backend/database.py and a backend/database/ package. Python cannot have both, so the pool code lives in backend/database/__init__.py / _pool.py.

## 3. Technology stack

Python 3.12 · Flask · FastAPI · Pydantic · psycopg 3 (+ pool) · sqlglot (PostgreSQL dialect parser) · pandas · openpyxl · xlrd (attachments and exports) · bcrypt · PyJWT · PostgreSQL 18 · Ollama + Qwen 2.5 3B (local only) · HTML/CSS/JS + Tailwind (Stitch tokens). No paid or cloud AI API is used anywhere.
## 4. Installation

git clone <your repo> ai-database-assistant && cd ai-database-assistant
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt                           # add requirements-dev.txt for tests
cp .env.example .env                                      # then edit it (see §8)
## 5. PostgreSQL setup

Install PostgreSQL 18 (the SQL is compatible with 14+), then:
psql -U postgres -c "CREATE DATABASE ai_database;"
Or with Docker only for the database: docker compose up -d db.
## 6. Ollama installation

Install from https://ollama.com/download, then make sure it is running (ollama serve; it listens on http://localhost:11434).
## 7. Qwen model setup

ollama pull qwen2.5:3b
ollama run qwen2.5:3b "say hi"      # optional smoke test
If Ollama is stopped, the app answers “AI service is unavailable. Please start Ollama.” and never falls back to a cloud model. The header pill shows Ready / Model missing / Offline live.
## 8. Environment configuration

cp .env.example .env, then at minimum set:
| Variable | Purpose |
| --- | --- |
| POSTGRES_* | connection to your database |
| SECRET_KEY | ≥ 32 chars; python -c "import secrets; print(secrets.token_urlsafe(48))". The API refuses to start without it. |
| OLLAMA_BASE_URL, OLLAMA_MODEL | defaults http://localhost:11434, qwen2.5:3b |
| COOKIE_SECURE=true | when served over HTTPS |
| POSTGRES_EXEC_USER/PASSWORD | optional, recommended least-privilege SQL account (§17) |

No secrets are hard-coded; .env is git-ignored.
## 9. Database initialisation

python -m backend.scripts.init_db --seed                  # tables, indexes, constraints + sample data
python -m backend.scripts.init_db --seed --least-privilege   # …and the restricted SQL-execution role
python -m backend.scripts.bootstrap_users                 # creates dba / developer / analyst (prompts for passwords)
Passwords are never stored in SQL files or code. For scripted setups: DBA_PASSWORD=… DEVELOPER_PASSWORD=… ANALYST_PASSWORD=… python -m backend.scripts.bootstrap_users (min. 10 characters). Add more users: python -m backend.scripts.bootstrap_users --add alice alice@corp.com DEVELOPER.

Both scripts are idempotent. database/schema.sql / seed.sql can also be run directly with psql -f. Tables: users, employees, departments, projects, tasks, approval_requests, audit_logs as specified, plus notifications, revoked_tokens (logout) and import_staging (Excel previews).
## 10. User roles

| Role | Purpose | Summary |
| --- | --- | --- |
| DBA | Administration, approvals, audit | Everything; dangerous statements still need explicit confirmation |
| Database Developer | Development work | Reads/inserts/creates/alters directly; UPDATE / DELETE / DROP / TRUNCATE / destructive ALTER need DBA approval |
| Data Analyst | Analysis | Read-only (SELECT, joins, aggregates, CTEs, subqueries) |

Default matrix (GET /api/auth/permissions, also shown in Settings):
| Operation | DBA | Developer | Analyst |
| --- | --- | --- | --- |
| SELECT / JOIN / GROUP BY / HAVING / ORDER BY | ✅ | ✅ | ✅ |
| INSERT | ✅ | ✅ | ❌ |
| UPDATE, DELETE | ✅ | ⏳ DBA approval | ❌ |
| CREATE, ALTER | ✅ | ✅ (controlled) | ❌ |
| DROP, TRUNCATE | ✅ (confirm) | ⏳ DBA approval | ❌ |
| BEGIN … COMMIT | ✅ | ✅ (strictest op inside wins) | ❌ |

“Controlled” = allowed, but protected tables stay off-limits and destructive ALTER (drop/rename/retype column) escalates to approval.

Configuring permissions. Point PERMISSIONS_FILE at JSON, e.g. {"DEVELOPER": {"DELETE": "DENY", "CREATE": "APPROVAL"}}. Values: ALLOW, APPROVAL, DENY. Invariants cannot be relaxed: Analysts are read-only, and a broken file falls back to the safe defaults.
## 11. DBA approval workflow

Developer: "Change Kumar salary to 60000"
   → AI SQL → validate → Proceed & Execute
   → approval_requests row (PENDING) + 🔔 to every active DBA        (nothing touches PostgreSQL)
DBA opens Pending Approvals → reads requester, role, request text, SQL, operation, table, time
   → Approve & Execute            → re-validates the STORED SQL against the requester's role → executes → EXECUTED
   → Reject (reason required)     → REJECTED, never executes → 🔔 to the developer with the reason
Statuses: PENDING, APPROVED, REJECTED, EXECUTED, FAILED. The approval state lives only in PostgreSQL; fields like approved: true sent by a client are ignored. The request row is locked (FOR UPDATE) so double-approval and approve/reject races are impossible; a DBA cannot approve their own request; dangerous requests need the DBA's explicit confirmation tick.

API-wise the execution step happens inside POST /api/approvals/{id}/approve, performed by the backend after it verifies the request is PENDING, re-validates it, and records the DBA as reviewer.
## 12. Attaching files, multiple queries and downloads

### Attach a file in Natural Language Intent

## 1. Click Attach file (or drag a file onto the panel). .xlsx, .xls and .csv are accepted. The first row must be column names; they are normalised to snake_case (Employee Code → employee_code). The chip shows the file name, the row count and the columns; Preview shows every row, ✕ removes it.

## 2. Type what you want, e.g. “Update the salary of the employees in the file”, “Remove the employees listed in the file”, “Add the employees in the file”, or “Show the employees from the file”.

## 3. Generate SQL. The model is told which columns the file has and is asked to write the SQL for one row with {{column}} placeholders. The server then expands that template once per row, rendering every value as a properly escaped SQL literal, and validates the whole result like any other SQL. The screen shows the template, the expanded queries, and “Runs once for each of the N rows in …”.

## 4. Proceed & Execute. All rows run in one all-or-nothing transaction: if row 17 fails, nothing is saved and the message says “Query 17 of 40 failed … No changes were saved.”

Why a template? A 3B model cannot reliably write hundreds of statements, and file contents must never be trusted as SQL. A cell containing x'); DROP TABLE employees;-- stays inert text.

RBAC and approval apply exactly as for typed SQL: an Analyst can only run read-only file-driven queries; a Developer’s file-driven UPDATE/DELETE becomes one approval request containing all N queries; a DBA runs them directly (after confirmation when dangerous). Files are stored server-side per user (one active file; auto-expires after a day), formulas (=…) are rejected, files over MAX_ATTACH_ROWS (default 500) are rejected rather than silently truncated.
### Multiple queries

- Ask for several things (“Show departments and count employees”) and the model returns several statements, or click Write SQL manually and separate queries with ;.
- Every statement is validated individually and the strictest decision wins (a script containing one DELETE needs approval for a Developer; one DROP is denied to an Analyst). The script runs as one atomic transaction. A script made only of SELECTs runs in a READ ONLY transaction.
- Results appear as one tab per query, plus a per-query summary. Up to MAX_STATEMENTS (default 500) queries per execution; long batches of identically shaped SELECTs are merged into a single table.
- An explicit BEGIN; …; COMMIT; (or ROLLBACK;) block still works.
### Downloading results

The Result panel has CSV (the tab you are viewing) and Excel (every result as its own sheet) buttons. Files are built by the server, cells that could run as spreadsheet formulas are neutralised, the limit is MAX_EXPORT_ROWS, and each download is audited (RESULT_EXPORTED).
## 13. API documentation

All write requests need the X-CSRF-Token header (value of the csrf_token cookie; the UI does this automatically). Errors look like {"success": false, "message": "…"}.
| Method & path | Role | Description |
| --- | --- | --- |
| POST /api/auth/login | public | {username, password} (username or email) → sets cookies |
| POST /api/auth/logout | any | revokes the session token |
| GET /api/auth/me | any | user, role, own permissions, expiry |
| GET /api/auth/permissions | any | full RBAC matrix |
| POST /api/query/generate | any | {question, attachment_id?} → {sql, statement_count, batch, operation_type, requires_confirmation, requires_dba_approval, valid, validation_errors, dangerous, …}; executes nothing |
| POST /api/query/validate | any | dry-run validation of edited SQL |
| POST /api/query/execute | any | {sql, question?, confirm_dangerous?} → 200 {results: [per query], …}, 202 approval created, 403 permission, 409 dangerous-confirmation needed, 422 validation, 503 DB down |
| GET /api/query/history | any | your recent SQL |
| GET /api/schema | any | schema visible to your role |
| GET /api/status | any | database + AI health |
| GET /api/approvals/pending | DBA | pending requests (with danger info) |
| GET /api/approvals/mine | any | your requests (DBA: all) |
| POST /api/approvals/{id}/approve | DBA | {confirm_dangerous?} — approves and executes |
| POST /api/approvals/{id}/reject | DBA | {reason} |
| GET /api/notifications, POST /api/notifications/read | any | in-app notifications |
| POST /api/attachments | any | multipart file (.xlsx/.xls/.csv) → {attachment_id, columns, row_count, sample} |
| GET /api/attachments/{id}, DELETE /api/attachments/{id} | owner | preview all rows / remove |
| POST /api/export | any | {format: csv\|xlsx, sheets: [{name, columns, rows}]} → file download |
| GET /api/employees | any | employee directory (?search=) |
| GET /api/audit-logs | DBA | ?action=&user_id=&limit=&offset= |
| GET /api/health | public | liveness |

## 14. Security

- Passwords: bcrypt (cost 12), min. 10 characters, constant-time check even for unknown users, generic error message, lockout after repeated failures (LOGIN_MAX_FAILURES).
- Sessions: JWT in an HttpOnly, SameSite=Strict (and Secure when COOKIE_SECURE=true) cookie; expiry; server-side revocation on logout; CSRF token signed inside the JWT and checked on every non-GET request.
- The backend is authoritative: the role is read from the database on every request — forged tokens, forged role claims, forged “approved” flags are ignored (tested).
- The Flask proxy is stateless. It accepts no cookies of its own, which a regression test enforces (an earlier draft leaked one user’s session to all visitors through a shared HTTP session).
- SQL: parser-based validation, allow-list, normalised execution, statement/lock timeouts, result cap (server-side cursor), READ ONLY transactions for SELECT, rollback on any failure.
- Least-privilege execution (recommended): python -m backend.scripts.init_db --least-privilege creates POSTGRES_EXEC_USER, which owns only the business tables, has no access to users and friends and is read-only on audit_logs / approval_requests. PostgreSQL itself then refuses credential access or audit tampering even if the validator had a bug (tested).
- Attached files: extension + signature check, size/row/column limits, formulas rejected, bounded reads, private per user, never executed as SQL (values are escaped literals inside a validated template). Prompt-injection text inside a file can at worst change the model's draft, which is still validated, permission-checked and (for sensitive changes) approved.
- Exports: server-built, spreadsheet-formula-safe, size-limited, audited.
- Clean sign-out: signing out (or session expiry) wipes all client-side state, so the next person on the same browser tab sees nothing from the previous session.
- Errors: friendly messages only; stack traces and driver details go to server logs. Audit logs never contain passwords or tokens.
- Headers: CSP, X-Frame-Options: DENY, nosniff, no-store on API responses. All UI output is HTML-escaped.
- Deploy checklist: HTTPS in front (COOKIE_SECURE=true), strong SECRET_KEY, restricted .env, don’t expose port 8000 or 5432 publicly, keep Ollama on localhost.
## 15. Project structure

```text
├── main.py                    # Application entry point
├── backend/
│   ├── ai/                    # Prompting, schema retrieval and SQL generation
│   ├── approvals/             # DBA approval workflow
│   ├── audit/                 # Audit logging and routes
│   ├── auth/                  # Authentication, sessions and permissions
│   ├── database/              # PostgreSQL pools, execution and validation
│   ├── excel/                 # Spreadsheet attachment processing
│   └── scripts/               # Database and user bootstrap utilities
├── frontend/                  # Flask UI and static assets
├── database/                  # Database schema and seed data
├── tests/                     # Backend and UI smoke tests
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
16. Running tests
Install the development dependencies and run the test suite:
pip install -r requirements-dev.txt
pytest
The test suite covers authentication and RBAC, SQL validation and execution, approvals, file attachments, exports, proxy behavior, and security controls. Some integration tests may require a configured PostgreSQL instance and the relevant environment variables.
17. Operational recommendations
- Run behind HTTPS in production and set COOKIE_SECURE=true.
- Store secrets in environment variables or a managed secret store; never commit .env.
- Keep PostgreSQL and Ollama ports private and restrict network access.
- Use the least-privilege SQL execution account where possible.
- Back up the database and audit records according to your organisation's retention policy.
- Review permission configuration and approval policies before production use.
- Treat AI-generated SQL as a proposal: inspect the query and its impact before execution.
18. Troubleshooting
Issue	Recommended action
AI service is unavailable	Start Ollama and verify that the configured model is installed.
Database connection fails	Check PostgreSQL status and the POSTGRES_* settings in .env.
Application refuses to start	Confirm that required environment variables, especially SECRET_KEY, are configured.
A request requires DBA approval	Sign in with a DBA account to review the pending request.
Spreadsheet upload is rejected	Check the file type, size, row limit, column headers, and whether cells contain formulas.
```

## 19. License

No license is specified in this repository. Add a LICENSE file before distributing the project if you intend to apply an open-source license.
#   A s k d a t a - a i  
 