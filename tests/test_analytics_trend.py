import pytest
from datetime import datetime, timezone
import app.main


def test_trend_validation(client):
    # Unknown department -> 404
    r = client.get("/analytics/departments/NonExistent/trend?from=2026-07-01&to=2026-07-07")
    assert r.status_code == 404

    # Seed employee
    res_post = client.post("/employees", json={
        "emp_code": "EMP0001",
        "name": "Alice",
        "department": "Engineering",
        "joined_on": "2026-07-01",
        "email": "alice@example.com"
    })
    assert res_post.status_code == 201

    # Invalid from date
    r = client.get("/analytics/departments/Engineering/trend?from=invalid&to=2026-07-07")
    assert r.status_code == 422

    # Invalid to date
    r = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-02-31")
    assert r.status_code == 422

    # from > to
    r = client.get("/analytics/departments/Engineering/trend?from=2026-07-10&to=2026-07-01")
    assert r.status_code == 422

    # Exactly 92 days passes (e.g. 2026-01-01 to 2026-04-02: 31 + 28 + 31 + 2 = 92 days)
    r_92 = client.get("/analytics/departments/Engineering/trend?from=2026-01-01&to=2026-04-02")
    assert r_92.status_code == 200
    assert len(r_92.json()["items"]) == 92

    # Exactly 92 days passes: 2026-07-01 to 2026-09-30 (31 + 31 + 30 = 92 days)
    r_q3 = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-09-30")
    assert r_q3.status_code == 200
    items_q3 = r_q3.json()["items"]
    assert len(items_q3) == 92
    assert items_q3[0]["date"] == "2026-07-01"
    assert items_q3[-1]["date"] == "2026-09-30"

    # 93 days returns 422 (2026-01-01 to 2026-04-03 is 93 days)
    r_93 = client.get("/analytics/departments/Engineering/trend?from=2026-01-01&to=2026-04-03")
    assert r_93.status_code == 422

    # One-day range passes
    r_1 = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-01")
    assert r_1.status_code == 200
    data_1 = r_1.json()
    assert set(data_1.keys()) == {"department", "items"}
    assert len(data_1["items"]) == 1


def test_trend_basic_and_moving_avg(client):
    # Seed 2 employees:
    # EMP0001 joined 2026-07-01
    # EMP0002 joined 2026-07-03
    client.post("/employees", json={
        "emp_code": "EMP0001",
        "name": "Alice",
        "department": "Engineering",
        "joined_on": "2026-07-01",
        "email": "alice@example.com"
    })
    client.post("/employees", json={
        "emp_code": "EMP0002",
        "name": "Bob",
        "department": "Engineering",
        "joined_on": "2026-07-03",
        "email": "bob@example.com"
    })

    # Add attendance logs:
    # 2026-07-01 (Wed): EMP0001 PRESENT late 15
    # 2026-07-02 (Thu): EMP0001 WFH half_day late 0
    # 2026-07-03 (Fri): EMP0001 ABSENT, EMP0002 PRESENT late 5
    # 2026-07-04 (Sat): EMP0001 PRESENT (weekend record)
    # 2026-07-05 (Sun): no record
    # 2026-07-06 (Mon): working day with no records
    # 2026-07-07 (Tue): working day with no records
    app.main.db.attendance_logs.insert_many([
        {"emp_code": "EMP0001", "date": "2026-07-01", "status": "PRESENT", "late_minutes": 15},
        {"emp_code": "EMP0001", "date": "2026-07-02", "status": "WFH", "half_day": True, "late_minutes": 0},
        {"emp_code": "EMP0001", "date": "2026-07-03", "status": "ABSENT", "late_minutes": 0},
        {"emp_code": "EMP0002", "date": "2026-07-03", "status": "PRESENT", "late_minutes": 5},
        {"emp_code": "EMP0001", "date": "2026-07-04", "status": "PRESENT", "late_minutes": 0},
    ])

    r = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07")
    assert r.status_code == 200
    data = r.json()

    assert set(data.keys()) == {"department", "items"}
    assert data["department"] == "Engineering"
    assert len(data["items"]) == 7

    day1 = data["items"][0]  # 2026-07-01 Wed
    assert day1["date"] == "2026-07-01"
    assert day1["is_working_day"] is True
    assert day1["headcount"] == 1
    assert day1["present_count"] == 1.0
    assert day1["late_count"] == 1
    assert day1["attendance_rate"] == 1.0
    assert day1["moving_avg_7d"] == 1.0

    day2 = data["items"][1]  # 2026-07-02 Thu
    assert day2["date"] == "2026-07-02"
    assert day2["is_working_day"] is True
    assert day2["headcount"] == 1
    assert day2["present_count"] == 0.5
    assert day2["late_count"] == 0
    assert day2["attendance_rate"] == 0.5
    # mean(1.0, 0.5) = 0.75
    assert day2["moving_avg_7d"] == 0.75

    day3 = data["items"][2]  # 2026-07-03 Fri
    assert day3["date"] == "2026-07-03"
    assert day3["is_working_day"] is True
    assert day3["headcount"] == 2  # Bob joined 2026-07-03
    assert day3["present_count"] == 1.0  # Bob 1.0, Alice 0
    assert day3["late_count"] == 1
    assert day3["attendance_rate"] == 0.5  # 1.0 / 2
    # mean(1.0, 0.5, 0.5) = 2.0 / 3 = 0.6667
    assert day3["moving_avg_7d"] == 0.6667

    day4 = data["items"][3]  # 2026-07-04 Sat
    assert day4["date"] == "2026-07-04"
    assert day4["is_working_day"] is False
    assert day4["headcount"] == 2
    assert day4["present_count"] == 1.0  # weekend present counted in present_count
    assert day4["attendance_rate"] is None
    # moving_avg_7d ignores null rates in window: mean(1.0, 0.5, 0.5) = 0.6667
    assert day4["moving_avg_7d"] == 0.6667

    day5 = data["items"][4]  # 2026-07-05 Sun
    assert day5["date"] == "2026-07-05"
    assert day5["is_working_day"] is False
    assert day5["headcount"] == 2
    assert day5["present_count"] == 0.0
    assert day5["attendance_rate"] is None
    assert day5["moving_avg_7d"] == 0.6667

    day6 = data["items"][5]  # 2026-07-06 Mon
    assert day6["date"] == "2026-07-06"
    assert day6["is_working_day"] is True
    assert day6["headcount"] == 2
    assert day6["present_count"] == 0.0
    assert day6["attendance_rate"] == 0.0  # working day with no records -> 0.0
    # mean(1.0, 0.5, 0.5, 0.0) = 2.0 / 4 = 0.5
    assert day6["moving_avg_7d"] == 0.5

    day7 = data["items"][6]  # 2026-07-07 Tue
    assert day7["date"] == "2026-07-07"
    assert day7["is_working_day"] is True
    assert day7["headcount"] == 2
    assert day7["present_count"] == 0.0
    assert day7["attendance_rate"] == 0.0
    # mean(1.0, 0.5, 0.5, 0.0, 0.0) = 2.0 / 5 = 0.4
    assert day7["moving_avg_7d"] == 0.4


def test_trend_headcount_zero_and_all_null_moving_avg(client):
    # Employee joins in future relative to the query window
    client.post("/employees", json={
        "emp_code": "EMP0001",
        "name": "Alice",
        "department": "Engineering",
        "joined_on": "2026-08-01",
        "email": "alice@example.com"
    })

    # Query range before joined_on
    r = client.get("/analytics/departments/Engineering/trend?from=2026-07-04&to=2026-07-05")
    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 2
    # Headcount is 0 on all days
    for item in data["items"]:
        assert item["headcount"] == 0
        assert item["attendance_rate"] is None
        assert item["moving_avg_7d"] is None


def test_trend_mid_range_headcount_change(client):
    # Member joins on 2026-07-03.
    # Query range: 2026-07-01 to 2026-07-05.
    # Headcount is 0 before joined_on (07-01, 07-02).
    # Headcount becomes 1 on 07-03, and remains 1 on 07-04 and 07-05.
    client.post("/employees", json={
        "emp_code": "EMP0001",
        "name": "Alice",
        "department": "Engineering",
        "joined_on": "2026-07-03",
        "email": "alice@example.com"
    })

    r = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-05")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 5

    assert items[0]["date"] == "2026-07-01"
    assert items[0]["headcount"] == 0
    assert items[0]["attendance_rate"] is None

    assert items[1]["date"] == "2026-07-02"
    assert items[1]["headcount"] == 0
    assert items[1]["attendance_rate"] is None

    assert items[2]["date"] == "2026-07-03"
    assert items[2]["headcount"] == 1

    assert items[3]["date"] == "2026-07-04"
    assert items[3]["headcount"] == 1

    assert items[4]["date"] == "2026-07-05"
    assert items[4]["headcount"] == 1


def test_trend_weekend_and_half_day_and_legacy_record(client):
    client.post("/employees", json={
        "emp_code": "EMP0001",
        "name": "Alice",
        "department": "Engineering",
        "joined_on": "2026-07-01",
        "email": "alice@example.com"
    })

    # Friday 2026-07-03: half-day record (WFH)
    # Saturday 2026-07-04: weekend record with late_minutes
    # Monday 2026-07-06: legacy record without half_day field, PRESENT
    app.main.db.attendance_logs.insert_many([
        {
            "emp_code": "EMP0001",
            "date": "2026-07-03",
            "status": "WFH",
            "half_day": True,
            "late_minutes": 0
        },
        {
            "emp_code": "EMP0001",
            "date": "2026-07-04",
            "status": "PRESENT",
            "half_day": False,
            "late_minutes": 25
        },
        {
            "emp_code": "EMP0001",
            "date": "2026-07-06",
            "status": "PRESENT",
            "late_minutes": 10
            # note: no half_day key -> legacy record
        }
    ])

    r = client.get("/analytics/departments/Engineering/trend?from=2026-07-03&to=2026-07-06")
    assert r.status_code == 200
    items = r.json()["items"]

    # 2026-07-03 (Fri): half-day record -> present_count 0.5, rate 0.5
    assert items[0]["date"] == "2026-07-03"
    assert items[0]["present_count"] == 0.5
    assert items[0]["is_working_day"] is True
    assert items[0]["attendance_rate"] == 0.5

    # 2026-07-04 (Sat): weekend record -> present_count 1.0, late_count 1, rate is null
    assert items[1]["date"] == "2026-07-04"
    assert items[1]["is_working_day"] is False
    assert items[1]["present_count"] == 1.0
    assert items[1]["late_count"] == 1
    assert items[1]["attendance_rate"] is None

    # 2026-07-05 (Sun): no records
    assert items[2]["date"] == "2026-07-05"
    assert items[2]["is_working_day"] is False
    assert items[2]["present_count"] == 0.0
    assert items[2]["attendance_rate"] is None

    # 2026-07-06 (Mon): legacy record -> half_day missing defaults to False -> present_count 1.0, late_count 1
    assert items[3]["date"] == "2026-07-06"
    assert items[3]["is_working_day"] is True
    assert items[3]["present_count"] == 1.0
    assert items[3]["late_count"] == 1
    assert items[3]["attendance_rate"] == 1.0


def test_trend_moving_average_window_eighth_day(client):
    # Query 10 days: 2026-07-01 (Wed) to 2026-07-10 (Fri)
    # Day 1: 2026-07-01 (Wed) -> rate 1.0
    # Day 2: 2026-07-02 (Thu) -> rate 1.0
    # Day 3: 2026-07-03 (Fri) -> rate 1.0
    # Day 4: 2026-07-04 (Sat) -> weekend -> rate null
    # Day 5: 2026-07-05 (Sun) -> weekend -> rate null
    # Day 6: 2026-07-06 (Mon) -> rate 1.0
    # Day 7: 2026-07-07 (Tue) -> rate 1.0
    # Day 8: 2026-07-08 (Wed) -> rate 0.5
    # Window for Day 8 (idx 7) is rows idx 1..7 (Day 2 to Day 8):
    # Excludes Day 1 (2026-07-01)!
    # Rates in window: [1.0 (Thu), 1.0 (Fri), null (Sat), null (Sun), 1.0 (Mon), 1.0 (Tue), 0.5 (Wed)]
    # Non-null values in window: 1.0, 1.0, 1.0, 1.0, 0.5 -> sum = 4.5, count = 5 -> mean = 0.9000
    client.post("/employees", json={
        "emp_code": "EMP0001",
        "name": "Alice",
        "department": "Engineering",
        "joined_on": "2026-07-01",
        "email": "alice@example.com"
    })

    app.main.db.attendance_logs.insert_many([
        {"emp_code": "EMP0001", "date": "2026-07-01", "status": "PRESENT", "late_minutes": 0},
        {"emp_code": "EMP0001", "date": "2026-07-02", "status": "PRESENT", "late_minutes": 0},
        {"emp_code": "EMP0001", "date": "2026-07-03", "status": "PRESENT", "late_minutes": 0},
        {"emp_code": "EMP0001", "date": "2026-07-06", "status": "PRESENT", "late_minutes": 0},
        {"emp_code": "EMP0001", "date": "2026-07-07", "status": "PRESENT", "late_minutes": 0},
        {"emp_code": "EMP0001", "date": "2026-07-08", "status": "WFH", "half_day": True, "late_minutes": 0},
    ])

    r = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-10")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 10

    day8 = items[7]  # 2026-07-08
    assert day8["date"] == "2026-07-08"
    assert day8["attendance_rate"] == 0.5
    assert day8["moving_avg_7d"] == 0.9  # 4.5 / 5 = 0.9
