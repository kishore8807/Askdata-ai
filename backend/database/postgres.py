"""Safe PostgreSQL execution of *already validated* SQL.

Layers of protection at this level (in addition to the validator):
  * one pooled connection per request, never an ad-hoc connection
  * SELECT-only requests run in a READ ONLY transaction, so even a validator bug cannot write
  * statement_timeout / lock_timeout / idle_in_transaction_session_timeout on every execution
  * result size cap
  * rollback on any failure; commit only when every statement succeeded
  * technical details are logged, only a friendly message goes back to the user
"""
from __future__ import annotations

import datetime as dt
import decimal
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import errors as pgerr
from psycopg_pool import PoolTimeout

from ..config import settings
from ..database import exec_pool
from .sql_validator import ValidationResult

log = logging.getLogger("postgres")


class ExecutionError(Exception):
    """Raised with a message that is safe to show to the user."""

    def __init__(self, user_message: str, *, technical: str | None = None, connection_failed: bool = False):
        super().__init__(user_message)
        self.user_message = user_message
        self.technical = technical
        self.connection_failed = connection_failed


@dataclass
class StatementResult:
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    rowcount: int = 0
    truncated: bool = False
    command: str = ""

    @property
    def has_rows(self) -> bool:
        return bool(self.columns)


@dataclass
class ExecutionResult:
    results: list[StatementResult]
    committed: bool
    message: str

    @property
    def primary(self) -> StatementResult:
        for r in reversed(self.results):
            if r.has_rows:
                return r
        return self.results[-1] if self.results else StatementResult(sql="")

    def to_payload(self) -> dict[str, Any]:
        p = self.primary
        results = [{"index": i + 1, "sql": r.sql, "command": r.command, "columns": r.columns, "rows": r.rows,
                    "row_count": len(r.rows) if r.has_rows else r.rowcount, "truncated": r.truncated,
                    "has_rows": r.has_rows} for i, r in enumerate(self.results)]
        # A long batch of SELECTs with the same shape (e.g. one SELECT per row of an attached file)
        # is merged into ONE result set instead of hundreds of tabs.
        if len(results) > 8 and all(r["has_rows"] for r in results) and len({tuple(r["columns"]) for r in results}) == 1:
            rows = [row for r in results for row in r["rows"]]
            results = [{"index": 1, "sql": f"{len(results)} queries merged", "command": "SELECT", "columns": results[0]["columns"],
                        "rows": rows, "row_count": len(rows), "truncated": any(r["truncated"] for r in results), "has_rows": True}]
            p = StatementResult(sql="", columns=results[0]["columns"], rows=rows, rowcount=len(rows), truncated=results[0]["truncated"])
        return {
            "columns": p.columns, "rows": p.rows, "row_count": p.rowcount if not p.has_rows else len(p.rows),
            "truncated": p.truncated, "message": self.message, "committed": self.committed,
            "statement_count": len(self.results), "results": results,
        }


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v) if v.is_finite() else str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "<binary>"
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


def friendly_db_error(exc: psycopg.Error) -> str:
    """Map a PostgreSQL error to a short, safe message."""
    primary = ""
    try:
        primary = (exc.diag.message_primary or "")[:160]
    except Exception:  # noqa: BLE001
        pass
    if isinstance(exc, pgerr.QueryCanceled):
        return "The query took too long and was cancelled."
    if isinstance(exc, pgerr.ReadOnlySqlTransaction):
        return "This query attempted to change data but only reading is permitted."
    if isinstance(exc, pgerr.UniqueViolation):
        return "A record with the same unique value already exists."
    if isinstance(exc, pgerr.ForeignKeyViolation):
        return "The operation violates a relationship with another table (foreign key)."
    if isinstance(exc, pgerr.NotNullViolation):
        return "A required column was missing a value."
    if isinstance(exc, pgerr.CheckViolation):
        return "A value violates a table constraint."
    if isinstance(exc, (pgerr.UndefinedTable, pgerr.UndefinedColumn, pgerr.UndefinedFunction)):
        return f"The query refers to something that does not exist: {primary}" if primary else "The query refers to an unknown table or column."
    if isinstance(exc, pgerr.DuplicateTable):
        return "That table already exists."
    if isinstance(exc, pgerr.DuplicateColumn):
        return "That column already exists."
    if isinstance(exc, (pgerr.InvalidTextRepresentation, pgerr.DatatypeMismatch, pgerr.InvalidDatetimeFormat,
                        pgerr.NumericValueOutOfRange, pgerr.DivisionByZero, pgerr.AmbiguousColumn)):
        return f"The query could not be run: {primary}" if primary else "The query contains an invalid value."
    if isinstance(exc, pgerr.SyntaxError):
        return "PostgreSQL reported a syntax error in the query."
    if isinstance(exc, pgerr.InsufficientPrivilege):
        return "The database account does not have the privileges required for this operation."
    if isinstance(exc, pgerr.DependentObjectsStillExist):
        return "Other objects depend on this one; it cannot be dropped."
    return "The database rejected the operation."


def _command_label(cur: psycopg.Cursor) -> str:
    return (cur.statusmessage or "").strip()


def execute_validated(v: ValidationResult) -> ExecutionResult:
    """Execute a ValidationResult. The caller is responsible for authn/authz/approval checks."""
    if not v.valid or not v.statements:
        raise ExecutionError("The generated SQL failed validation.")
    max_rows = settings.max_result_rows
    budget = max_rows * 3                      # total rows returned across all statements of a script
    total = len(v.statements)
    current = {"i": 0}
    try:
        with exec_pool().connection() as conn:
            conn.read_only = v.read_only          # defence in depth for SELECT
            results: list[StatementResult] = []
            try:
                with conn.cursor() as ctl:
                    ctl.execute("SELECT set_config('statement_timeout', %s, true), "
                                "set_config('lock_timeout', '5000', true), "
                                "set_config('idle_in_transaction_session_timeout', '30000', true)",
                                (str(settings.statement_timeout_ms),))
                for n, stmt in enumerate(v.statements, start=1):
                    current["i"] = n
                    # SELECTs use a server-side cursor so a huge result never lands in our memory.
                    cm = conn.cursor(name="ro_cur") if v.read_only else conn.cursor()
                    with cm as cur:
                        cur.execute(stmt)  # type: ignore[arg-type]  # validated, normalised text; no client params
                        sr = StatementResult(sql=stmt, command=_command_label(cur))
                        if cur.description:
                            sr.columns = [d.name for d in cur.description]
                            cap = max(0, min(max_rows, budget))
                            fetched = cur.fetchmany(cap + 1)
                            sr.truncated = len(fetched) > cap
                            sr.rows = [[_jsonable(c) for c in row] for row in fetched[:cap]]
                            sr.rowcount = len(sr.rows)
                            budget -= len(sr.rows)
                        else:
                            sr.rowcount = max(cur.rowcount, 0)
                        results.append(sr)
                if v.ends_with_rollback:
                    conn.rollback()
                    committed = False
                else:
                    conn.commit()
                    committed = not v.read_only
            except BaseException:
                conn.rollback()
                raise
            finally:
                try:
                    conn.read_only = False
                except Exception:  # noqa: BLE001
                    pass
    except PoolTimeout as exc:
        raise ExecutionError("Database connection failed. Please try again later.",
                             technical=str(exc), connection_failed=True) from exc
    except psycopg.OperationalError as exc:
        if isinstance(exc, (pgerr.QueryCanceled, pgerr.LockNotAvailable)):
            raise ExecutionError("The query took too long or the data is locked by another operation.",
                                 technical=str(exc)) from exc
        log.exception("database operational error")
        raise ExecutionError("Database connection failed. Please try again later.",
                             technical=str(exc), connection_failed=True) from exc
    except psycopg.Error as exc:
        log.warning("sql execution failed (statement %s/%s): %s", current["i"], total, exc)
        msg = friendly_db_error(exc)
        if total > 1:
            msg = f"Query {current['i']} of {total} failed: {msg} No changes were saved."
        raise ExecutionError(msg, technical=str(exc)) from exc

    return ExecutionResult(results=results, committed=committed, message=_summary(v, results, committed))


def _summary(v: ValidationResult, results: list[StatementResult], committed: bool) -> str:
    n = len(results)
    if v.ends_with_rollback:
        return "Transaction rolled back as requested. No changes were saved."
    ops = {o.value for o in v.operations}
    affected = sum(r.rowcount for r in results if not r.has_rows)
    def plural(k: int, w: str) -> str:
        return f"{k} {w if k == 1 else ('queries' if w == 'query' else w + 's')}"
    if v.read_only:
        rows = sum(len(r.rows) for r in results)
        return (f"Query returned {plural(rows, 'row')}." if n == 1
                else f"{plural(n, 'query')} executed. {plural(rows, 'row')} returned.")
    if n > 1 and not v.is_transaction:
        extra = f" {plural(affected, 'row')} affected." if affected else ""
        return f"{plural(n, 'query')} executed successfully in one transaction.{extra}"
    if "UPDATE" in ops:
        return f"Update executed successfully. {plural(affected, 'row')} affected."
    if "DELETE" in ops:
        return f"Delete executed successfully. {plural(affected, 'row')} affected."
    if "INSERT" in ops:
        return f"Insert executed successfully. {plural(affected, 'row')} added."
    if ops & {"CREATE", "ALTER", "DROP", "TRUNCATE"}:
        return f"{v.operation.value if v.operation.value != 'TRANSACTION' else 'Transaction'} executed successfully."
    return "Executed successfully."
