import pytest
from datetime import datetime, timezone
import app.main

def test_dept_summary_validation_and_empty(client):
    # Invalid month -> 422
    assert client.get("/analytics/departments/summary?month=2026-7").status_code == 422
    assert client.get("/analytics/departments/summary?month=invalid").status_code == 422
    assert client.get("/analytics/departments/summary").status_code == 422

    # Empty result when no employees exist
    res = client.get("/analytics/departments/summary?month=2026-07")
    assert res.status_code == 200
    assert res.json() == {"month": "2026-07", "items": []}

    # Department filter that matches nothing -> 200 with items []
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "Engineering", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    res_none = client.get("/analytics/departments/summary?month=2026-07&department=Nonexistent")
    assert res_none.status_code == 200
    assert res_none.json() == {"month": "2026-07", "items": []}


def test_dept_summary_sample_data(client):
    # Seed sample employees and logs
    # EMP0001 (Engineering): joined 2026-01-05
    # EMP0002 (Engineering): joined 2026-07-13
    # EMP0003 (Engineering): joined 2026-01-05
    # EMP0004 (Engineering): joined 2026-01-05
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "Asha Rao", "email": "asha@example.com",
        "department": "Engineering", "joined_on": "2026-01-05",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0002", "name": "Bikram Shah", "email": "bikram@example.com",
        "department": "Engineering", "joined_on": "2026-07-13",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0003", "name": "Chitra Iyer", "email": "chitra@example.com",
        "department": "Engineering", "joined_on": "2026-01-05",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0004", "name": "Dev Kulkarni", "email": "dev@example.com",
        "department": "Engineering", "joined_on": "2026-01-05",
        "shift_start": "09:30", "shift_end": "18:30"
    })

    # Engineering logs for July 2026:
    # EMP0001:
    # 2026-07-06: PRESENT, work_hours 9.12, late 0, overtime 0
    # 2026-07-07: PRESENT, work_hours 9.03, late 0, overtime 0
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-06", "status": "PRESENT",
        "work_hours": 9.12, "late_minutes": 0, "overtime_minutes": 0, "half_day": False
    })
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-07", "status": "PRESENT",
        "work_hours": 9.03, "late_minutes": 0, "overtime_minutes": 0, "half_day": False
    })

    # EMP0002:
    # 2026-07-14: PRESENT, legacy record (no half_day), work_hours 8.98, late 0, overtime 0
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0002", "date": "2026-07-14", "status": "PRESENT",
        "work_hours": 8.98, "late_minutes": 0, "overtime_minutes": 0
    })

    # EMP0003:
    # 2026-07-06: PRESENT, work_hours 9.08, late 35, overtime 40
    # 2026-07-08: LEAVE, work_hours None, late 0, overtime 0
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0003", "date": "2026-07-06", "status": "PRESENT",
        "work_hours": 9.08, "late_minutes": 35, "overtime_minutes": 40, "half_day": False
    })
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0003", "date": "2026-07-08", "status": "LEAVE",
        "work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False
    })

    # EMP0004:
    # 2026-07-07: WFH, work_hours 3.5, half_day True, late 0, overtime 0
    # 2026-07-08: ABSENT, work_hours None, late 0, overtime 0
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0004", "date": "2026-07-07", "status": "WFH",
        "work_hours": 3.50, "late_minutes": 0, "overtime_minutes": 0, "half_day": True
    })
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0004", "date": "2026-07-08", "status": "ABSENT",
        "work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False
    })

    # Hand-computed expected values:
    # Headcount: 4 (EMP0001, EMP0002, EMP0003, EMP0004)
    # present_days:
    #   EMP0001: 1.0 + 1.0 = 2.0
    #   EMP0002: 1.0 (legacy record treated as full day)
    #   EMP0003: 1.0 (07-06 is Monday)
    #   EMP0004: 0.5 (half_day)
    #   Total present_days = 4.5
    # avg_work_hours:
    #   Present records with work_hours:
    #     9.12 + 9.03 + 8.98 + 9.08 + 3.50 = 39.71 / 5 = 7.942 -> rounds to 7.94
    # late_count: 1 (EMP0003 on 07-06)
    # total_late_minutes: 35
    # leave_count: 1 (EMP0003 on 07-08)
    # on_duty_count: 0

    res = client.get("/analytics/departments/summary?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["month"] == "2026-07"
    assert len(data["items"]) == 1
    eng = data["items"][0]
    assert eng["department"] == "Engineering"
    assert eng["headcount"] == 4
    assert eng["present_days"] == 4.5
    assert eng["avg_work_hours"] == 7.94
    assert eng["late_count"] == 1
    assert eng["total_late_minutes"] == 35
    assert eng["leave_count"] == 1
    assert eng["on_duty_count"] == 0


def test_dept_summary_headcount_not_inflated_by_multiple_logs(client):
    # One employee with several logs must contribute exactly 1 to headcount
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "Engineering", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    for day in range(6, 11): # 5 days
        app.main.db.attendance_logs.insert_one({
            "emp_code": "EMP0001", "date": f"2026-07-{day:02d}", "status": "PRESENT",
            "work_hours": 8.0, "late_minutes": 0, "overtime_minutes": 0, "half_day": False
        })

    res = client.get("/analytics/departments/summary?month=2026-07&department=Engineering")
    assert res.status_code == 200
    item = res.json()["items"][0]
    assert item["headcount"] == 1
    assert item["present_days"] == 5.0
    assert item["avg_work_hours"] == 8.0


def test_dept_summary_zero_logs_counted_in_headcount(client):
    # Employee in IT with zero logs must be counted in headcount
    client.post("/employees", json={
        "emp_code": "EMP0005", "name": "E", "email": "e@e.com",
        "department": "IT", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    res = client.get("/analytics/departments/summary?month=2026-07&department=IT")
    assert res.status_code == 200
    assert len(res.json()["items"]) == 1
    it = res.json()["items"][0]
    assert it["department"] == "IT"
    assert it["headcount"] == 1
    assert it["present_days"] == 0.0
    assert it["avg_work_hours"] is None
    assert it["late_count"] == 0
    assert it["total_late_minutes"] == 0
    assert it["leave_count"] == 0
    assert it["on_duty_count"] == 0


def test_dept_summary_join_dates_and_june_exclusion(client):
    # EMP0001 joined 2026-01-01
    # EMP0002 joined 2026-07-13
    # EMP0003 joined 2026-07-31 (last day of July)
    # EMP0004 joined 2026-08-01 (August)
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "Engineering", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0002", "name": "B", "email": "b@b.com",
        "department": "Engineering", "joined_on": "2026-07-13",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0003", "name": "C", "email": "c@c.com",
        "department": "Engineering", "joined_on": "2026-07-31",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0004", "name": "D", "email": "d@d.com",
        "department": "Engineering", "joined_on": "2026-08-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })

    # In June 2026 (2026-06): only EMP0001 was hired; EMP0002, 03, 04 are excluded from Engineering headcount
    res_june = client.get("/analytics/departments/summary?month=2026-06&department=Engineering")
    assert res_june.status_code == 200
    assert res_june.json()["items"][0]["headcount"] == 1

    # In July 2026 (2026-07): EMP0001, EMP0002, EMP0003 are included; EMP0004 is excluded
    res_july = client.get("/analytics/departments/summary?month=2026-07&department=Engineering")
    assert res_july.status_code == 200
    assert res_july.json()["items"][0]["headcount"] == 3


def test_dept_summary_weekend_and_on_duty_with_work_hours(client):
    # A weekend record contributes late and overtime totals, but NOT present_days
    # An ON_DUTY record on a weekday with work_hours counts towards present_days and avg_work_hours
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "Operations", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Saturday 2026-07-11: PRESENT, work_hours 8.0, late 20, overtime 30
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-11", "status": "PRESENT",
        "work_hours": 8.0, "late_minutes": 20, "overtime_minutes": 30, "half_day": False
    })
    # Monday 2026-07-13: ON_DUTY, work_hours 10.0, late 0, overtime 0
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-13", "status": "ON_DUTY",
        "work_hours": 10.0, "late_minutes": 0, "overtime_minutes": 0, "half_day": False
    })

    res = client.get("/analytics/departments/summary?month=2026-07&department=Operations")
    assert res.status_code == 200
    item = res.json()["items"][0]
    assert item["headcount"] == 1
    assert item["present_days"] == 1.0 # Only Monday ON_DUTY
    assert item["on_duty_count"] == 1
    assert item["late_count"] == 1
    assert item["total_late_minutes"] == 20
    # avg_work_hours: (8.0 + 10.0) / 2 = 9.0
    assert item["avg_work_hours"] == 9.0
