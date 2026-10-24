"""SQL validator — the gatekeeper between "SQL text" and PostgreSQL.

Design (defence in depth, nothing here trusts the AI model or the browser):

1. Parse with a real PostgreSQL-dialect parser (sqlglot). Unparseable SQL is rejected.
2. ALLOW-LIST statement types. Anything unknown (COPY, GRANT, DO, CALL, SET, EXPLAIN, VACUUM, ...) is rejected.
3. Walk the *entire* tree so tricks like data-modifying CTEs, SELECT ... INTO, FOR UPDATE are classified correctly.
4. Block dangerous functions, system catalogs / foreign schemas, and hidden or protected tables.
5. Multi-statement input is only accepted as a well-formed BEGIN ... COMMIT/ROLLBACK block.
6. Compute the RBAC decision (ALLOW / APPROVAL / DENY) from the permission matrix, using the strictest op involved.
7. Return *normalised* SQL regenerated from the parse tree. What is validated is exactly what is executed
   (no parser-differential tricks with comments or odd whitespace).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from ..auth.permissions import decision_for, strictest
from ..config import settings
from ..models import HIDDEN_TABLES, PROTECTED_TABLES, Decision, Op, Role

logging.getLogger("sqlglot").setLevel(logging.ERROR)

ALLOWED_SCHEMAS = {"", "public"}

# Functions that read files, sleep, open connections, change settings, or kill sessions.
BLOCKED_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "set_config", "current_setting", "dblink", "dblink_exec",
    "dblink_connect", "lo_import", "lo_export", "lo_create", "lo_unlink", "lo_get", "lo_put", "copy",
    "query_to_xml", "query_to_xml_and_xmlschema", "table_to_xml", "database_to_xml", "cursor_to_xml",
    "txid_current", "version", "inet_server_addr", "inet_server_port", "current_database", "current_user",
    "session_user", "current_catalog", "current_schemas",
}
BLOCKED_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink", "xpath_", "adminpack", "file_fdw")
# Harmless pg_* helpers we explicitly allow.
ALLOWED_PG_FUNCTIONS = {"pg_typeof", "pg_size_pretty"}

_SEVERITY = [Op.DROP, Op.TRUNCATE, Op.DELETE, Op.UPDATE, Op.ALTER, Op.CREATE, Op.INSERT, Op.SELECT]
_DESTRUCTIVE_ALTER = re.compile(r"\b(DROP\s+(COLUMN|CONSTRAINT)|ALTER\s+COLUMN\s+\S+\s+(SET\s+DATA\s+)?TYPE|RENAME)\b", re.I)
_FORBIDDEN_ALTER = re.compile(r"\b(OWNER\s+TO|TRIGGER|ROW\s+LEVEL\s+SECURITY|SET\s+SCHEMA|SET\s+TABLESPACE|INHERIT)\b", re.I)


class ValidationError(Exception):
    pass


@dataclass
class ValidationResult:
    valid: bool = False
    errors: list[str] = field(default_factory=list)          # safe, user-facing
    sql: str = ""                                              # normalised SQL (statements joined)
    statements: list[str] = field(default_factory=list)       # normalised body statements (no BEGIN/COMMIT)
    is_transaction: bool = False
    is_script: bool = False                                    # several statements, run atomically
    ends_with_rollback: bool = False
    operation: Op = Op.UNKNOWN                                 # headline operation
    operations: list[Op] = field(default_factory=list)        # all ops involved
    target_tables: list[str] = field(default_factory=list)
    decision: Decision = Decision.DENY                        # RBAC verdict for the supplied role
    permission_message: str | None = None
    dangerous: bool = False
    danger_reasons: list[str] = field(default_factory=list)
    read_only: bool = False                                    # every statement is a pure SELECT

    @property
    def target_table(self) -> str | None:
        return ", ".join(self.target_tables[:3]) if self.target_tables else None

    @property
    def permission_denied(self) -> bool:
        """True only when RBAC (not validation) is the reason the SQL cannot run."""
        return self.permission_message is not None

    @property
    def operation_label(self) -> str:
        return self.operation.value

    def summary(self) -> dict:
        return {
            "valid": self.valid, "errors": self.errors, "operation_type": self.operation.value,
            "operations": [o.value for o in self.operations], "target_table": self.target_table,
            "decision": self.decision.value, "permission_denied": self.permission_denied, "dangerous": self.dangerous, "danger_reasons": self.danger_reasons,
            "is_transaction": self.is_transaction, "is_script": self.is_script, "statement_count": len(self.statements),
        }


# ───────────────────────── helpers ─────────────────────────
def _table_name(t: exp.Table) -> str:
    return (t.name or "").lower()


def _cte_names(tree: exp.Expression) -> set[str]:
    return {c.alias_or_name.lower() for c in tree.find_all(exp.CTE) if c.alias_or_name}


def _no_column_refs(e: exp.Expression) -> bool:
    return not any(True for _ in e.find_all(exp.Column))


def _is_unsafe_where(where: exp.Where | None) -> bool:
    """True when a WHERE clause is missing or cannot restrict rows (1=1, TRUE, id=1 OR 1=1, ...)."""
    if where is None:
        return True
    cond = where.this

    def unsafe(e: exp.Expression) -> bool:
        while isinstance(e, exp.Paren):
            e = e.this
        if isinstance(e, exp.Or):
            return unsafe(e.left) or unsafe(e.right)
        if isinstance(e, exp.And):
            return unsafe(e.left) and unsafe(e.right)
        if isinstance(e, exp.Boolean):
            return bool(e.this)
        if isinstance(e, exp.Not):
            return False
        # A predicate that touches no column cannot narrow the row set: 1=1, 'a'='a', 2>1 ...
        return _no_column_refs(e) and not e.find(exp.Subquery) and not e.find(exp.Select)

    return unsafe(cond)


def _headline(ops: set[Op]) -> Op:
    for op in _SEVERITY:
        if op in ops:
            return op
    return Op.UNKNOWN


def _function_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for f in tree.find_all(exp.Func):
        if isinstance(f, exp.Anonymous):
            names.add((f.name or "").lower())
        else:
            names.add(f.sql_name().lower())
    return names


# ───────────────────────── statement analysis ─────────────────────────
@dataclass
class _Stmt:
    sql: str
    ops: set[Op]
    targets: list[str]
    reads: list[exp.Table]
    writes: list[str]
    dangerous: list[str]


def _analyse(stmt: exp.Expression, role: Role | None) -> _Stmt:
    ops: set[Op] = set()
    dangerous: list[str] = []
    writes: list[str] = []
    ctes = _cte_names(stmt)

    # ── classify by node type (allow-list)
    if isinstance(stmt, (exp.Select, exp.SetOperation, exp.Subquery)):
        ops.add(Op.SELECT)
        if isinstance(stmt, exp.Select) and stmt.args.get("into"):
            ops = {Op.CREATE}
            into = stmt.args["into"].find(exp.Table)
            if into is not None:
                writes.append(_table_name(into))
        if stmt.args.get("locks") or stmt.find(exp.Lock):
            raise ValidationError("Row locking clauses (FOR UPDATE / FOR SHARE) are not allowed.")
    elif isinstance(stmt, exp.Insert):
        ops.add(Op.INSERT)
    elif isinstance(stmt, exp.Update):
        ops.add(Op.UPDATE)
    elif isinstance(stmt, exp.Delete):
        ops.add(Op.DELETE)
    elif isinstance(stmt, exp.Create):
        kind = (stmt.args.get("kind") or "").upper()
        if kind not in {"TABLE", "INDEX", "VIEW"}:
            raise ValidationError(f"CREATE {kind or 'statement'} is not supported.")
        ops.add(Op.CREATE)
    elif isinstance(stmt, exp.Alter):
        if (stmt.args.get("kind") or "").upper() != "TABLE":
            raise ValidationError("Only ALTER TABLE is supported.")
        text = stmt.sql(dialect="postgres", comments=False)
        if _FORBIDDEN_ALTER.search(text):
            raise ValidationError("This ALTER TABLE operation is not permitted.")
        ops.add(Op.ALTER)
        if _DESTRUCTIVE_ALTER.search(text):
            dangerous.append("Destructive ALTER TABLE (drops, renames or retypes a column/constraint)")
    elif isinstance(stmt, exp.Drop):
        kind = (stmt.args.get("kind") or "").upper()
        if kind not in {"TABLE", "VIEW", "INDEX"}:
            raise ValidationError(f"DROP {kind or 'statement'} is not permitted.")
        ops.add(Op.DROP)
        dangerous.append(f"DROP {kind} permanently removes the object" + (" (CASCADE)" if stmt.args.get("cascade") else ""))
    elif isinstance(stmt, exp.TruncateTable):
        ops.add(Op.TRUNCATE)
        dangerous.append("TRUNCATE removes every row in the table")
    else:
        raise ValidationError("This type of SQL statement is not supported.")

    # ── nested data-modifying statements (data-modifying CTEs, subqueries)
    for node in stmt.walk():
        if node is stmt:
            continue
        if isinstance(node, exp.Insert):
            ops.add(Op.INSERT)
        elif isinstance(node, exp.Update):
            ops.add(Op.UPDATE)
        elif isinstance(node, exp.Delete):
            ops.add(Op.DELETE)
        elif (isinstance(stmt, (exp.Select, exp.SetOperation, exp.Subquery, exp.Insert, exp.Update, exp.Delete))
              and isinstance(node, (exp.Create, exp.Drop, exp.Alter, exp.TruncateTable, exp.Merge, exp.Command))):
            raise ValidationError("Nested DDL statements are not allowed.")

    # ── function deny-list
    for fname in _function_names(stmt):
        if not fname:
            continue
        if fname in BLOCKED_FUNCTIONS or (fname.startswith(BLOCKED_FUNCTION_PREFIXES) and fname not in ALLOWED_PG_FUNCTIONS):
            raise ValidationError(f"The function '{fname}' is not allowed.")

    # ── tables: schema / catalog restrictions, hidden & protected tables
    targets: list[str] = []
    reads: list[exp.Table] = []
    for t in stmt.find_all(exp.Table):
        name = _table_name(t)
        if not name:
            continue
        schema = (t.args.get("db").name.lower() if t.args.get("db") else "")
        if t.args.get("catalog") or schema not in ALLOWED_SCHEMAS:
            raise ValidationError("Access to other schemas or system catalogs is not allowed.")
        if name.startswith(("pg_", "information_schema")) or name.startswith("sql_"):
            raise ValidationError("Access to system catalogs is not allowed.")
        if not schema and name in ctes:
            continue
        reads.append(t)

    def first_table(node: exp.Expression | None) -> exp.Table | None:
        if node is None:
            return None
        return node if isinstance(node, exp.Table) else node.find(exp.Table)

    # statement-level write targets
    if isinstance(stmt, exp.Drop):
        for t in reads:                       # DROP may name several objects; every one is a target
            writes.append(_table_name(t)); targets.append(_table_name(t))
    elif isinstance(stmt, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Alter)):
        tt = first_table(stmt.this)
        if tt is not None:
            writes.append(_table_name(tt))
            targets.append(_table_name(tt))
    elif isinstance(stmt, exp.TruncateTable):
        for t in stmt.expressions:
            if isinstance(t, exp.Table):
                writes.append(_table_name(t)); targets.append(_table_name(t))
    # nested DML targets
    for node in stmt.walk():
        if node is not stmt and isinstance(node, (exp.Insert, exp.Update, exp.Delete)):
            tt = first_table(node.this)
            if tt is not None:
                writes.append(_table_name(tt)); targets.append(_table_name(tt))
    if not targets:
        for t in reads:
            targets.append(_table_name(t))
        targets = list(dict.fromkeys(targets))

    for t in reads:
        n = _table_name(t)
        if n in HIDDEN_TABLES:
            raise ValidationError("That table is not available.")
        if n in PROTECTED_TABLES and role != Role.DBA:
            raise ValidationError("That table is restricted to Database Administrators.")
    for w in writes:
        if w in HIDDEN_TABLES or w in PROTECTED_TABLES:
            raise ValidationError("That table cannot be modified through SQL.")
    # Safety net: a statement that changes anything may not mention a hidden/protected table at all.
    if ops - {Op.SELECT}:
        for t in reads:
            if _table_name(t) in HIDDEN_TABLES | PROTECTED_TABLES:
                raise ValidationError("That table cannot be modified through SQL.")

    # ── UPDATE / DELETE safety (including nested ones)
    for node in stmt.walk():
        if isinstance(node, (exp.Update, exp.Delete)) and _is_unsafe_where(node.args.get("where")):
            kind = "UPDATE" if isinstance(node, exp.Update) else "DELETE"
            dangerous.append(f"{kind} without a restrictive WHERE condition would affect every row")

    return _Stmt(sql=stmt.sql(dialect="postgres", comments=False), ops=ops, targets=list(dict.fromkeys(targets)),
                 reads=reads, writes=writes, dangerous=dangerous)


# ───────────────────────── public API ─────────────────────────
def validate_sql(sql: str, role: Role | None = None) -> ValidationResult:
    """Validate SQL and compute the RBAC decision for `role` (role=None → structural validation only)."""
    res = ValidationResult()
    text = (sql or "").strip()
    if not text:
        res.errors.append("The SQL is empty.")
        return res
    if len(text) > settings.max_sql_length:
        res.errors.append("The SQL is too long.")
        return res
    if "\x00" in text:
        res.errors.append("The SQL contains invalid characters.")
        return res

    try:
        parsed = [s for s in sqlglot.parse(text, read="postgres") if s is not None and not isinstance(s, exp.Semicolon)]
    except SqlglotError:
        res.errors.append("The SQL could not be parsed as valid PostgreSQL.")
        return res
    except Exception:  # noqa: BLE001 — parser crash must never become an execution path
        logging.getLogger("validator").exception("parser crashed")
        res.errors.append("The SQL could not be parsed as valid PostgreSQL.")
        return res

    if not parsed:
        res.errors.append("The SQL is empty.")
        return res
    if len(parsed) > settings.max_statements + 2:
        res.errors.append(f"Too many statements (maximum {settings.max_statements}).")
        return res

    # ── transaction envelope / multi-statement policy
    control = (exp.Transaction, exp.Commit, exp.Rollback)
    body = parsed
    if any(isinstance(st, control) for st in parsed):
        first, last = parsed[0], parsed[-1]
        if not (len(parsed) >= 3 and isinstance(first, exp.Transaction) and isinstance(last, (exp.Commit, exp.Rollback))):
            res.errors.append("Transaction statements must form a complete BEGIN ... COMMIT block.")
            return res
        body = parsed[1:-1]
        if any(isinstance(st, control) for st in body):
            res.errors.append("Nested or repeated transaction control statements are not allowed.")
            return res
        res.is_transaction = True
        res.ends_with_rollback = isinstance(last, exp.Rollback)
    elif len(parsed) > 1:
        # A "script": several independent statements. EVERY statement is validated below on its own,
        # the strictest RBAC decision applies to the whole script, and it runs as one atomic transaction.
        res.is_script = True
    if len(body) > settings.max_statements:
        res.errors.append(f"Too many statements (maximum {settings.max_statements}).")
        return res

    all_ops: set[Op] = set()
    try:
        analysed = [_analyse(s, role) for s in body]
    except ValidationError as exc:
        res.errors.append(str(exc))
        return res
    except Exception:  # noqa: BLE001
        logging.getLogger("validator").exception("validator crashed")
        res.errors.append("The SQL could not be validated safely.")
        return res

    for a in analysed:
        all_ops |= a.ops
        res.statements.append(a.sql)
        res.target_tables.extend(a.targets)
        res.danger_reasons.extend(a.dangerous)
    res.target_tables = list(dict.fromkeys(res.target_tables))
    res.danger_reasons = list(dict.fromkeys(res.danger_reasons))
    res.dangerous = bool(res.danger_reasons)
    res.operations = [o for o in _SEVERITY if o in all_ops]
    res.operation = Op.TRANSACTION if res.is_transaction else _headline(all_ops)
    res.read_only = all_ops == {Op.SELECT} and not res.is_transaction
    res.sql = ";\n".join(res.statements) + ";"
    if res.is_transaction or len(res.statements) >= 3:
        if not (res.sql.startswith("BEGIN;") and (res.sql.endswith("COMMIT;") or res.sql.endswith("ROLLBACK;"))):
            res.sql = "BEGIN;\n" + res.sql + ("\nROLLBACK;" if res.ends_with_rollback else "\nCOMMIT;")

    # ── RBAC decision
    if role is None:
        res.valid = True
        res.decision = Decision.ALLOW
        return res

    decisions = [decision_for(role, op) for op in all_ops]
    if res.is_transaction:
        decisions.append(decision_for(role, Op.TRANSACTION))
    # "Controlled" developer DDL: destructive ALTERs need DBA approval.
    if role == Role.DEVELOPER and Op.ALTER in all_ops and res.dangerous and any("ALTER" in r for r in res.danger_reasons):
        decisions.append(Decision.APPROVAL)
    res.decision = strictest(decisions)

    # Developers can never submit full-table UPDATE/DELETE (not even via approval).
    unsafe_write = any("without a restrictive WHERE" in r for r in res.danger_reasons)
    if role == Role.DEVELOPER and unsafe_write:
        res.errors.append("UPDATE and DELETE statements must include a restrictive WHERE condition.")
        res.decision = Decision.DENY
        return res

    if res.decision == Decision.DENY:
        res.permission_message = "You do not have permission to perform this operation."
        res.errors.append(res.permission_message)
        return res

    res.valid = True
    return res
