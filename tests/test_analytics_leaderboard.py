import pytest
from datetime import datetime, timezone
import app.main

def test_leaderboard_validation(client):
    # Invalid month format -> 422
    assert client.get("/analytics/leaderboard/late?month=2026-7").status_code == 422
    assert client.get("/analytics/leaderboard/late?month=invalid").status_code == 422
    assert client.get("/analytics/leaderboard/late").status_code == 422

    # Invalid limit (< 1 or > 50 or non-int) -> 422
    assert client.get("/analytics/leaderboard/late?month=2026-07&limit=0").status_code == 422
    assert client.get("/analytics/leaderboard/late?month=2026-07&limit=51").status_code == 422
    assert client.get("/analytics/leaderboard/late?month=2026-07&limit=-5").status_code == 422
    assert client.get("/analytics/leaderboard/late?month=2026-07&limit=abc").status_code == 422

    # Month with no late records -> empty list
    res = client.get("/analytics/leaderboard/late?month=2026-07")
    assert res.status_code == 200
    assert res.json() == {"month": "2026-07", "items": []}


def test_leaderboard_sample_data(client):
    # Seed employees
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "Asha Rao", "email": "asha@example.com",
        "department": "Engineering", "joined_on": "2026-01-05",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0003", "name": "Chitra Iyer", "email": "chitra@example.com",
        "department": "Engineering", "joined_on": "2026-01-05",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0005", "name": "E", "email": "e@example.com",
        "department": "Engineering", "joined_on": "2026-01-05",
        "shift_start": "22:00", "shift_end": "06:00"
    })

    # Sample logs in July 2026:
    # EMP0003: 2026-07-06 late_minutes 35
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0003", "date": "2026-07-06", "status": "PRESENT",
        "late_minutes": 35, "work_hours": 9.08, "overtime_minutes": 40
    })
    # EMP0001: on time (late_minutes 0) -> should NOT appear
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-06", "status": "PRESENT",
        "late_minutes": 0, "work_hours": 9.12, "overtime_minutes": 0
    })
    # EMP0005: on time (late_minutes 0) -> should NOT appear
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0005", "date": "2026-07-06", "status": "PRESENT",
        "late_minutes": 0, "work_hours": 8.75, "overtime_minutes": 40
    })

    res = client.get("/analytics/leaderboard/late?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["month"] == "2026-07"
    assert len(data["items"]) == 1
    assert data["items"][0] == {
        "rank": 1,
        "emp_code": "EMP0003",
        "name": "Chitra Iyer",
        "department": "Engineering",
        "total_late_minutes": 35,
        "late_count": 1
    }


def test_leaderboard_competition_ranking_and_tie_order(client):
    # Setup 4 employees in IT
    # EMP0001: 50 min
    # EMP0002: 40 min
    # EMP0003: 40 min (tie with EMP0002)
    # EMP0004: 20 min
    # Expected ranks: EMP0001 -> 1; EMP0002 & EMP0003 -> 2; EMP0004 -> 4 (Standard competition 1, 2, 2, 4)
    # Between EMP0002 and EMP0003: ordered by emp_code ascending
    for i in range(1, 5):
        client.post("/employees", json={
            "emp_code": f"EMP000{i}", "name": f"User {i}", "email": f"u{i}@test.com",
            "department": "IT", "joined_on": "2026-01-01",
            "shift_start": "09:30", "shift_end": "18:30"
        })

    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0001", "date": "2026-07-01", "late_minutes": 50})
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0002", "date": "2026-07-01", "late_minutes": 40})
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0003", "date": "2026-07-01", "late_minutes": 40})
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0004", "date": "2026-07-01", "late_minutes": 20})

    res = client.get("/analytics/leaderboard/late?month=2026-07")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 4
    assert items[0]["emp_code"] == "EMP0001" and items[0]["rank"] == 1
    assert items[1]["emp_code"] == "EMP0002" and items[1]["rank"] == 2
    assert items[2]["emp_code"] == "EMP0003" and items[2]["rank"] == 2
    assert items[3]["emp_code"] == "EMP0004" and items[3]["rank"] == 4


def test_leaderboard_limit_boundary_ties(client):
    # When limit = 2:
    # EMP0001 (rank 1), EMP0002 (rank 2), EMP0003 (rank 2)
    # Since rank <= limit (2 <= 2), all 3 rows must be returned! (More than limit rows!)
    for i in range(1, 4):
        client.post("/employees", json={
            "emp_code": f"EMP000{i}", "name": f"User {i}", "email": f"u{i}@test.com",
            "department": "IT", "joined_on": "2026-01-01",
            "shift_start": "09:30", "shift_end": "18:30"
        })
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0001", "date": "2026-07-01", "late_minutes": 50})
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0002", "date": "2026-07-01", "late_minutes": 40})
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0003", "date": "2026-07-01", "late_minutes": 40})

    res = client.get("/analytics/leaderboard/late?month=2026-07&limit=2")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 3
    assert [x["emp_code"] for x in items] == ["EMP0001", "EMP0002", "EMP0003"]
    assert [x["rank"] for x in items] == [1, 2, 2]


def test_leaderboard_limit_smaller_than_late_count(client):
    for i in range(1, 6):
        client.post("/employees", json={
            "emp_code": f"EMP000{i}", "name": f"User {i}", "email": f"u{i}@test.com",
            "department": "IT", "joined_on": "2026-01-01",
            "shift_start": "09:30", "shift_end": "18:30"
        })
        app.main.db.attendance_logs.insert_one({
            "emp_code": f"EMP000{i}", "date": "2026-07-01", "late_minutes": i * 10
        })

    # Total 5 late employees. Request limit=3 -> exactly 3 employees with ranks 1, 2, 3
    res = client.get("/analytics/leaderboard/late?month=2026-07&limit=3")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 3
    assert [x["rank"] for x in items] == [1, 2, 3]
    assert items[0]["emp_code"] == "EMP0005"
    assert items[1]["emp_code"] == "EMP0004"
    assert items[2]["emp_code"] == "EMP0003"


def test_leaderboard_department_filter_and_restarts_at_one(client):
    # EMP0001 in HR: 100 min late
    # EMP0002 in IT: 50 min late
    # EMP0003 in IT: 30 min late
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "HR User", "email": "hr@test.com",
        "department": "HR", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0002", "name": "IT User 1", "email": "it1@test.com",
        "department": "IT", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    client.post("/employees", json={
        "emp_code": "EMP0003", "name": "IT User 2", "email": "it2@test.com",
        "department": "IT", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0001", "date": "2026-07-01", "late_minutes": 100})
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0002", "date": "2026-07-01", "late_minutes": 50})
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0003", "date": "2026-07-01", "late_minutes": 30})

    # Filter by IT: Ranking restarts at 1 for EMP0002
    res = client.get("/analytics/leaderboard/late?month=2026-07&department=IT")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 2
    assert items[0]["emp_code"] == "EMP0002" and items[0]["rank"] == 1
    assert items[1]["emp_code"] == "EMP0003" and items[1]["rank"] == 2


def test_leaderboard_weekend_record_and_unknown_employee(client):
    # Weekend late record counts
    # Log whose employee does not exist is ignored
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "Valid User", "email": "v@test.com",
        "department": "IT", "joined_on": "2026-01-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Saturday 2026-07-11 late 25 min for EMP0001
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP0001", "date": "2026-07-11", "late_minutes": 25})
    # Log for non-existent EMP9999
    app.main.db.attendance_logs.insert_one({"emp_code": "EMP9999", "date": "2026-07-11", "late_minutes": 50})

    res = client.get("/analytics/leaderboard/late?month=2026-07")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["emp_code"] == "EMP0001"
    assert items[0]["total_late_minutes"] == 25
    assert items[0]["late_count"] == 1
