"""Schema retrieval: gives the SLM only the *relevant* slice of the live database schema."""
from __future__ import annotations

import re
import time
from typing import Any

from ..database import app_conn
from ..models import HIDDEN_TABLES, PROTECTED_TABLES, Role

_CACHE: dict[str, Any] = {"at": 0.0, "schema": None}
_TTL = 30  # seconds
MAX_TABLES_IN_PROMPT = 6
MAX_ENUM_VALUES = 12


def _load_schema() -> dict[str, dict[str, Any]]:
    with app_conn() as conn:
        cols = conn.execute(
            """SELECT table_name, column_name, data_type, character_maximum_length AS maxlen,
                      is_nullable, column_default
                 FROM information_schema.columns
                WHERE table_schema = 'public' ORDER BY table_name, ordinal_position""").fetchall()
        constraints = conn.execute(
            """SELECT tc.table_name, tc.constraint_type, tc.constraint_name, kcu.column_name,
                      ccu.table_name AS ref_table, ccu.column_name AS ref_column
                 FROM information_schema.table_constraints tc
                 JOIN information_schema.key_column_usage kcu
                   ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
                 LEFT JOIN information_schema.constraint_column_usage ccu
                   ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
                      AND tc.constraint_type = 'FOREIGN KEY'
                WHERE tc.table_schema='public' AND tc.constraint_type IN ('PRIMARY KEY','FOREIGN KEY','UNIQUE')"""
        ).fetchall()
        checks = conn.execute(
            """SELECT cl.relname AS table_name, pg_get_constraintdef(c.oid) AS definition
                 FROM pg_constraint c JOIN pg_class cl ON cl.oid = c.conrelid
                 JOIN pg_namespace n ON n.oid = cl.relnamespace
                WHERE c.contype='c' AND n.nspname='public'""").fetchall()
        views = {r["table_name"] for r in conn.execute(
            "SELECT table_name FROM information_schema.views WHERE table_schema='public'").fetchall()}

    schema: dict[str, dict[str, Any]] = {}
    for c in cols:
        t = schema.setdefault(c["table_name"], {"columns": [], "pk": [], "fks": [], "unique": [], "checks": [],
                                                 "is_view": c["table_name"] in views})
        typ = c["data_type"] + (f"({c['maxlen']})" if c["maxlen"] else "")
        t["columns"].append({"name": c["column_name"], "type": typ, "nullable": c["is_nullable"] == "YES"})
    for k in constraints:
        t = schema.get(k["table_name"])
        if not t:
            continue
        if k["constraint_type"] == "PRIMARY KEY":
            t["pk"].append(k["column_name"])
        elif k["constraint_type"] == "FOREIGN KEY":
            fk = (k["column_name"], k["ref_table"], k["ref_column"])
            if fk not in t["fks"]:
                t["fks"].append(fk)
        elif k["constraint_type"] == "UNIQUE" and k["column_name"] not in t["unique"]:
            t["unique"].append(k["column_name"])
    for ch in checks:
        if ch["table_name"] in schema:
            schema[ch["table_name"]]["checks"].append(ch["definition"])
    return schema


def get_schema(force: bool = False) -> dict[str, dict[str, Any]]:
    if force or _CACHE["schema"] is None or time.time() - _CACHE["at"] > _TTL:
        _CACHE["schema"] = _load_schema()
        _CACHE["at"] = time.time()
    return _CACHE["schema"]


def invalidate() -> None:
    _CACHE["at"] = 0.0
    _CACHE["schema"] = None


def visible_schema(role: Role) -> dict[str, dict[str, Any]]:
    """Schema the role may see. Credential/internal tables are never exposed; audit tables are DBA-only."""
    out = {}
    for name, t in get_schema().items():
        if name in HIDDEN_TABLES:
            continue
        if name in PROTECTED_TABLES and role != Role.DBA:
            continue
        out[name] = t
    return out


# ───────── relevance ─────────
def _stems(word: str) -> set[str]:
    w = word.lower()
    forms = {w}
    if w.endswith("ies"):
        forms.add(w[:-3] + "y")
    if w.endswith("es"):
        forms.add(w[:-2])
    if w.endswith("s"):
        forms.add(w[:-1])
    forms.add(w + "s")
    return forms


def select_relevant(question: str, schema: dict[str, dict[str, Any]]) -> list[str]:
    words = {w for w in re.findall(r"[a-zA-Z_]+", question.lower()) if len(w) > 2}
    stems: set[str] = set()
    for w in words:
        stems |= _stems(w)
    scores: dict[str, int] = {}
    for name, t in schema.items():
        s = 0
        parts = set(name.lower().split("_")) | {name.lower()}
        if parts & stems:
            s += 5
        for c in t["columns"]:
            cname = c["name"].lower()
            if cname in stems or (set(cname.split("_")) & stems and cname not in ("id", "name", "status", "created_at")):
                s += 1
            elif cname in ("name", "status") and cname in words:
                s += 1
        if s:
            scores[name] = s
    chosen = sorted(scores, key=lambda n: (-scores[n], n))[:MAX_TABLES_IN_PROMPT]
    # pull in FK neighbours of the top hits so JOINs are possible
    for name in list(chosen[:2]):
        for _, ref, _ in schema[name]["fks"]:
            if ref in schema and ref not in chosen and len(chosen) < MAX_TABLES_IN_PROMPT:
                chosen.append(ref)
    if not chosen:  # nothing matched — fall back to the (small) business schema
        chosen = sorted(schema)[:MAX_TABLES_IN_PROMPT]
    return chosen


def _enum_values(table: str, column: str) -> list[str]:
    """Small distinct value lists help the model write correct literals (e.g. department = 'Development')."""
    try:
        with app_conn() as conn:
            rows = conn.execute(
                f'SELECT DISTINCT "{column}" AS v FROM "{table}" WHERE "{column}" IS NOT NULL ORDER BY 1 LIMIT %s',
                (MAX_ENUM_VALUES + 1,)).fetchall()
        vals = [str(r["v"]) for r in rows]
        return vals if len(vals) <= MAX_ENUM_VALUES else []
    except Exception:  # noqa: BLE001
        return []


def build_schema_prompt(question: str, role: Role) -> tuple[str, list[str]]:
    """Return (schema text for the prompt, table names included)."""
    schema = visible_schema(role)
    tables = select_relevant(question, schema)
    lines: list[str] = []
    for name in tables:
        t = schema[name]
        cols = []
        for c in t["columns"]:
            flag = " PK" if c["name"] in t["pk"] else ""
            cols.append(f"{c['name']} {c['type']}{flag}")
        lines.append(f"TABLE {name} ({', '.join(cols)})")
        for col, ref_t, ref_c in t["fks"]:
            lines.append(f"  FOREIGN KEY {name}.{col} -> {ref_t}.{ref_c}")
        for chk in t["checks"][:4]:
            lines.append(f"  CHECK {chk}")
        for c in t["columns"]:
            if c["type"].startswith(("character varying", "text")) and c["name"] in (
                    "department", "status", "designation", "priority", "department_name"):
                vals = _enum_values(name, c["name"])
                if vals:
                    lines.append(f"  VALUES {name}.{c['name']}: " + ", ".join(f"'{v}'" for v in vals))
    return "\n".join(lines), tables


def schema_for_ui(role: Role) -> list[dict[str, Any]]:
    out = []
    for name, t in sorted(visible_schema(role).items()):
        out.append({"table": name, "is_view": t["is_view"], "primary_key": t["pk"],
                    "columns": t["columns"], "foreign_keys": [
                        {"column": c, "references": f"{r}.{rc}"} for c, r, rc in t["fks"]]})
    return out
