import pytest
from datetime import datetime, timezone
import app.main
from app.main import to_epoch_ms

def test_employee_monthly_404_and_422(client):
    # 404 for unknown employee
    res = client.get("/analytics/employees/EMP9999/monthly?month=2026-07")
    assert res.status_code == 404

    # Create employee
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })

    # 422 for invalid month formats
    assert client.get("/analytics/employees/EMP0001/monthly?month=2026-7").status_code == 422
    assert client.get("/analytics/employees/EMP0001/monthly?month=2026-13").status_code == 422
    assert client.get("/analytics/employees/EMP0001/monthly?month=invalid").status_code == 422
    assert client.get("/analytics/employees/EMP0001/monthly").status_code == 422


def test_employee_monthly_sample_data(client):
    # EMP0001 July 2026: records on Mon 6 and Tue 7 July, both full days
    # working_days in July 2026 = 23 (Mon-Fri)
    # present_days = 2.0
    # attendance_pct = 2.0 / 23 * 100 = 8.69565... -> rounds to 8.70
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "Engineering", "joined_on": "2026-01-05",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Mon 2026-07-06: PRESENT, punch_in 09:28, out 18:35, late 0, overtime 0, half_day false
    pi1 = datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc)
    po1 = datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc)
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-06", "status": "PRESENT",
        "punch_in": pi1, "punch_out": po1, "work_hours": 9.12,
        "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": []
    })
    # Tue 2026-07-07: PRESENT, punch_in 09:28, out 18:30, late 0, overtime 0, half_day false
    pi2 = datetime(2026, 7, 7, 3, 58, tzinfo=timezone.utc)
    po2 = datetime(2026, 7, 7, 13, 0, tzinfo=timezone.utc)
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-07", "status": "PRESENT",
        "punch_in": pi2, "punch_out": po2, "work_hours": 9.03,
        "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": []
    })

    res = client.get("/analytics/employees/EMP0001/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["emp_code"] == "EMP0001"
    assert data["month"] == "2026-07"
    assert data["working_days"] == 23
    assert data["present_days"] == 2.0
    assert data["leave_days"] == 0
    assert data["late_count"] == 0
    assert data["total_late_minutes"] == 0
    assert data["total_overtime_minutes"] == 0
    assert data["attendance_pct"] == 8.70


def test_employee_monthly_half_day_and_legacy(client):
    # EMP0004: Half-day record (3.5 hours) counts as 0.5 present_days
    # Also add legacy record without half_day field (counts as false -> full day 1.0)
    client.post("/employees", json={
        "emp_code": "EMP0004", "name": "D", "email": "d@d.com",
        "department": "Engineering", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Tue 2026-07-07: WFH, half_day: True
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0004", "date": "2026-07-07", "status": "WFH",
        "punch_in": datetime(2026, 7, 7, 4, 0, tzinfo=timezone.utc),
        "punch_out": datetime(2026, 7, 7, 7, 30, tzinfo=timezone.utc),
        "work_hours": 3.5, "late_minutes": 0, "overtime_minutes": 0,
        "half_day": True, "history": []
    })
    # Wed 2026-07-08: PRESENT, legacy record (no half_day, no history)
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0004", "date": "2026-07-08", "status": "PRESENT",
        "punch_in": datetime(2026, 7, 8, 4, 0, tzinfo=timezone.utc),
        "punch_out": datetime(2026, 7, 8, 13, 0, tzinfo=timezone.utc),
        "work_hours": 9.0, "late_minutes": 0, "overtime_minutes": 0
    })

    res = client.get("/analytics/employees/EMP0004/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["present_days"] == 1.5
    assert data["working_days"] == 23
    # 1.5 / 23 * 100 = 6.5217... -> 6.52
    assert data["attendance_pct"] == 6.52


def test_employee_monthly_weekend_and_on_duty(client):
    # A weekend record contributes late and overtime totals, but NOT present_days
    # An ON_DUTY record on a weekday counts as present_day 1.0
    client.post("/employees", json={
        "emp_code": "EMP0005", "name": "E", "email": "e@e.com",
        "department": "Sales", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Saturday 2026-07-11: PRESENT, late 30, overtime 45
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0005", "date": "2026-07-11", "status": "PRESENT",
        "punch_in": datetime(2026, 7, 11, 4, 30, tzinfo=timezone.utc),
        "punch_out": datetime(2026, 7, 11, 13, 45, tzinfo=timezone.utc),
        "work_hours": 9.25, "late_minutes": 30, "overtime_minutes": 45,
        "half_day": False, "history": []
    })
    # Monday 2026-07-13: ON_DUTY, late 0, overtime 0
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0005", "date": "2026-07-13", "status": "ON_DUTY",
        "punch_in": datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc),
        "punch_out": datetime(2026, 7, 13, 13, 0, tzinfo=timezone.utc),
        "work_hours": 9.0, "late_minutes": 0, "overtime_minutes": 0,
        "half_day": False, "history": []
    })

    res = client.get("/analytics/employees/EMP0005/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["present_days"] == 1.0 # only Monday ON_DUTY counts towards present_days
    assert data["late_count"] == 1 # from Saturday
    assert data["total_late_minutes"] == 30
    assert data["total_overtime_minutes"] == 45


def test_employee_monthly_leave_days_all_week(client):
    # leave_days counts every LEAVE record in the month, even on a weekend
    client.post("/employees", json={
        "emp_code": "EMP0006", "name": "F", "email": "f@f.com",
        "department": "HR", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Weekday LEAVE
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0006", "date": "2026-07-08", "status": "LEAVE",
        "punch_in": None, "punch_out": None, "work_hours": None,
        "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": []
    })
    # Weekend LEAVE (Sunday 2026-07-12)
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0006", "date": "2026-07-12", "status": "LEAVE",
        "punch_in": None, "punch_out": None, "work_hours": None,
        "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": []
    })

    res = client.get("/analytics/employees/EMP0006/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["leave_days"] == 2
    assert data["present_days"] == 0.0


def test_employee_monthly_mid_month_joiner(client):
    # EMP0002 joined 2026-07-13 (Monday). Total Mon-Fri from 2026-07-13 to 2026-07-31 = 15 working days
    client.post("/employees", json={
        "emp_code": "EMP0002", "name": "B", "email": "b@b.com",
        "department": "Engineering", "joined_on": "2026-07-13",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Mon 2026-07-13 full day
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0002", "date": "2026-07-13", "status": "PRESENT",
        "punch_in": datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc),
        "punch_out": datetime(2026, 7, 13, 13, 0, tzinfo=timezone.utc),
        "work_hours": 9.0, "late_minutes": 0, "overtime_minutes": 0,
        "half_day": False, "history": []
    })

    res = client.get("/analytics/employees/EMP0002/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 15
    assert data["present_days"] == 1.0
    # 1.0 / 15 * 100 = 6.6666... -> 6.67
    assert data["attendance_pct"] == 6.67


def test_employee_monthly_joined_after_month(client):
    # Employee joined in August 2026, querying July 2026
    client.post("/employees", json={
        "emp_code": "EMP0007", "name": "G", "email": "g@g.com",
        "department": "Finance", "joined_on": "2026-08-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })

    res = client.get("/analytics/employees/EMP0007/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 0
    assert data["present_days"] == 0.0
    assert data["leave_days"] == 0
    assert data["late_count"] == 0
    assert data["total_late_minutes"] == 0
    assert data["total_overtime_minutes"] == 0
    assert data["attendance_pct"] is None


def test_employee_monthly_no_records(client):
    # Employee joined in January 2026, has no records in July 2026
    client.post("/employees", json={
        "emp_code": "EMP0008", "name": "H", "email": "h@h.com",
        "department": "Finance", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })

    res = client.get("/analytics/employees/EMP0008/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 23
    assert data["present_days"] == 0.0
    assert data["leave_days"] == 0
    assert data["late_count"] == 0
    assert data["total_late_minutes"] == 0
    assert data["total_overtime_minutes"] == 0
    assert data["attendance_pct"] == 0.0


def test_employee_monthly_rounding_boundary(client):
    # Test half-up rounding boundary for attendance_pct:
    # 2.0 / 16 * 100 = 12.5 (exact)
    # Suppose working_days = 20: 1.0 / 20 * 100 = 5.0
    # Let's test a case where value ends in .xx5
    # e.g., 23 working days:
    # 1.0 / 23 * 100 = 4.3478 -> 4.35
    # 3.0 / 23 * 100 = 13.0434 -> 13.04
    # With mid-month joiner: joined 2026-07-29 (Wed, Thu, Fri -> 3 working days)
    # 1.0 / 3 * 100 = 33.3333 -> 33.33
    client.post("/employees", json={
        "emp_code": "EMP0009", "name": "I", "email": "i@i.com",
        "department": "Finance", "joined_on": "2026-07-29",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Wed 2026-07-29: 1 present day
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0009", "date": "2026-07-29", "status": "PRESENT",
        "punch_in": datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc),
        "punch_out": datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc),
        "work_hours": 9.0, "late_minutes": 0, "overtime_minutes": 0,
        "half_day": False, "history": []
    })

    res = client.get("/analytics/employees/EMP0009/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 3
    assert data["present_days"] == 1.0
    assert data["attendance_pct"] == 33.33
