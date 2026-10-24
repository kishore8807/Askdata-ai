"""Download of query results as CSV or Excel (built server-side, formula-injection safe, audited)."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, Field

from .audit.logger import log_event
from .auth.authentication import get_current_user
from .config import settings
from .models import AuditAction, CurrentUser

router = APIRouter(prefix="/api", tags=["export"])

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


class Sheet(BaseModel):
    name: str = Field(default="Result", max_length=100)
    columns: list[str] = Field(min_length=1, max_length=200)
    rows: list[list[Any]] = Field(default_factory=list)


class ExportRequest(BaseModel):
    format: Literal["csv", "xlsx"]
    sheets: list[Sheet] = Field(min_length=1, max_length=20)


def _scalar(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return str(v)[:32000]


def _csv_cell(v: Any) -> Any:
    v = _scalar(v)
    if isinstance(v, str) and v.startswith(_FORMULA_START):
        return "'" + v                       # stops Excel from running it as a formula
    return "" if v is None else v


def _sheet_name(raw: str, used: set[str], idx: int) -> str:
    base = _BAD_SHEET_CHARS.sub(" ", raw).strip().strip("'")[:28] or f"Result {idx}"
    name, n = base, 2
    while name.lower() in used:
        name = f"{base[:25]} {n}"; n += 1
    used.add(name.lower())
    return name


@router.post("/export")
def export(body: ExportRequest, user: CurrentUser = Depends(get_current_user)):
    total = sum(len(s.rows) for s in body.sheets)
    if total > settings.max_export_rows:
        raise HTTPException(413, f"Too many rows to export (maximum {settings.max_export_rows}).")
    for s in body.sheets:
        if any(len(r) != len(s.columns) for r in s.rows):
            raise HTTPException(422, "The result data is malformed.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if body.format == "csv":
        if len(body.sheets) != 1:
            raise HTTPException(422, "CSV export supports one result at a time.")
        s = body.sheets[0]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([_csv_cell(c) for c in s.columns])
        for r in s.rows:
            w.writerow([_csv_cell(c) for c in r])
        payload = ("\ufeff" + buf.getvalue()).encode("utf-8")          # BOM so Excel reads UTF-8 correctly
        media, fname = "text/csv; charset=utf-8", f"query_results_{stamp}.csv"
    else:
        wb = Workbook()
        wb.remove(wb.active)
        used: set[str] = set()
        for i, s in enumerate(body.sheets, start=1):
            ws = wb.create_sheet(_sheet_name(s.name, used, i))
            ws.append([str(c)[:255] for c in s.columns])
            for cell in ws[1]:
                cell.font = Font(bold=True)
            ws.freeze_panes = "A2"
            for r in s.rows:
                ws.append([_scalar(c) for c in r])
            for row in ws.iter_rows(min_row=1):          # force text cells to stay text (never formulas)
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
            for idx, col in enumerate(s.columns, start=1):
                longest = max([len(str(col))] + [len(str(r[idx - 1])) for r in s.rows[:200] if r[idx - 1] is not None])
                ws.column_dimensions[get_column_letter(idx)].width = min(max(10, longest + 2), 60)
        out = io.BytesIO()
        wb.save(out)
        payload = out.getvalue()
        media, fname = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"query_results_{stamp}.xlsx"

    log_event(AuditAction.RESULT_EXPORTED, user_id=user.id, role=user.role.value, ip=user.ip,
              metadata={"format": body.format, "sheets": len(body.sheets), "rows": total})
    return Response(content=payload, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
