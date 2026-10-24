"""RBAC permission matrix. Configurable via PERMISSIONS_FILE (JSON); the backend is the only authority."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import settings
from ..models import Decision, Op, Role

log = logging.getLogger("rbac")

A, P, D = Decision.ALLOW, Decision.APPROVAL, Decision.DENY

# Default matrix (mirrors the specification).
# Developer CREATE / ALTER / TRANSACTION are "controlled": allowed, but the SQL validator additionally
# blocks protected system tables and validates every statement inside a transaction.
DEFAULT_MATRIX: dict[Role, dict[Op, Decision]] = {
    Role.DBA: {op: A for op in (Op.SELECT, Op.INSERT, Op.UPDATE, Op.DELETE, Op.CREATE, Op.ALTER,
                                Op.DROP, Op.TRUNCATE, Op.TRANSACTION)},
    Role.DEVELOPER: {
        Op.SELECT: A, Op.INSERT: A, Op.UPDATE: P, Op.DELETE: P,
        Op.CREATE: A, Op.ALTER: A, Op.DROP: P, Op.TRUNCATE: P, Op.TRANSACTION: A,
    },
    Role.ANALYST: {
        Op.SELECT: A, Op.INSERT: D, Op.UPDATE: D, Op.DELETE: D,
        Op.CREATE: D, Op.ALTER: D, Op.DROP: D, Op.TRUNCATE: D, Op.TRANSACTION: D,
    },
}

_matrix: dict[Role, dict[Op, Decision]] | None = None


def _load() -> dict[Role, dict[Op, Decision]]:
    matrix = {r: dict(ops) for r, ops in DEFAULT_MATRIX.items()}
    if settings.permissions_file:
        try:
            override = json.loads(Path(settings.permissions_file).read_text())
            for role_name, ops in override.items():
                for op_name, dec in ops.items():
                    matrix[Role(role_name.upper())][Op(op_name.upper())] = Decision(dec.upper())
            log.info("permission overrides loaded from %s", settings.permissions_file)
        except Exception:  # noqa: BLE001 — a broken override must never widen access
            log.exception("invalid PERMISSIONS_FILE; using the built-in matrix")
            matrix = {r: dict(ops) for r, ops in DEFAULT_MATRIX.items()}
    # Hard invariants that configuration can never relax
    matrix[Role.ANALYST] = {op: (A if op == Op.SELECT else D) for op in Op}
    for op in Op:
        matrix[Role.DBA].setdefault(op, D)
    return matrix


def get_matrix() -> dict[Role, dict[Op, Decision]]:
    global _matrix
    if _matrix is None:
        _matrix = _load()
    return _matrix


def reset_matrix() -> None:
    global _matrix
    _matrix = None


def decision_for(role: Role, op: Op) -> Decision:
    return get_matrix().get(role, {}).get(op, Decision.DENY)


_STRICTNESS = {Decision.ALLOW: 0, Decision.APPROVAL: 1, Decision.DENY: 2}


def strictest(decisions: list[Decision]) -> Decision:
    return max(decisions, key=lambda d: _STRICTNESS[d]) if decisions else Decision.DENY


def matrix_for_display() -> dict[str, dict[str, str]]:
    return {r.value: {o.value: d.value for o, d in ops.items() if o != Op.UNKNOWN}
            for r, ops in get_matrix().items()}
