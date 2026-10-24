"""File attach → AI template → per-row expansion → validate → (approval) → execute. Ollama is stubbed in these tests only."""
import io

import pandas as pd
import pytest

from backend.ai import slm


def xlsx(rows, cols):
    b = io.BytesIO()
    pd.DataFrame(rows, columns=cols).to_excel(b, index=False)
    return b.getvalue()


def attach(c, data, name="people.xlsx"):
    return c.post("/api/attachments", files={"file": (name, data, "application/octet-stream")})


def gen(c, question, att=None):
    return c.post("/api/query/generate", json={"question": question, **({"attachment_id": att} if att else {})})


def run(c, sql, **kw): return c.post("/api/query/execute", json={"sql": sql, **kw})


def stub(monkeypatch, reply, seen=None):
    def fake(system, user):
        if seen is not None:
            seen["system"], seen["user"] = system, user
        return reply
    monkeypatch.setattr(slm, "generate", fake)


SAL = xlsx([["EMP001", 70001], ["EMP002", 70002], ["EMP003", 70003]], ["Employee Code", "Salary"])


def test_attach_returns_description_and_normalised_columns(dev):
    j = attach(dev, SAL).json()
    assert j["columns"] == ["employee_code", "salary"] and j["row_count"] == 3 and len(j["sample"]) == 3
    p = dev.get(f"/api/attachments/{j['attachment_id']}").json()
    assert p["rows"][0] == {"employee_code": "EMP001", "salary": 70001}


def test_csv_attachment(dev):
    j = attach(dev, b"employee_code,salary\nEMP001,10\nEMP002,20\n", "a.csv").json()
    assert j["row_count"] == 2 and j["columns"] == ["employee_code", "salary"]


def test_bad_attachments_rejected(dev):
    assert attach(dev, b"hi", "x.txt").status_code == 422
    assert attach(dev, b"", "x.xlsx").status_code == 422
    assert attach(dev, b"not excel", "x.xlsx").status_code == 422
    assert attach(dev, xlsx([["a"]], ["a"]), "x.xls").status_code == 422
    assert attach(dev, b"PK\x03\x04junk", "x.xlsx").status_code == 422
    assert attach(dev, b"a,a\n1,2\n", "dup.csv").status_code == 422                  # duplicate headers
    assert attach(dev, b"a,b\n", "empty.csv").status_code == 422                     # no data rows
    assert attach(dev, b"a,b\n=1+1,2\n", "f.csv").status_code == 422                 # formula
    assert attach(dev, b"PK\x03\x04" + b"0" * (2 * 1024 * 1024)).status_code == 422  # too large
    f = xlsx([["=SUM(1,2)", 1]], ["a", "b"])
    assert attach(dev, f).status_code == 422                                          # real formula cell


def test_too_many_rows_never_truncated(dev):
    from backend.config import settings
    object.__setattr__(settings, "max_attach_rows", 5)
    try:
        r = attach(dev, ("a\n" + "\n".join(str(i) for i in range(10)) + "\n").encode(), "big.csv")
    finally:
        object.__setattr__(settings, "max_attach_rows", 500)
    assert r.status_code == 422 and "too many rows" in r.json()["message"]


def test_attachment_is_private_and_removable(dev, dba):
    aid = attach(dev, SAL).json()["attachment_id"]
    assert dba.get(f"/api/attachments/{aid}").status_code == 404                      # other user cannot read it
    assert dba.c.delete(f"/api/attachments/{aid}", headers=dba._h()).json()["removed"] is False
    assert dev.c.delete(f"/api/attachments/{aid}", headers=dev._h()).json()["removed"] is True
    assert dev.get(f"/api/attachments/{aid}").status_code == 404
    assert gen(dev, "update salaries from the file", aid).status_code == 404


def test_new_attachment_replaces_old(dev):
    a = attach(dev, SAL).json()["attachment_id"]
    b = attach(dev, SAL).json()["attachment_id"]
    assert dev.get(f"/api/attachments/{a}").status_code == 404 and dev.get(f"/api/attachments/{b}").status_code == 200


def test_prompt_describes_file_and_is_not_polluted(dev, monkeypatch):
    seen = {}
    stub(monkeypatch, "CLARIFY: ok", seen)
    aid = attach(dev, SAL).json()["attachment_id"]
    gen(dev, "update salaries from the file", aid)
    assert 'ATTACHED FILE "people.xlsx"' in seen["user"] and "employee_code, salary" in seen["user"]
    assert "{{column_name}}" in seen["system"] and "EMP001" in seen["user"]


def test_update_from_file_developer_needs_approval_then_dba_executes_all_rows(dev, dba, db, monkeypatch):
    stub(monkeypatch, "```sql\nUPDATE employees SET salary = {{salary}} WHERE employee_code = '{{employee_code}}';\n```")
    aid = attach(dev, SAL).json()["attachment_id"]
    j = gen(dev, "Update the salary of the employees in the file", aid).json()
    assert j["valid"] and j["batch"]["rows"] == 3 and j["statement_count"] == 3
    assert j["requires_dba_approval"] is True and j["operation_type"] == "UPDATE"
    assert j["sql"].count("UPDATE employees") == 3 and "70002" in j["sql"] and "{{" not in j["sql"]
    r = run(dev, j["sql"], question="Update the salary of the employees in the file")
    assert r.status_code == 202
    assert db.execute("SELECT salary FROM employees WHERE employee_code='EMP001'").fetchone()["salary"] != 70001
    done = dba.post(f"/api/approvals/{r.json()['approval_id']}/approve", json={}).json()
    assert done["success"] and "3 rows affected" in done["message"]
    sal = {x["employee_code"]: float(x["salary"]) for x in db.execute("SELECT employee_code, salary FROM employees WHERE employee_code IN ('EMP001','EMP002','EMP003')").fetchall()}
    assert sal == {"EMP001": 70001, "EMP002": 70002, "EMP003": 70003}
    for code, original in (("EMP001", 55000), ("EMP002", 45000), ("EMP003", 48000)):          # keep the seed data pristine
        db.execute("UPDATE employees SET salary=%s WHERE employee_code=%s", (original, code))


def test_remove_from_file_dba_runs_directly_atomically(dba, db, monkeypatch):
    for c in ("T1", "T2", "T3"):
        db.execute("INSERT INTO employees (employee_code,name,email,department,designation,salary,joining_date,status) VALUES (%s,%s,%s,'HR','x',1,'2020-01-01','Active')", (c, c, c.lower() + "@x.com"))
    stub(monkeypatch, "DELETE FROM employees WHERE employee_code = {{employee_code}};")
    aid = attach(dba, xlsx([["T1"], ["T2"], ["T3"]], ["employee_code"])).json()["attachment_id"]
    j = gen(dba, "Remove the employees listed in the file", aid).json()
    r = run(dba, j["sql"])
    assert r.status_code == 200 and "3 rows affected" in r.json()["message"] and r.json()["statement_count"] == 3
    assert db.execute("SELECT count(*) AS c FROM employees WHERE employee_code IN ('T1','T2','T3')").fetchone()["c"] == 0


def test_batch_is_all_or_nothing(dba, db, monkeypatch):
    stub(monkeypatch, "UPDATE employees SET email = {{email}} WHERE employee_code = {{employee_code}};")
    aid = attach(dba, xlsx([["EMP004", "fresh.addr@x.com"], ["EMP005", "ravi@example.com"]], ["employee_code", "email"])).json()["attachment_id"]   # 2nd row violates UNIQUE
    j = gen(dba, "update emails from the file", aid).json()
    r = run(dba, j["sql"])
    assert r.status_code == 400 and "Query 2 of 2 failed" in r.json()["message"] and "No changes were saved" in r.json()["message"]
    assert db.execute("SELECT email FROM employees WHERE employee_code='EMP004'").fetchone()["email"] == "anitha@example.com"


def test_insert_from_file_and_hostile_cell_values_stay_data(dev, db, monkeypatch):
    evil = "O'Brien'); DROP TABLE employees; --"
    stub(monkeypatch, "INSERT INTO employees (employee_code, name, email, department, designation, salary, joining_date, status) VALUES ({{employee_code}}, {{name}}, {{email}}, 'HR', 'Temp', {{salary}}, {{joining_date}}, 'Active');")
    f = xlsx([["N1", evil, "n1@x.com", 10, "2024-01-02"], ["N2", "Back\\slash", "n2@x.com", 20, "2024-01-03"]], ["employee_code", "name", "email", "salary", "joining_date"])
    aid = attach(dev, f).json()["attachment_id"]
    j = gen(dev, "Add the employees in the file", aid).json()
    assert j["valid"] and j["requires_dba_approval"] is False
    assert run(dev, j["sql"]).status_code == 200
    names = [r["name"] for r in db.execute("SELECT name FROM employees WHERE employee_code IN ('N1','N2') ORDER BY employee_code").fetchall()]
    assert names == [evil, "Back\\slash"]
    assert db.execute("SELECT to_regclass('employees') AS t").fetchone()["t"] is not None


def test_analyst_batch_write_denied_but_select_batch_allowed(analyst, db, monkeypatch):
    aid = attach(analyst, xlsx([["EMP001"], ["EMP002"]], ["employee_code"])).json()["attachment_id"]
    stub(monkeypatch, "DELETE FROM employees WHERE employee_code = {{employee_code}};")
    j = gen(analyst, "remove them", aid).json()
    assert j["permission_denied"] and not j["valid"]
    assert run(analyst, j["sql"]).status_code == 403
    stub(monkeypatch, "SELECT employee_code, name FROM employees WHERE employee_code = {{employee_code}};")
    j = gen(analyst, "show them", aid).json()
    r = run(analyst, j["sql"])
    assert r.status_code == 200 and r.json()["statement_count"] == 2 and len(r.json()["results"]) == 2


def test_long_select_batches_are_merged_into_one_result(dba, monkeypatch):
    codes = [["EMP001"], ["EMP002"], ["EMP003"], ["EMP004"], ["EMP005"], ["EMP006"], ["EMP007"], ["EMP008"], ["EMP001"]]
    aid = attach(dba, xlsx(codes, ["employee_code"])).json()["attachment_id"]
    stub(monkeypatch, "SELECT employee_code, name FROM employees WHERE employee_code = {{employee_code}};")
    r = run(dba, gen(dba, "show them", aid).json()["sql"]).json()
    assert len(r["results"]) == 1 and r["row_count"] == 9


def test_placeholder_errors_are_friendly(dev, monkeypatch):
    aid = attach(dev, SAL).json()["attachment_id"]
    stub(monkeypatch, "UPDATE employees SET salary = {{bonus}} WHERE employee_code = {{employee_code}};")
    j = gen(dev, "x x x", aid).json()
    assert j["needs_clarification"] and "bonus" in j["message"] and "employee_code, salary" in j["message"]
    stub(monkeypatch, "UPDATE employees SET salary = {{salary}} WHERE employee_code = {{employee_code}};")
    j = gen(dev, "update salaries from the file").json()                 # no attachment
    assert j["needs_clarification"] and "Attach" in j["message"]


def test_model_cannot_smuggle_sql_through_the_template(dev, monkeypatch):
    aid = attach(dev, SAL).json()["attachment_id"]
    stub(monkeypatch, "SELECT * FROM users WHERE id = {{salary}};")
    j = gen(dev, "x x x", aid).json()
    assert j["valid"] is False                                           # expanded SQL is validated like any other


def test_multi_question_script_via_model(dba, monkeypatch):
    stub(monkeypatch, "```sql\nSELECT * FROM departments;\nSELECT COUNT(*) AS n FROM employees;\n```")
    j = gen(dba, "Show departments and count employees").json()
    assert j["valid"] and j["statement_count"] == 2
    r = run(dba, j["sql"]).json()
    assert [x["columns"][0] for x in r["results"]] == ["id", "n"] and "2 queries executed" in r["message"]
