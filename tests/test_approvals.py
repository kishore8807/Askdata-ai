SQL_UPDATE = "UPDATE employees SET salary = 60000 WHERE employee_code = 'EMP001'"


def execute(c, sql, **kw): return c.post("/api/query/execute", json={"sql": sql, **kw})


def salary(db, code="EMP001"):
    return db.execute("SELECT salary FROM employees WHERE employee_code=%s", (code,)).fetchone()["salary"]


def reset(dba): execute(dba, "UPDATE employees SET salary = 55000 WHERE employee_code = 'EMP001'")


def test_developer_update_creates_request_and_does_not_execute(dev, dba, db):
    reset(dba)
    r = execute(dev, SQL_UPDATE, question="Change Kumar salary to 60000")
    assert r.status_code == 202
    j = r.json()
    assert j["approval_required"] and j["message"].startswith("DBA approval is required")
    assert salary(db) == 55000                                   # nothing executed
    row = db.execute("SELECT * FROM approval_requests WHERE id=%s", (j["approval_id"],)).fetchone()
    assert row["status"] == "PENDING" and row["operation_type"] == "UPDATE" and row["target_table"] == "employees"
    assert row["natural_language_request"] == "Change Kumar salary to 60000" and row["requester_role"] == "DEVELOPER"


def test_dba_gets_notification_and_sees_pending(dev, dba):
    execute(dev, "DELETE FROM tasks WHERE id = 999", question="notify test")
    n = dba.get("/api/notifications").json()
    assert n["unread"] >= 1 and any("Database Change Request" in x["title"] for x in n["notifications"])
    p = dba.get("/api/approvals/pending").json()
    assert p["count"] >= 1
    item = p["requests"][0]
    for k in ("requester_name", "requester_role", "natural_language_request", "generated_sql",
              "operation_type", "target_table", "created_at", "status"):
        assert k in item


def test_approve_executes_and_notifies_requester(dev, dba, db):
    reset(dba)
    rid = execute(dev, SQL_UPDATE, question="raise").json()["approval_id"]
    r = dba.post(f"/api/approvals/{rid}/approve", json={})
    assert r.status_code == 200 and r.json()["status"] == "EXECUTED"
    assert salary(db) == 60000
    row = db.execute("SELECT * FROM approval_requests WHERE id=%s", (rid,)).fetchone()
    assert row["status"] == "EXECUTED" and row["reviewed_by"] and row["reviewed_at"]
    notes = dev.get("/api/notifications").json()["notifications"]
    assert any("approved" in n["message"].lower() for n in notes)
    acts = [a["action"] for a in db.execute("SELECT action FROM audit_logs WHERE metadata->>'approval_id' = %s", (str(rid),)).fetchall()]
    assert {"APPROVAL_REQUESTED", "APPROVAL_APPROVED", "SQL_EXECUTED"} <= set(acts)
    reset(dba)


def test_reject_never_executes(dev, dba, db):
    reset(dba)
    rid = execute(dev, SQL_UPDATE).json()["approval_id"]
    assert dba.post(f"/api/approvals/{rid}/reject", json={"reason": "Salary freeze"}).status_code == 200
    assert salary(db) == 55000
    row = db.execute("SELECT * FROM approval_requests WHERE id=%s", (rid,)).fetchone()
    assert row["status"] == "REJECTED" and row["rejection_reason"] == "Salary freeze"
    msgs = [n["message"] for n in dev.get("/api/notifications").json()["notifications"]]
    assert any("rejected" in m.lower() and "Salary freeze" in m for m in msgs)
    # a rejected request can never be approved afterwards
    assert dba.post(f"/api/approvals/{rid}/approve", json={}).status_code == 409
    assert salary(db) == 55000


def test_developer_delete_flow(dev, dba, db):
    dba.post("/api/query/execute", json={"sql": "INSERT INTO departments (department_name) VALUES ('Temp')"})
    rid = execute(dev, "DELETE FROM departments WHERE department_name = 'Temp'").json()["approval_id"]
    assert db.execute("SELECT 1 FROM departments WHERE department_name='Temp'").fetchone()
    assert dba.post(f"/api/approvals/{rid}/approve", json={}).json()["status"] == "EXECUTED"
    assert not db.execute("SELECT 1 FROM departments WHERE department_name='Temp'").fetchone()


def test_non_dba_cannot_approve_or_reject(dev, analyst, dba, db):
    reset(dba)
    rid = execute(dev, SQL_UPDATE).json()["approval_id"]
    for c in (dev, analyst):                                     # requester approving own request, and an analyst
        assert c.post(f"/api/approvals/{rid}/approve", json={}).status_code == 403
        assert c.post(f"/api/approvals/{rid}/reject", json={"reason": "nope nope"}).status_code == 403
        assert c.get("/api/approvals/pending").status_code == 403
    assert salary(db) == 55000
    assert db.execute("SELECT status FROM approval_requests WHERE id=%s", (rid,)).fetchone()["status"] == "PENDING"
    dba.post(f"/api/approvals/{rid}/reject", json={"reason": "cleanup"})


def test_forged_approval_state_is_ignored(dev, db):
    """Client-supplied 'approved' flags are ignored: the Developer still cannot execute directly."""
    r = dev.post("/api/query/execute", json={"sql": SQL_UPDATE, "approved": True, "approval_id": 1, "status": "APPROVED",
                                              "role": "DBA", "approval_status": "EXECUTED"})
    assert r.status_code == 202 and r.json()["approval_required"]
    assert salary(db) == 55000


def test_cannot_double_approve(dev, dba):
    rid = execute(dev, "DELETE FROM tasks WHERE id = 12345").json()["approval_id"]
    assert dba.post(f"/api/approvals/{rid}/approve", json={}).status_code == 200
    assert dba.post(f"/api/approvals/{rid}/approve", json={}).status_code == 409


def test_failed_execution_marks_failed(dev, dba, db):
    rid = execute(dev, "UPDATE employees SET email = 'ravi@example.com' WHERE employee_code = 'EMP001'").json()["approval_id"]
    r = dba.post(f"/api/approvals/{rid}/approve", json={}).json()
    assert r["success"] is False and r["status"] == "FAILED"
    assert db.execute("SELECT status FROM approval_requests WHERE id=%s", (rid,)).fetchone()["status"] == "FAILED"


def test_dangerous_approval_needs_dba_confirmation(dev, dba, db):
    execute(dba, "CREATE TABLE appr_t (id int)")
    rid = execute(dev, "DROP TABLE appr_t").json()["approval_id"]
    assert dba.post(f"/api/approvals/{rid}/approve", json={}).status_code == 400
    assert db.execute("SELECT to_regclass('appr_t') AS t").fetchone()["t"] is not None
    assert dba.post(f"/api/approvals/{rid}/approve", json={"confirm_dangerous": True}).json()["status"] == "EXECUTED"
    assert db.execute("SELECT to_regclass('appr_t') AS t").fetchone()["t"] is None


def test_developer_full_table_write_blocked_outright(dev, db):
    r = execute(dev, "DELETE FROM employees")
    assert r.status_code in (403, 422) and db.execute("SELECT count(*) AS c FROM employees").fetchone()["c"] >= 8


def test_developer_direct_ops(dev):
    assert execute(dev, "SELECT * FROM employees").status_code == 200
    assert execute(dev, "CREATE TABLE dev_t (id int)").status_code == 200
    assert execute(dev, "ALTER TABLE dev_t ADD COLUMN note text").status_code == 200
    assert execute(dev, "INSERT INTO dev_t (id) VALUES (1)").status_code == 200
    assert execute(dev, "ALTER TABLE dev_t DROP COLUMN note").status_code == 202       # destructive → approval
    assert execute(dev, "DROP TABLE dev_t").status_code == 202


def test_requester_sees_own_requests(dev, analyst):
    assert any(x["requester_role"] == "DEVELOPER" for x in dev.get("/api/approvals/mine").json()["requests"])
    assert analyst.get("/api/approvals/mine").json()["requests"] == []


def test_dev_transaction_with_update_needs_approval(dev, db):
    r = execute(dev, "BEGIN; UPDATE employees SET salary = salary + 1 WHERE department = 'Sales'; COMMIT;")
    assert r.status_code == 202 and r.json()["operation_type"] == "TRANSACTION"
