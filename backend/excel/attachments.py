"""Server-side storage of attached files. The browser only ever holds an id; the data never round-trips."""
from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from ..database import app_conn

COMMENT = "Server-side storage of files attached to the AI Query Studio (one active attachment per user)."


def save(user_id: int, filename: str, parsed: dict[str, Any]) -> str:
    att_id = str(uuid.uuid4())
    summary = {"columns": parsed["columns"], "row_count": len(parsed["rows"])}
    with app_conn() as conn:
        conn.execute("DELETE FROM import_staging WHERE user_id = %s OR created_at < NOW() - INTERVAL '1 day'", (user_id,))
        conn.execute("INSERT INTO import_staging (id, user_id, filename, rows, summary) VALUES (%s,%s,%s,%s,%s)",
                     (att_id, user_id, filename[:255], Jsonb(parsed["rows"]), Jsonb(summary)))
    return att_id


def load(user_id: int, att_id: str) -> dict[str, Any] | None:
    try:
        uuid.UUID(att_id)
    except (ValueError, AttributeError, TypeError):
        return None
    with app_conn() as conn:
        row = conn.execute("SELECT filename, rows, summary FROM import_staging WHERE id=%s AND user_id=%s",
                           (att_id, user_id)).fetchone()
    if not row:
        return None
    return {"id": att_id, "filename": row["filename"], "columns": row["summary"]["columns"], "rows": row["rows"]}


def delete(user_id: int, att_id: str) -> bool:
    try:
        uuid.UUID(att_id)
    except (ValueError, AttributeError, TypeError):
        return False
    with app_conn() as conn:
        cur = conn.execute("DELETE FROM import_staging WHERE id=%s AND user_id=%s", (att_id, user_id))
        return cur.rowcount > 0
