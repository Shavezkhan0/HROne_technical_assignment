import pytest
import app.main


def test_admin_explain_endpoint_names_and_shapes(client):
    # Unknown endpoint name -> 422 (FastAPI enum validation)
    r = client.get("/admin/explain/invalid_endpoint")
    assert r.status_code == 422

    # Seed 1 employee and 1 log so collections have documents
    client.post("/employees", json={
        "emp_code": "EMP0001",
        "name": "Alice",
        "department": "Engineering",
        "joined_on": "2026-01-01",
        "email": "alice@example.com"
    })
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0001",
        "date": "2026-07-01",
        "status": "PRESENT",
        "late_minutes": 10
    })

    # Test all 5 valid endpoints
    endpoints_params = [
        ("attendance_list", "attendance_logs", "?emp_code=EMP0001&date_from=2026-07-01&date_to=2026-07-31"),
        ("employee_monthly", "attendance_logs", "?emp_code=EMP0001&month=2026-07"),
        ("department_summary", "employees", "?month=2026-07&department=Engineering"),
        ("late_leaderboard", "attendance_logs", "?month=2026-07&limit=10&department=Engineering"),
        ("department_trend", "attendance_logs", "?department=Engineering&from=2026-07-01&to=2026-07-10")
    ]

    for ep_name, coll_name, params in endpoints_params:
        r = client.get(f"/admin/explain/{ep_name}{params}")
        assert r.status_code == 200, f"Explain failed for {ep_name}: {r.status_code} {r.text}"
        data = r.json()
        assert set(data.keys()) == {"endpoint", "collection", "explain"}
        assert data["endpoint"] == ep_name
        assert data["collection"] == coll_name
        assert isinstance(data["explain"], dict)
        assert "command" in data["explain"] or "stages" in data["explain"] or "queryPlanner" in data["explain"]


def test_admin_explain_missing_parameters_422(client):
    # employee_monthly missing required params
    r1 = client.get("/admin/explain/employee_monthly")
    assert r1.status_code == 422

    r2 = client.get("/admin/explain/employee_monthly?emp_code=EMP0001")
    assert r2.status_code == 422

    # department_summary missing month
    r3 = client.get("/admin/explain/department_summary")
    assert r3.status_code == 422

    # late_leaderboard missing month
    r4 = client.get("/admin/explain/late_leaderboard")
    assert r4.status_code == 422

    # department_trend missing department or dates
    r5 = client.get("/admin/explain/department_trend?from=2026-07-01&to=2026-07-05")
    assert r5.status_code == 422

    r6 = client.get("/admin/explain/department_trend?department=Engineering")
    assert r6.status_code == 422

    # Invalid range: from > to
    r7 = client.get("/admin/explain/department_trend?department=Engineering&from=2026-07-10&to=2026-07-05")
    assert r7.status_code == 422

    # Invalid range: date_from > date_to in attendance_list
    r8 = client.get("/admin/explain/attendance_list?date_from=2026-07-10&date_to=2026-07-05")
    assert r8.status_code == 422


def test_admin_explain_unknown_entities_and_read_only(client):
    # Explain should NOT fail with 404 for non-existent employee or department
    r1 = client.get("/admin/explain/employee_monthly?emp_code=EMP9999&month=2026-07")
    assert r1.status_code == 200
    assert r1.json()["endpoint"] == "employee_monthly"

    r2 = client.get("/admin/explain/department_trend?department=UnknownDept&from=2026-07-01&to=2026-07-05")
    assert r2.status_code == 200
    assert r2.json()["endpoint"] == "department_trend"

    # Verify explain did not insert any documents
    emp_count = app.main.db.employees.count_documents({})
    log_count = app.main.db.attendance_logs.count_documents({})
    assert emp_count == 0
    assert log_count == 0
