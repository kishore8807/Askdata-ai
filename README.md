# 🤖 AI Database Work Assistant

### Natural Language to SQL • Role-Based Access Control • Human Approval • PostgreSQL

A secure, AI-powered database assistant that converts natural-language requests into reviewable PostgreSQL queries. It combines local AI, SQL validation, role-based access control (RBAC), human approval, and audit logging to make database operations safer and easier.

**AI generates → Human reviews → System validates → RBAC checks → DBA approves sensitive changes → PostgreSQL executes → Audit log records**

---

## 📌 Table of Contents

- [Overview](#-overview)
- [Key Features](#-key-features)
- [System Architecture](#-system-architecture)
- [Technology Stack](#-technology-stack)
- [Project Structure](#-project-structure)
- [Installation](#-installation)
- [PostgreSQL Setup](#-postgresql-setup)
- [Local AI Setup](#-local-ai-setup)
- [Environment Configuration](#-environment-configuration)
- [Database Initialization](#-database-initialization)
- [User Roles and Permissions](#-user-roles-and-permissions)
- [Approval Workflow](#-approval-workflow)
- [File Upload and Data Export](#-file-upload-and-data-export)
- [API Documentation](#-api-documentation)
- [Security](#-security)
- [Running Tests](#-running-tests)
- [Troubleshooting](#-troubleshooting)
- [Future Improvements](#-future-improvements)
- [License](#-license)

---

## 🎯 Overview

AI Database Work Assistant helps database teams interact with PostgreSQL using natural language instead of manually writing every SQL query.

Users submit requests in plain English. A locally hosted language model proposes SQL statements, which are then validated and checked against the user's permissions before execution.

Sensitive operations can require approval from a Database Administrator (DBA). Every sensitive action is recorded in an audit log.

### Why This Project?

- Simplifies database interaction using natural language.
- Reduces the need to manually write SQL queries.
- Adds permission checks before database operations.
- Requires human approval for sensitive changes when applicable.
- Uses a locally hosted AI model instead of a paid cloud AI API.
- Supports spreadsheet-based operations and downloadable results.

## ✨ Key Features

### 🧠 AI-Powered SQL Generation
- Converts natural-language requests into PostgreSQL queries.
- Uses Qwen 2.5 3B through Ollama.
- Retrieves relevant database schema information to help generate queries.
- Treats AI-generated SQL as a proposal that must be validated.

### 🔐 Authentication and Access Control
- Supports three roles: DBA, Database Developer, and Data Analyst.
- Uses secure session handling and role-based permissions.
- Supports session expiration, logout, and token revocation.
- Restricts operations according to the configured permission matrix.

### 🛡️ SQL Validation and Execution
- Uses parser-based SQL validation.
- Applies an allow-list and operation classification.
- Checks permissions before executing SQL.
- Uses query timeouts, transaction handling, and rollback on failure.
- Executes SELECT-only transactions in read-only mode.

### 👨‍💻 DBA Approval Workflow
- Routes sensitive operations for approval when required.
- Provides an in-app notification workflow.
- Allows DBAs to approve or reject pending requests.
- Revalidates stored SQL before an approved request executes.

### 📊 Excel and CSV Support
- Accepts Excel and CSV attachments.
- Supports file-driven data operations.
- Previews uploaded data before processing.
- Supports multiple SQL statements in a single request.
- Exports query results as CSV or Excel.

### 📝 Audit Logging
- Records sensitive database operations.
- Tracks approval and execution outcomes.
- Supports audit-log access for DBAs.

---

## 🏗️ System Architecture

The application uses a frontend, Flask proxy, FastAPI backend, local AI model, and PostgreSQL database.

```text
                 ┌──────────────────────┐
                 │     Web Interface    │
                 │    HTML / CSS / JS   │
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │      Flask Proxy     │
                 │   frontend/app.py    │
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │     FastAPI Backend  │
                 │ Authentication       │
                 │ AI SQL Generation    │
                 │ SQL Validation       │
                 │ RBAC Permissions     │
                 │ DBA Approvals        │
                 │ Query Execution      │
                 │ Audit Logging        │
                 └──────┬────────┬──────┘
                        │        │
              ┌─────────▼──┐  ┌──▼────────────┐
              │  Ollama    │  │  PostgreSQL   │
              │ Qwen 2.5 3B│  │   Database    │
              └────────────┘  └───────────────┘
```

### How It Works

1. The user submits a request in natural language.
2. The AI generates a proposed SQL query.
3. The backend validates the SQL.
4. The permission system checks the user's role.
5. Sensitive operations are routed for DBA approval when required.
6. Authorized operations are executed against PostgreSQL.
7. The system records relevant actions in the audit log.

---

## 🛠️ Technology Stack

| Category | Technologies |
|---|---|
| Programming Language | Python |
| Frontend | HTML, CSS, JavaScript, Tailwind CSS |
| Frontend Server / Proxy | Flask |
| Backend API | FastAPI |
| Data Validation | Pydantic |
| Database | PostgreSQL |
| Database Driver | Psycopg 3 |
| SQL Validation | SQLGlot |
| AI Runtime | Ollama |
| AI Model | Qwen 2.5 3B |
| Authentication | JWT, bcrypt |
| Data Processing | Pandas |
| Excel Support | OpenPyXL, xlrd |
| Testing | Pytest |

**AI processing:** The project uses a locally hosted model. No paid or cloud AI API is used for AI inference.

---

## 📂 Project Structure

```text
ai-database-assistant/
│
├── backend/
│   ├── ai/                 # AI prompting and SQL generation
│   ├── approvals/          # DBA approval workflow
│   ├── audit/              # Audit logging
│   ├── auth/               # Authentication and permissions
│   ├── database/           # Database pools and SQL validation
│   ├── excel/              # Spreadsheet processing
│   └── scripts/            # Database initialization utilities
│
├── frontend/
│   ├── app.py              # Flask application and proxy
│   └── static/             # Frontend assets
│
├── database/
│   ├── schema.sql          # Database schema
│   └── seed.sql            # Sample data, if included
│
├── tests/                  # Automated tests
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
└── README.md
```

*Note: The actual repository structure may contain additional files or differ slightly from this overview.*

---

## ⚙️ Installation

### Prerequisites

Install the following before starting:

- Python 3.12
- PostgreSQL 14 or later
- Ollama
- Git

### Step 1: Clone the Repository

Clone the project repository and open its root directory.

### Step 2: Create a Virtual Environment

Create and activate a Python virtual environment for the project.

On Windows, use the activation method appropriate for your terminal.

### Step 3: Install Dependencies

Install the packages listed in `requirements.txt`.

### Step 4: Configure Environment Variables

Create your local `.env` file using `.env.example` as a reference.

Update the database credentials and application secret before starting the application.

---

## 🐘 PostgreSQL Setup

1. Install PostgreSQL.
2. Create a database for the application.
3. Configure the database connection settings in `.env`.
4. Initialize the application tables using the provided database initialization script.
5. Create the required application users.

The project supports PostgreSQL 14 and later according to its current setup documentation.

**Important:** Never commit database passwords or other secrets to GitHub.

---

## 🤖 Local AI Setup

This project uses Ollama to run Qwen 2.5 3B locally.

### Setup Steps

1. Install Ollama.
2. Start the Ollama service.
3. Download the Qwen 2.5 3B model.
4. Configure the model and Ollama endpoint in the environment settings.
5. Start the application and verify AI availability.

Default model:

`qwen2.5:3b`

Default Ollama endpoint:

`http://localhost:11434`

If Ollama is unavailable, the application reports that the AI service is unavailable rather than switching to a cloud AI model.

---

## 🔑 Environment Configuration

Configure the required environment variables in `.env`.

| Variable | Purpose |
|---|---|
| `POSTGRES_*` | PostgreSQL connection settings |
| `SECRET_KEY` | Application signing secret |
| `OLLAMA_BASE_URL` | Local Ollama endpoint |
| `OLLAMA_MODEL` | AI model name |
| `COOKIE_SECURE` | Enables secure cookies when using HTTPS |
| `POSTGRES_EXEC_USER` | Optional restricted SQL execution account |
| `POSTGRES_EXEC_PASSWORD` | Password for the optional execution account |

Use a strong application secret and keep `.env` excluded from Git.

---

## 🗄️ Database Initialization

The project provides scripts to initialize the database and create application users.

The setup process includes:

- Creating database tables and indexes.
- Loading optional sample data.
- Configuring the restricted SQL execution role when required.
- Creating the initial DBA, Developer, and Analyst accounts.

Use the scripts provided by the repository and follow their prompts to configure passwords.

**Security note:** Never publish real passwords or production database credentials in the repository.

---

## 👥 User Roles and Permissions

The application supports three roles.

| Operation | DBA | Developer | Analyst |
|---|---|---|---|
| SELECT | ✅ | ✅ | ✅ |
| INSERT | ✅ | ✅ | ❌ |
| UPDATE | ✅ | Approval may be required | ❌ |
| DELETE | ✅ | Approval may be required | ❌ |
| CREATE | ✅ | Controlled access | ❌ |
| ALTER | ✅ | Controlled access | ❌ |
| DROP / TRUNCATE | Confirmation may be required | Approval may be required | ❌ |
| Audit Logs | ✅ | Restricted | Restricted |

The exact permissions are governed by the application's permission matrix.

Analysts are read-only. Destructive operations have additional restrictions and may require explicit confirmation or DBA approval.

---

## 🔄 Approval Workflow

The approval system provides an additional layer of protection for sensitive database changes.

### Workflow

1. A Developer submits a natural-language request.
2. The AI generates the proposed SQL.
3. The system validates the SQL and checks permissions.
4. If approval is required, the request enters the pending queue.
5. Active DBAs receive in-app notifications.
6. A DBA reviews the request and its proposed SQL.
7. The DBA approves or rejects the request.
8. Approved requests are revalidated before execution.
9. The final outcome is recorded.

### Approval States

- `PENDING`
- `APPROVED`
- `REJECTED`
- `EXECUTED`
- `FAILED`

Approval is handled by the backend. The client cannot bypass the workflow simply by sending an approved flag.

---

## 📊 File Upload and Data Export

### Supported File Types

- `.csv`
- `.xlsx`
- `.xls`

### File Processing

Users can attach a spreadsheet and request operations such as:

- Retrieve records matching the uploaded data.
- Add records from a file.
- Update records using spreadsheet values.
- Remove records matching the file.
- Preview uploaded rows before processing.

The backend validates uploaded files and their contents. File-driven operations follow the same SQL validation and permission checks as other requests.

### Exporting Results

Query results can be exported as:

- CSV
- Excel

Exports are generated by the server and are subject to configured size limits and auditing.

---

## 🔌 API Documentation

The backend exposes API endpoints for authentication, SQL generation, validation, execution, approvals, attachments, exports, and auditing.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/auth/login` | Authenticate a user |
| POST | `/api/auth/logout` | Sign out and revoke the session |
| GET | `/api/auth/me` | Retrieve the current user |
| GET | `/api/auth/permissions` | Retrieve the permission matrix |
| POST | `/api/query/generate` | Generate proposed SQL |
| POST | `/api/query/validate` | Validate SQL |
| POST | `/api/query/execute` | Execute authorized SQL |
| GET | `/api/query/history` | Retrieve recent SQL history |
| GET | `/api/schema` | Retrieve schema information visible to the role |
| GET | `/api/status` | Check database and AI status |
| GET | `/api/approvals/pending` | Retrieve pending approvals |
| GET | `/api/approvals/mine` | Retrieve approval requests |
| POST | `/api/approvals/{id}/approve` | Approve and execute a request |
| POST | `/api/approvals/{id}/reject` | Reject a request |
| GET | `/api/notifications` | Retrieve notifications |
| POST | `/api/attachments` | Upload a spreadsheet |
| POST | `/api/export` | Export query results |
| GET | `/api/audit-logs` | Retrieve audit logs |

The application also provides endpoints for attachment previews, notification management, employee directory access, and health checks.

Write requests require the appropriate CSRF token. Access to endpoints depends on authentication and the configured role permissions.

---

## 🔐 Security

Security is a central part of the application design.

Key controls include:

- Password hashing with bcrypt.
- Session handling using JWT in HttpOnly cookies.
- CSRF protection for write requests.
- Session expiry and token revocation.
- Role-based access control.
- Parser-based SQL validation.
- Read-only transactions for SELECT-only operations.
- Query and lock timeouts.
- Transaction rollback on failure.
- DBA approval for sensitive operations when required.
- Restricted SQL execution credentials as an additional security measure.
- File validation and spreadsheet formula protection.
- Audit logging for sensitive actions.
- Security headers and restricted error details.

### Production Recommendations

- Use HTTPS.
- Enable secure cookies when deployed over HTTPS.
- Keep `.env` private.
- Use strong secrets and database passwords.
- Restrict access to PostgreSQL and Ollama ports.
- Use least-privilege database accounts.
- Review permissions and approval policies before production use.

**Important:** AI-generated SQL must always be treated as a proposal. Review its impact before allowing database changes.

---

## 🧪 Running Tests

The project includes automated tests covering areas such as:

- Authentication and authorization.
- SQL validation and execution.
- DBA approval workflows.
- File attachments and exports.
- Proxy behavior.
- Security controls.

Install the development dependencies and run the test suite using the project's documented testing procedure.

Some integration tests require a configured PostgreSQL instance and the appropriate environment variables.

---

## 🛠️ Troubleshooting

| Problem | Recommended Action |
|---|---|
| AI service unavailable | Start Ollama and verify that the configured model is installed. |
| Database connection failure | Check PostgreSQL status and the connection settings in `.env`. |
| Application fails to start | Verify the required environment variables, especially `SECRET_KEY`. |
| Request requires approval | Sign in using a DBA account and review the pending request. |
| Spreadsheet upload rejected | Check the file type, size, row limits, column headers, and formulas. |
| SQL execution fails | Review the validation errors, permissions, and database configuration. |

---

## 🚀 Future Improvements

Potential improvements for future versions include:

- More comprehensive AI-generated SQL explanations.
- Enhanced query review and visualization.
- Expanded monitoring and operational dashboards.
- Additional database support.
- More comprehensive integration and security testing.
- Improved deployment and developer documentation.

*These are potential enhancements, not claims that the features are already implemented.*

---

## 📄 License

No license is currently specified in the project documentation.

Add an appropriate `LICENSE` file if you intend to distribute the project as open source.

---

## ⭐ Project Summary

**AI Database Work Assistant** demonstrates how local AI, natural-language processing, SQL validation, role-based access control, human approval, and PostgreSQL can work together to support safer database operations.

The goal is simple: make database interaction easier while keeping permissions, validation, and human oversight at the centre of the workflow.