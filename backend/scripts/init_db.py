"""Initialise the PostgreSQL database.

    python -m backend.scripts.init_db                      # schema only
    python -m backend.scripts.init_db --seed               # + sample departments / employees / projects / tasks
    python -m backend.scripts.init_db --seed --least-privilege   # + dedicated low-privilege SQL execution role

Idempotent: safe to run repeatedly.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from ..config import settings

ROOT = Path(__file__).resolve().parents[2]


def apply_schema(conn: psycopg.Connection, seed: bool) -> None:
    conn.execute("SET client_encoding TO 'UTF8';")
    conn.execute((ROOT / "database" / "schema.sql").read_text(encoding="utf-8"))
    print("  [OK] schema applied")
    if seed:
        conn.execute((ROOT / "database" / "seed.sql").read_text(encoding="utf-8"))
        print("  [OK] sample data loaded")


def apply_least_privilege(conn: psycopg.Connection) -> None:
    role, pw = settings.exec_user, settings.exec_password
    if not role or not pw:
        raise SystemExit("Set POSTGRES_EXEC_USER and POSTGRES_EXEC_PASSWORD in .env first.")
    ident = sql.Identifier(role)
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    stmt = "ALTER ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE" if exists else \
           "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
    conn.execute(sql.SQL(stmt).format(ident, sql.Literal(pw)))
    script = (ROOT / "database" / "least_privilege.sql").read_text()
    # run each statement with the identifiers substituted safely
    for raw in [s.strip() for s in script.split(";") if s.strip() and not all(l.strip().startswith("--") or not l.strip() for l in s.splitlines())]:
        body = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--"))
        conn.execute(sql.SQL(body.replace(":exec_role", "{role}").replace(":db_name", "{db}")).format(
            role=ident, db=sql.Identifier(settings.postgres_db)))
    print(f"  [OK] least-privilege execution role '{role}' configured")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", action="store_true", help="load sample company data")
    ap.add_argument("--least-privilege", action="store_true", help="create the restricted SQL execution role")
    args = ap.parse_args(argv)
    try:
        with psycopg.connect(settings.conninfo, autocommit=True) as conn:
            print(f"Initialising database '{settings.postgres_db}' on {settings.postgres_host}:{settings.postgres_port}")
            apply_schema(conn, args.seed)
            if args.least_privilege:
                apply_least_privilege(conn)
    except psycopg.OperationalError as exc:
        print(f"✗ Could not connect to PostgreSQL: {exc}", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
