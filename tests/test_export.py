import csv
import io

from openpyxl import load_workbook

SHEET = {"name": "Query 1", "columns": ["a", "b"], "rows": [[1, "x"], [2, "=HYPERLINK(\"http://evil\")"], [None, "-5+3"]]}


def test_csv_export_is_formula_safe_and_audited(dev, db):
    r = dev.post("/api/export", json={"format": "csv", "sheets": [SHEET]})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and r.headers["content-disposition"].endswith('.csv"')
    text = r.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["a", "b"] and rows[1] == ["1", "x"]
    assert rows[2][1].startswith("'=") and rows[3][1].startswith("'-")          # neutralised
    assert db.execute("SELECT 1 FROM audit_logs WHERE action='RESULT_EXPORTED'").fetchone()


def test_xlsx_export_multi_sheet_and_text_cells_stay_text(dba):
    r = dba.post("/api/export", json={"format": "xlsx", "sheets": [SHEET, {**SHEET, "name": "Query 1"}, {"name": "a/b:c", "columns": ["z"], "rows": [[1]]}]})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    wb = load_workbook(io.BytesIO(r.content))
    assert len(wb.sheetnames) == 3 and len(set(n.lower() for n in wb.sheetnames)) == 3 and all("/" not in n and ":" not in n for n in wb.sheetnames)
    cell = wb[wb.sheetnames[0]]["B3"]
    assert cell.data_type == "s" and cell.value.startswith("=HYPERLINK")           # a string, not a formula


def test_export_validation(dev, anon):
    assert anon.post("/api/export", json={"format": "csv", "sheets": [SHEET]}).status_code == 401
    assert dev.post("/api/export", json={"format": "csv", "sheets": [SHEET, SHEET]}).status_code == 422
    assert dev.post("/api/export", json={"format": "pdf", "sheets": [SHEET]}).status_code == 422
    assert dev.post("/api/export", json={"format": "csv", "sheets": []}).status_code == 422
    assert dev.post("/api/export", json={"format": "csv", "sheets": [{"columns": ["a"], "rows": [[1, 2]]}]}).status_code == 422
    from backend.config import settings
    object.__setattr__(settings, "max_export_rows", 2)
    try:
        assert dev.post("/api/export", json={"format": "csv", "sheets": [{"columns": ["a"], "rows": [[1], [2], [3]]}]}).status_code == 413
    finally:
        object.__setattr__(settings, "max_export_rows", 20000)


def test_end_to_end_run_then_export(analyst):
    res = analyst.post("/api/query/execute", json={"sql": "SELECT employee_code, salary FROM employees ORDER BY 1 LIMIT 3"}).json()
    r = analyst.post("/api/export", json={"format": "xlsx", "sheets": [{"name": "Query 1", "columns": res["columns"], "rows": res["rows"]}]})
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.active["A2"].value == "EMP001"
