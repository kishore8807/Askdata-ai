"""Prompt construction + extraction of SQL from the model's reply."""
from __future__ import annotations

import re

SYSTEM_PROMPT = """You are an AI database SQL assistant.

Your task is to convert the user's natural-language request into PostgreSQL SQL.

Rules:
1. Use only the provided database schema.
2. Do not invent tables or columns.
3. Generate valid PostgreSQL SQL.
4. Never execute SQL.
5. Return SQL only when SQL generation is requested.
6. Do not include explanations inside the SQL.
7. Never assume missing values.
8. Preserve user intent.
9. Use safe WHERE conditions for UPDATE and DELETE.
10. If the request is ambiguous, ask for clarification.
11. Never generate credentials or secrets.
12. Never bypass database permissions.

Additional rules:
13. If the user asks for several separate things or 3 or more statements, wrap them in a BEGIN; ... COMMIT; block.
    They will run together as one all-or-nothing transaction.
14. If the user attached a file (described under ATTACHED FILE in the request) and the request is about that file:
    a) Write ONE simple SQL statement for ONE row (e.g. INSERT INTO employees (...) VALUES ({{col1}}, {{col2}}...); or UPDATE employees SET ... WHERE ...;).
    b) Put {{column_name}} wherever a value from the file belongs, using only the exact column names listed for the file.
    c) NEVER use psql commands like \\echo, \\set, or \\connect.
    d) NEVER use the file name as a table in a FROM clause (e.g. NEVER write FROM "file.xlsx").
    e) NEVER wrap placeholders in extra quotes or create WITH/CTE wrappers around the file.
    f) The backend automatically expands your single-row template once for every row in the file.

Output format:
- When the request is clear, reply with the SQL inside a ```sql code block and nothing else.
  (Wrap in BEGIN; ... COMMIT; whenever 3 or more statements are present, or when explicitly requested.)
- When the request is ambiguous or required values are missing, reply with a single line starting with
  CLARIFY: followed by a short question.
- When the request cannot be answered with the provided schema, reply with a single line starting with
  CANNOT: followed by a short reason.

Conventions:
- Match text values exactly as listed in the VALUES hints when they exist.
- Identify a single employee with employee_code when the user supplies a code; otherwise filter by name using ILIKE.
- Dates use ISO format 'YYYY-MM-DD'."""

EXAMPLES = """Examples:
Request: Show all employees in the Development department
```sql
SELECT * FROM employees WHERE department = 'Development';
```
Request: Change employee EMP001 salary to 60000
```sql
UPDATE employees SET salary = 60000 WHERE employee_code = 'EMP001';
```
Request: How many employees are in each department with more than 5 people
```sql
SELECT department, COUNT(*) AS employee_count FROM employees GROUP BY department HAVING COUNT(*) > 5;
```
Request: Show all departments and count the employees
```sql
SELECT * FROM departments;
SELECT COUNT(*) AS employee_count FROM employees;
```
Request: Change the salary
CLARIFY: Which employee, and what should the new salary be?"""

ATTACH_EXAMPLES = """Examples with an attached file (columns: employee_code, name, email, department, designation, salary, joining_date, status):
Request: Update the salary of the employees in the file
```sql
UPDATE employees SET salary = {{salary}} WHERE employee_code = {{employee_code}};
```
Request: Remove the employees listed in the file
```sql
DELETE FROM employees WHERE employee_code = {{employee_code}};
```
Request: Add the employees in the file
```sql
INSERT INTO employees (name, email, department, designation, salary, joining_date, status) VALUES ({{name}}, {{email}}, {{department}}, {{designation}}, {{salary}}, {{joining_date}}, {{status}});
```
Request: Deactivate the employees listed in the file
```sql
UPDATE employees SET status = 'Inactive' WHERE email = {{email}};
```"""


def attachment_block(att: dict) -> str:
    cols = att["columns"]
    placeholders = ", ".join(f"{{{{{c}}}}}" for c in cols)
    lines = [
        f'ATTACHED FILE "{att["filename"]}": {len(att["rows"])} data rows.',
        f'Columns in attached file: {", ".join(cols)}',
        f'Required placeholders for file columns: {placeholders}',
        'Sample rows from attached file:'
    ]
    for r in att["rows"][:2]:
        lines.append(" | ".join(f"{c}={str(r[c])[:60]}" for c in cols))
    lines.append(f'CRITICAL INSTRUCTION FOR ATTACHED FILE: You MUST write EXACTLY ONE single-row SQL template statement using double-curly placeholders (e.g. {placeholders}). NEVER copy literal values from the sample rows. NEVER output multiple statements.')
    return "\n".join(lines)


def build_user_prompt(question: str, schema_text: str, role_label: str, attachment: dict | None = None) -> str:
    parts = [f"Database schema (PostgreSQL):\n{schema_text}", EXAMPLES]
    if attachment:
        parts += [ATTACH_EXAMPLES, attachment_block(attachment)]
    parts.append(f"The requester has the role: {role_label}.\nRequest: {question.strip()}\n")
    return "\n\n".join(parts)


_FENCE = re.compile(r"```(?:sql|postgresql|pgsql)?\s*(.*?)```", re.S | re.I)


def parse_model_reply(reply: str) -> dict:
    """Returns {'kind': 'sql'|'clarify'|'cannot'|'empty', 'sql'|'message': ...}."""
    text = (reply or "").strip()
    if not text:
        return {"kind": "empty"}
    m = re.search(r"^\s*(CLARIFY|CANNOT)\s*:\s*(.+)$", text, re.I | re.M)
    if m and "```" not in text:
        return {"kind": "clarify" if m.group(1).upper() == "CLARIFY" else "cannot", "message": m.group(2).strip()}
    f = _FENCE.search(text)
    sql = f.group(1).strip() if f else text
    # drop leading prose if the model ignored the format and no fence was used
    if not f:
        idx = re.search(r"\b(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|BEGIN)\b", sql, re.I)
        if not idx:
            return {"kind": "clarify", "message": sql[:300]}
        sql = sql[idx.start():]
    sql = sql.strip().strip("`").strip()

    # Strip psql meta-commands (like \echo, \set, \connect)
    clean_lines = [line for line in sql.splitlines() if not line.strip().startswith("\\")]
    sql = "\n".join(clean_lines).strip()

    if not sql.endswith(";"):
        sql += ";"
    return {"kind": "sql", "sql": sql}
