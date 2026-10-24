from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .auth.authentication import get_current_user
from .database import app_conn
from .models import CurrentUser

router = APIRouter(prefix="/api/employees", tags=["employees"])


@router.get("")
def list_employees(search: str = Query(default="", max_length=100), limit: int = Query(default=100, ge=1, le=500),
                   offset: int = Query(default=0, ge=0), user: CurrentUser = Depends(get_current_user)):
    like = f"%{search.strip()}%"
    with app_conn() as conn:
        rows = conn.execute(
            """SELECT employee_code, name, email, department, designation, salary, joining_date, status
                 FROM employees
                WHERE %s = '' OR name ILIKE %s OR employee_code ILIKE %s OR email ILIKE %s OR department ILIKE %s
                ORDER BY employee_code LIMIT %s OFFSET %s""",
            (search.strip(), like, like, like, like, limit, offset)).fetchall()
        total = conn.execute("SELECT count(*) AS c FROM employees").fetchone()["c"]
    return {"success": True, "total": total, "employees": rows}
