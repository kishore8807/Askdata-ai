"""Domain constants and lightweight row models (the tables themselves live in database/schema.sql)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Role(str, Enum):
    DBA = "DBA"
    DEVELOPER = "DEVELOPER"
    ANALYST = "ANALYST"

    @property
    def label(self) -> str:
        return {"DBA": "Database Administrator", "DEVELOPER": "Database Developer", "ANALYST": "Data Analyst"}[self.value]


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class Decision(str, Enum):
    """Outcome of the RBAC check for one operation."""
    ALLOW = "ALLOW"
    APPROVAL = "APPROVAL"   # allowed only after DBA approval
    DENY = "DENY"


class Op(str, Enum):
    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    CREATE = "CREATE"
    ALTER = "ALTER"
    DROP = "DROP"
    TRUNCATE = "TRUNCATE"
    TRANSACTION = "TRANSACTION"
    UNKNOWN = "UNKNOWN"


class AuditAction(str, Enum):
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    SQL_GENERATED = "SQL_GENERATED"
    SQL_EXECUTION_REQUESTED = "SQL_EXECUTION_REQUESTED"
    SQL_EXECUTED = "SQL_EXECUTED"
    SQL_REJECTED = "SQL_REJECTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_APPROVED = "APPROVAL_APPROVED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    EXCEL_UPLOADED = "EXCEL_UPLOADED"
    EMPLOYEES_IMPORTED = "EMPLOYEES_IMPORTED"
    FILE_ATTACHED = "FILE_ATTACHED"
    FILE_REMOVED = "FILE_REMOVED"
    RESULT_EXPORTED = "RESULT_EXPORTED"
    DATABASE_ERROR = "DATABASE_ERROR"


@dataclass
class CurrentUser:
    id: int
    username: str
    email: str
    role: Role
    ip: str | None = None

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "email": self.email,
                "role": self.role.value, "role_label": self.role.label}


# Tables that never appear in the SQL console / AI context (credentials & internals).
HIDDEN_TABLES = frozenset({"users", "revoked_tokens", "notifications", "import_staging"})
# Tables the DBA may READ through SQL but that nobody may modify through SQL.
PROTECTED_TABLES = frozenset({"approval_requests", "audit_logs"})
# Business tables everybody (per RBAC) can work with.
