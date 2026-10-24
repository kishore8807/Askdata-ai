"""Per-row expansion of an AI-written SQL *template* for an attached file.

Why a template?  A 3B model cannot reliably write hundreds of statements, and file values must never be
trusted as SQL.  So the model writes ONE statement for ONE row using {{column}} placeholders, and this module
expands it for every row, rendering each value as a properly escaped SQL literal.  The expanded script then goes
through the normal validator → RBAC → (DBA approval) → execution pipeline, like any other SQL.
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..config import settings

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_DQ_PLACEHOLDER = re.compile(r'"\s*(\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\})\s*"')
_STRING_SPLIT = re.compile(r"('(?:[^']|'')*')")


class BatchError(Exception):
    """Message is safe to show to the user."""


def has_placeholders(sql: str) -> bool:
    return bool(PLACEHOLDER.search(sql or ""))


def used_columns(sql: str) -> list[str]:
    return list(dict.fromkeys(m.group(1).lower() for m in PLACEHOLDER.finditer(sql)))


def sql_literal(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v) if math.isfinite(v) else "NULL"
    return "'" + str(v).replace("\x00", "").replace("'", "''") + "'"


def _text(v: Any) -> str:
    """Value for use *inside* an existing quoted string, e.g. LIKE '%{{name}}%'."""
    if v is None:
        return ""
    return str(v).replace("\x00", "").replace("'", "''")


def resolve_column(name: str, available_columns: list[str]) -> str | None:
    target = name.lower().strip()
    # 1. Exact match
    for col in available_columns:
        if col.lower() == target:
            return col
    # 2. Normalized match (without non-alphanumeric)
    norm_target = re.sub(r'[^a-z0-9]', '', target)
    for col in available_columns:
        if re.sub(r'[^a-z0-9]', '', col.lower()) == norm_target:
            return col
    # 3. Common aliases
    aliases = {
        'employee_code': ['emp_code', 'employee_code', 'emp_id', 'employee_id', 'code', 'id', 'emp_no', 'user_id'],
        'emp_code': ['emp_code', 'employee_code', 'emp_id', 'employee_id', 'code', 'id', 'emp_no', 'user_id'],
        'salary': ['salary', 'pay', 'amount', 'new_salary', 'new_pay', 'sal', 'compensation'],
        'department': ['department', 'dept', 'dept_name', 'department_name'],
        'email': ['email', 'email_address', 'mail'],
        'name': ['name', 'employee_name', 'full_name'],
        'status': ['status', 'state', 'active_status', 'emp_status', 'joined_status'],
    }
    for col in available_columns:
        c_norm = re.sub(r'[^a-z0-9]', '', col.lower())
        for key, alt_list in aliases.items():
            if target in alt_list and c_norm in [re.sub(r'[^a-z0-9]', '', a) for a in alt_list]:
                return col
    # 4. Substring match if unique
    matches = [c for c in available_columns if norm_target and (norm_target in re.sub(r'[^a-z0-9]', '', c.lower()) or re.sub(r'[^a-z0-9]', '', c.lower()) in norm_target)]
    if len(matches) == 1:
        return matches[0]
    return None


def _render(template: str, row: dict[str, Any], column_map: dict[str, str]) -> str:
    parts = _STRING_SPLIT.split(template)
    for i, seg in enumerate(parts):
        if i % 2:      # inside a '...' string literal → substitute raw (escaped) text, no extra quotes
            parts[i] = PLACEHOLDER.sub(lambda m: _text(row[column_map.get(m.group(1).lower(), m.group(1).lower())]), seg)
        else:          # code → SQL literal; the model often wraps placeholders in "..." by mistake, so unwrap those
            seg = _DQ_PLACEHOLDER.sub(r"\1", seg)
            parts[i] = PLACEHOLDER.sub(lambda m: sql_literal(row[column_map.get(m.group(1).lower(), m.group(1).lower())]), seg)
    return "".join(parts)


def clean_template(template: str) -> str:
    """Clean LLM output template for attached files.
    - Remove transaction control statements (BEGIN/COMMIT/ROLLBACK)
    - Convert SELECT {{col}}... FROM read_file(...) or FROM "file.xlsx" into VALUES ({{col}}...)
    """
    t = template.strip()
    lines = [line.strip() for line in t.splitlines() if line.strip() and line.strip().rstrip(";").upper() not in ("BEGIN", "COMMIT", "ROLLBACK")]
    t = "\n".join(lines).strip()

    # Strip read_file('...'), read_csv('...'), or 'filename.xlsx' / "filename.xlsx" FROM clauses only
    t = re.sub(r"\s+FROM\s+(?:read_file|read_csv)(?:\([^\)]*\))?", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+FROM\s+(?:\"[^\"]+\.(?:xlsx|xls|csv)\"|'[^']+\.(?:xlsx|xls|csv)')", "", t, flags=re.IGNORECASE)

    # Convert INSERT INTO table (cols) SELECT {{col1}}, {{col2}}... into INSERT INTO table (cols) VALUES ({{col1}}, {{col2}}...)
    t = re.sub(
        r"INSERT\s+INTO\s+([A-Za-z0-9_.\"]+)\s*(\([^\)]+\))\s+SELECT\s+((?:\{\{\s*[A-Za-z0-9_]+\s*\}\}\s*,?\s*)+)",
        r"INSERT INTO \1 \2 VALUES (\3)",
        t,
        flags=re.IGNORECASE
    )
    # Strip any ::type casts inside placeholders if present (e.g. {{col}}::varchar -> {{col}})
    t = re.sub(r"(\{\{\s*[A-Za-z0-9_]+\s*\}\})::[A-Za-z0-9_\s]+", r"\1", t, flags=re.IGNORECASE)
    return t


def expand(template: str, columns: list[str], rows: list[dict[str, Any]]) -> str:
    """Return one SQL script: the template rendered once per file row."""
    template = clean_template(template)
    if not template.endswith(";"):
        template += ";"
    cols = used_columns(template)
    column_map = {}
    unknown = []
    for c in cols:
        resolved = resolve_column(c, columns)
        if resolved:
            column_map[c] = resolved
        else:
            unknown.append(c)
    if unknown:
        raise BatchError("The generated SQL uses columns that are not in the attached file: " + ", ".join(unknown)
                         + ". Available columns: " + ", ".join(columns) + ".")
    if not rows:
        raise BatchError("The attached file has no rows.")
    per_row = max(1, template.count(";"))
    if per_row * len(rows) > settings.max_statements:
        raise BatchError(f"This would run {per_row * len(rows)} queries (maximum {settings.max_statements}). "
                         "Attach a smaller file.")
    return "\n".join(_render(template, r, column_map) for r in rows)


def try_convert_to_template(sql: str, available_columns: list[str]) -> str | None:
    """If the model wrote literal SQL statements instead of {{placeholders}}, convert statement 1 to a template."""
    import sqlglot
    from sqlglot import exp
    try:
        stmts = [s for s in sqlglot.parse(sql, read="postgres") if s is not None and not isinstance(s, exp.Semicolon)]
        if not stmts:
            return None
        first = stmts[0]
        if isinstance(first, exp.Insert):
            table = first.this.this.name if hasattr(first.this, "this") and first.this.this else (first.this.name if first.this else "employees")
            cols = []
            if isinstance(first.this, exp.Schema):
                cols = [c.name for c in first.this.expressions]
            if not cols:
                cols = available_columns
            matched_cols, placeholders = [], []
            for c in cols:
                res = resolve_column(c, available_columns)
                if res:
                    matched_cols.append(res)
                    placeholders.append(f"{{{{{res}}}}}")
            if matched_cols:
                return f"INSERT INTO {table} ({', '.join(matched_cols)}) VALUES ({', '.join(placeholders)});"
        elif isinstance(first, exp.Update):
            table = first.this.name if first.this else "employees"
            set_parts = []
            for eq in first.expressions:
                if isinstance(eq, exp.EQ):
                    col = eq.this.name
                    res = resolve_column(col, available_columns)
                    if res:
                        set_parts.append(f"{col} = {{{{{res}}}}}")
            where_clause = ""
            where_node = first.args.get("where")
            if where_node:
                for eq in where_node.find_all(exp.EQ):
                    col = eq.this.name
                    res = resolve_column(col, available_columns)
                    if res:
                        where_clause = f" WHERE {col} = {{{{{res}}}}}"
                        break
            if set_parts and where_clause:
                return f"UPDATE {table} SET {', '.join(set_parts)}{where_clause};"
    except Exception:  # noqa: BLE001
        pass
    return None


