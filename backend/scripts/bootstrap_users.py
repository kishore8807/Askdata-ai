"""Create (or reset) the team accounts.  No passwords live in code or SQL files.

Interactive:   python -m backend.scripts.bootstrap_users
Scripted:      DBA_PASSWORD=... DEVELOPER_PASSWORD=... ANALYST_PASSWORD=... python -m backend.scripts.bootstrap_users
Add one user:  python -m backend.scripts.bootstrap_users --add alice alice@corp.com DEVELOPER
"""
from __future__ import annotations

import getpass
import os
import sys

from ..auth.authentication import MIN_PASSWORD_LEN, hash_password
from ..database import app_conn, close_pools
from ..models import Role

DEFAULTS = [("dba", "dba@company.local", Role.DBA),
            ("developer", "developer@company.local", Role.DEVELOPER),
            ("analyst", "analyst@company.local", Role.ANALYST)]


def upsert(username: str, email: str, role: Role, password: str) -> None:
    with app_conn() as conn:
        conn.execute(
            """INSERT INTO users (username, email, password_hash, role) VALUES (%s,%s,%s,%s)
               ON CONFLICT (username) DO UPDATE
                 SET email = EXCLUDED.email, password_hash = EXCLUDED.password_hash,
                     role = EXCLUDED.role, is_active = TRUE, updated_at = NOW()""",
            (username.lower(), email.lower(), hash_password(password), role.value))
    print(f"  ✓ {username} ({role.value})")


def _password(env: str, label: str) -> str:
    pw = os.getenv(env)
    if pw:
        return pw
    while True:
        pw = getpass.getpass(f"Password for {label} (min {MIN_PASSWORD_LEN} chars): ")
        if len(pw) >= MIN_PASSWORD_LEN and pw == getpass.getpass("Repeat: "):
            return pw
        print("  Too short or not matching, try again.")


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["--add"]:
            _, username, email, role = argv
            upsert(username, email, Role(role.upper()), _password("NEW_USER_PASSWORD", username))
        else:
            print("Creating the three team accounts:")
            for username, email, role in DEFAULTS:
                upsert(username, email, role, _password(f"{role.value}_PASSWORD", username))
    finally:
        close_pools()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
