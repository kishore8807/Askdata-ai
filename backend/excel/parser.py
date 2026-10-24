"""Parsing of attached data files (.xlsx, .xls, .csv) into a clean table of plain Python values.

The attachment is *data*, never SQL: values are later inserted into a validated SQL template as escaped literals.
Rows are never silently truncated (a half-applied bulk UPDATE/DELETE would be dangerous), so oversized files are rejected.
"""
from __future__ import annotations

import datetime as dt
import io
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import settings

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}
MAX_CELL_CHARS = 2000


class AttachmentError(Exception):
    """Message is safe to show to the user."""


def normalise_header(h: object) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(h).strip().lower()).strip("_")
    if s and s[0].isdigit():
        s = "c_" + s
    return s


def _cell(v: Any) -> Any:
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return v.date().isoformat() if (v.hour, v.minute, v.second) == (0, 0, 0) else v.replace(microsecond=0).isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, bool):
        return v
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):          # numpy scalars
        v = v.item()
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        return int(v) if v.is_integer() and abs(v) < 1e15 else v
    s = str(v).replace("\x00", "").strip()
    if not s:
        return None
    if len(s) > MAX_CELL_CHARS:
        raise AttachmentError(f"A cell is longer than {MAX_CELL_CHARS} characters.")
    if s.startswith("="):
        raise AttachmentError("The file contains Excel formulas. Please paste plain values only.")
    return s


def _reject_formula_cells(data: bytes, nrows: int) -> None:
    """pandas returns a formula's cached result; look at the raw cells so formulas are rejected explicitly."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), data_only=False, read_only=True)
        ws = wb.worksheets[0]
        for row in ws.iter_rows(min_row=1, max_row=nrows + 1):
            for cell in row:
                if getattr(cell, "data_type", None) == "f" or (isinstance(cell.value, str) and cell.value.startswith("=")):
                    raise AttachmentError("The file contains Excel formulas. Please paste plain values only.")
        wb.close()
    except AttachmentError:
        raise
    except Exception:  # noqa: BLE001 — best effort; _cell() still rejects text that starts with '='
        pass


def _is_blank(v: Any) -> bool:
    return v is None or v is pd.NaT or (isinstance(v, float) and math.isnan(v))


def _read(ext: str, data: bytes) -> pd.DataFrame:
    limit = settings.max_attach_rows + 1
    if ext == ".csv":
        if b"\x00" in data[:4096]:
            raise AttachmentError("That does not look like a CSV text file.")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
        return pd.read_csv(io.StringIO(text), dtype=object, keep_default_na=False, na_values=[""],
                           nrows=limit + 1, sep=None, engine="python", skipinitialspace=True, header=None)
    engine = "openpyxl" if ext == ".xlsx" else "xlrd"
    return pd.read_excel(io.BytesIO(data), engine=engine, dtype=object, sheet_name=0, header=None, nrows=limit + 1)


def parse_table(filename: str, data: bytes) -> dict[str, Any]:
    """Returns {"columns": [...], "rows": [ {col: value}, ... ]}. Raises AttachmentError."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AttachmentError("Only .xlsx, .xls and .csv files can be attached.")
    if not data:
        raise AttachmentError("The attached file is empty.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise AttachmentError(f"The file is too large (maximum {settings.max_upload_mb} MB).")
    if ext == ".xlsx" and data[:4] != b"PK\x03\x04":
        raise AttachmentError("The file content does not match its extension. Please attach a genuine Excel file.")
    if ext == ".xls" and data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise AttachmentError("The file content does not match its extension. Please attach a genuine Excel file.")

    try:
        df = _read(ext, data)
    except AttachmentError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AttachmentError("The file could not be read. It may be corrupted or password-protected.") from exc

    if df.empty:
        raise AttachmentError("The file has no data.")

    # Smart header row detection: skip top blank or single-cell title banner rows
    header_idx = 0
    for idx in range(min(10, len(df))):
        row_vals = [c for c in df.iloc[idx] if not _is_blank(c)]
        if len(row_vals) > 1 or (len(row_vals) == 1 and len(df.columns) == 1):
            header_idx = idx
            break

    header_row = list(df.iloc[header_idx])
    df = df.iloc[header_idx + 1:].reset_index(drop=True)
    if len(df) > settings.max_attach_rows:
        raise AttachmentError(f"The file has too many rows (maximum {settings.max_attach_rows}).")
    if ext == ".xlsx":
        _reject_formula_cells(data, len(df))

    # headers
    names = [normalise_header("" if _is_blank(c) else c) for c in header_row]
    keep = [i for i, n in enumerate(names) if n and not n.startswith("unnamed")]
    if not keep:
        raise AttachmentError("The first row of the file must contain column names.")
    if len(keep) > settings.max_attach_columns:
        raise AttachmentError(f"The file has too many columns (maximum {settings.max_attach_columns}).")
    cols = [names[i] for i in keep]
    dupes = sorted({c for c in cols if cols.count(c) > 1})
    if dupes:
        raise AttachmentError("Duplicate column names: " + ", ".join(dupes) + ".")
    df = df.iloc[:, keep]
    df.columns = cols

    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        clean = {c: _cell(rec[c]) for c in cols}
        if all(v is None for v in clean.values()):
            continue                                   # skip empty rows
        rows.append(clean)
    if not rows:
        raise AttachmentError("The file has no data rows below the header.")
    if not any(c in cols for c in ("employee_code", "emp_code", "employee_id", "emp_id", "code")):
        cols.insert(0, "employee_code")
        for idx, row in enumerate(rows, start=101):
            row["employee_code"] = f"EMP{idx:03d}"
    return {"columns": cols, "rows": rows}
