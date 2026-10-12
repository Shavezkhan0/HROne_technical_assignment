import pytest
from datetime import datetime, timezone, timedelta
import concurrent.futures
from app.main import to_epoch_ms, IST

def test_regularize_404(client):
    # Unknown employee
    res = client.patch("/attendance/EMP9999/2026-07-06", json={
        "reason": "Correcting time", "regularized_by": "admin"
    })
    assert res.status_code == 404

    # Known employee, missing record
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    res2 = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "Correcting time", "regularized_by": "admin"
    })
    assert res2.status_code == 404


def test_regularize_validation_422(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Punch in 09:30 IST on 2026-07-06 (04:00 UTC)
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi)})

    # 1. Missing reason or reason too short (< 5 chars)
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "regularized_by": "admin", "reason": "bad"
    }).status_code == 422
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "regularized_by": "admin"
    }).status_code == 422

    # 2. Missing regularized_by or empty
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": ""
    }).status_code == 422

    # 3. Invalid status
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin", "status": "UNKNOWN"
    }).status_code == 422

    # 4. Strict type validation: seconds-looking values, floats, strings for punch_in/punch_out
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin", "punch_in": 1783312500 # seconds -> < 100000000000
    }).status_code == 422
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin", "punch_in": "1783312500000"
    }).status_code == 422
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin", "punch_in": 1783312500000.5
    }).status_code == 422

    # 5. ABSENT/LEAVE cannot have punch times supplied
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin", "status": "ABSENT",
        "punch_in": to_epoch_ms(pi)
    }).status_code == 422
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin", "status": "LEAVE",
        "punch_out": to_epoch_ms(pi + timedelta(hours=8))
    }).status_code == 422

    # 6. punch_out before punch_in or exactly punch_in
    po_early = pi - timedelta(minutes=10)
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin",
        "punch_out": to_epoch_ms(po_early)
    }).status_code == 422
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin",
        "punch_out": to_epoch_ms(pi)
    }).status_code == 422

    # 7. punch_out more than 24 hours after punch_in
    po_late = pi + timedelta(hours=24, minutes=1)
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin",
        "punch_out": to_epoch_ms(po_late)
    }).status_code == 422


def test_regularize_punch_in_date_preservation(client):
    # Employee with standard shift 09:30 - 18:30
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi)})

    # Regularize punch_in to next calendar day in IST -> 422
    pi_wrong_day = datetime(2026, 7, 7, 4, 0, tzinfo=timezone.utc)
    res = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "valid reason", "regularized_by": "admin",
        "punch_in": to_epoch_ms(pi_wrong_day)
    })
    assert res.status_code == 422

    # Employee with overnight shift 22:00 - 06:00
    client.post("/employees", json={
        "emp_code": "EMP0002", "name": "B", "email": "b@b.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "22:00", "shift_end": "06:00"
    })
    # Punch in 2026-07-06 21:55 IST -> record date 2026-07-06
    pi_overnight = datetime(2026, 7, 6, 21, 55, tzinfo=IST).astimezone(timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0002", "punched_at": to_epoch_ms(pi_overnight)})

    # Regularize punch_in to 2026-07-07 05:30 IST (< 06:00) -> belongs to shift started 2026-07-06! Valid!
    pi_valid_overnight = datetime(2026, 7, 7, 5, 30, tzinfo=IST).astimezone(timezone.utc)
    res_valid = client.patch("/attendance/EMP0002/2026-07-06", json={
        "reason": "valid overnight correction", "regularized_by": "admin",
        "punch_in": to_epoch_ms(pi_valid_overnight)
    })
    assert res_valid.status_code == 200

    # Regularize punch_in to 2026-07-07 06:05 IST (>= 06:00) -> belongs to 2026-07-07! 422 for date 2026-07-06
    pi_invalid_overnight = datetime(2026, 7, 7, 6, 5, tzinfo=IST).astimezone(timezone.utc)
    res_invalid = client.patch("/attendance/EMP0002/2026-07-06", json={
        "reason": "crosses cutoff", "regularized_by": "admin",
        "punch_in": to_epoch_ms(pi_invalid_overnight)
    })
    assert res_invalid.status_code == 422


def test_regularize_nothing_changed_422(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi)})

    # Same status
    res = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "nothing changed", "regularized_by": "admin",
        "status": "PRESENT"
    })
    assert res.status_code == 422


def test_regularize_status_only(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi)})

    res = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "WFH approved", "regularized_by": "manager",
        "status": "WFH"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "WFH"
    assert len(data["history"]) == 1
    h = data["history"][0]
    assert h["by"] == "manager"
    assert h["reason"] == "WFH approved"
    assert "at" in h and isinstance(h["at"], int)
    assert h["changes"] == {"status": {"from": "PRESENT", "to": "WFH"}}


def test_regularize_punch_in_and_late_minutes(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Punch in 10:05 IST (35 min late) -> 04:35 UTC
    pi_late = datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi_late)})

    # Correct punch-in to 09:28 IST (on time) -> 03:58 UTC
    pi_ontime = datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc)
    res = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "biometric glitch", "regularized_by": "hr.admin",
        "punch_in": to_epoch_ms(pi_ontime)
    })
    assert res.status_code == 200
    data = res.json()
    assert data["late_minutes"] == 0
    assert data["punch_in"] == to_epoch_ms(pi_ontime)
    assert len(data["history"]) == 1
    ch = data["history"][0]["changes"]
    assert ch["punch_in"] == {"from": to_epoch_ms(pi_late), "to": to_epoch_ms(pi_ontime)}
    assert ch["late_minutes"] == {"from": 35, "to": 0}
    assert "work_hours" not in ch


def test_regularize_punch_out_derived_fields(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # in 09:30 IST -> 04:00 UTC
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi)})
    # out 13:00 IST -> 07:30 UTC (3.5 hours -> half day)
    po1 = datetime(2026, 7, 6, 7, 30, tzinfo=timezone.utc)
    client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(po1)})

    # Regularize punch_out to 19:10 IST -> 13:40 UTC (9.67 hours, 40 min overtime, half_day false)
    po2 = datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc)
    res = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "Forgot to punch out on time", "regularized_by": "manager",
        "punch_out": to_epoch_ms(po2)
    })
    assert res.status_code == 200
    data = res.json()
    assert data["work_hours"] == 9.67
    assert data["overtime_minutes"] == 40
    assert data["half_day"] is False
    ch = data["history"][0]["changes"]
    assert ch["punch_out"] == {"from": to_epoch_ms(po1), "to": to_epoch_ms(po2)}
    assert ch["work_hours"] == {"from": 3.5, "to": 9.67}
    assert ch["overtime_minutes"] == {"from": 0, "to": 40}
    assert ch["half_day"] == {"from": True, "to": False}


def test_regularize_present_to_leave_clears_derived(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    pi = datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc) # late 35 min
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi)})
    po = datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc) # overtime 40 min
    client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(po)})

    res = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "Applied for emergency leave", "regularized_by": "hr",
        "status": "LEAVE"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "LEAVE"
    assert data["punch_in"] is None
    assert data["punch_out"] is None
    assert data["work_hours"] is None
    assert data["late_minutes"] == 0
    assert data["overtime_minutes"] == 0
    assert data["half_day"] is False
    ch = data["history"][0]["changes"]
    assert ch["status"] == {"from": "PRESENT", "to": "LEAVE"}
    assert ch["punch_in"] == {"from": to_epoch_ms(pi), "to": None}
    assert ch["punch_out"] == {"from": to_epoch_ms(po), "to": None}
    assert ch["work_hours"] == {"from": 9.08, "to": None}
    assert ch["late_minutes"] == {"from": 35, "to": 0}
    assert ch["overtime_minutes"] == {"from": 40, "to": 0}


def test_regularize_absent_to_present_with_times(client):
    import app.main
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Directly seed an ABSENT record as seed data would do
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0001", "date": "2026-07-06", "status": "ABSENT",
        "punch_in": None, "punch_out": None, "work_hours": None,
        "late_minutes": 0, "overtime_minutes": 0, "half_day": False,
        "history": []
    })

    # Cannot regularize to PRESENT without supplying punch_in
    assert client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "marked present", "regularized_by": "admin", "status": "PRESENT"
    }).status_code == 422

    # Regularize with punch_in and punch_out
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    po = datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc)
    res = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "Was present on site", "regularized_by": "admin",
        "status": "PRESENT",
        "punch_in": to_epoch_ms(pi),
        "punch_out": to_epoch_ms(po)
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "PRESENT"
    assert data["punch_in"] == to_epoch_ms(pi)
    assert data["punch_out"] == to_epoch_ms(po)
    assert data["work_hours"] == 9.0
    ch = data["history"][0]["changes"]
    assert ch["status"] == {"from": "ABSENT", "to": "PRESENT"}
    assert ch["punch_in"] == {"from": None, "to": to_epoch_ms(pi)}
    assert ch["punch_out"] == {"from": None, "to": to_epoch_ms(po)}
    assert ch["work_hours"] == {"from": None, "to": 9.0}


def test_regularize_sequential_history_order(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi)})

    # First PATCH: change status to WFH
    res1 = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "First correction", "regularized_by": "admin1",
        "status": "WFH"
    })
    assert res1.status_code == 200
    assert len(res1.json()["history"]) == 1

    # Second PATCH: change status to ON_DUTY
    res2 = client.patch("/attendance/EMP0001/2026-07-06", json={
        "reason": "Second correction", "regularized_by": "admin2",
        "status": "ON_DUTY"
    })
    assert res2.status_code == 200
    data = res2.json()
    assert len(data["history"]) == 2
    assert data["history"][0]["by"] == "admin1"
    assert data["history"][0]["reason"] == "First correction"
    assert data["history"][0]["changes"] == {"status": {"from": "PRESENT", "to": "WFH"}}
    assert data["history"][1]["by"] == "admin2"
    assert data["history"][1]["reason"] == "Second correction"
    assert data["history"][1]["changes"] == {"status": {"from": "WFH", "to": "ON_DUTY"}}

    # Verify GET /attendance also serializes history epoch ms properly
    res_list = client.get("/attendance?emp_code=EMP0001")
    assert res_list.status_code == 200
    rec = res_list.json()["items"][0]
    assert len(rec["history"]) == 2
    assert isinstance(rec["history"][0]["at"], int)
    assert isinstance(rec["history"][1]["at"], int)


def test_regularize_concurrency(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com",
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi)})

    statuses = ["WFH", "ON_DUTY"]

    def patch_it(idx):
        st = statuses[idx % len(statuses)]
        return client.patch("/attendance/EMP0001/2026-07-06", json={
            "reason": f"Race attempt {idx}",
            "regularized_by": f"thread_{idx}",
            "status": st
        }).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(patch_it, range(10)))

    # Every success should add exactly one history entry, and losers get 409 (or 422 if same status won previously)
    successes = results.count(200)
    conflicts = results.count(409) + results.count(422)
    assert successes >= 1
    assert successes + conflicts == 10

    # Check database document
    import app.main
    doc = app.main.db.attendance_logs.find_one({"emp_code": "EMP0001", "date": "2026-07-06"})
    assert len(doc["history"]) == successes


def test_regularize_legacy_record_and_concurrency(client):
    import app.main
    client.post("/employees", json={
        "emp_code": "EMP0002", "name": "B", "email": "b@b.com",
        "department": "IT", "joined_on": "2026-07-01",
        "shift_start": "09:30", "shift_end": "18:30"
    })
    # Seed legacy record EMP0002 2026-07-14 (no half_day, no history)
    pi = datetime(2026, 7, 14, 4, 2, tzinfo=timezone.utc)
    po = datetime(2026, 7, 14, 13, 1, tzinfo=timezone.utc)
    app.main.db.attendance_logs.insert_one({
        "emp_code": "EMP0002",
        "date": "2026-07-14",
        "status": "PRESENT",
        "punch_in": pi,
        "punch_out": po,
        "work_hours": 8.98,
        "late_minutes": 0,
        "overtime_minutes": 0
        # No half_day, no history
    })

    # Test concurrent regularizations on legacy record (10 threads)
    def patch_legacy(idx):
        st = "WFH" if idx % 2 == 0 else "ON_DUTY"
        return client.patch("/attendance/EMP0002/2026-07-14", json={
            "reason": f"Legacy race attempt {idx}",
            "regularized_by": f"thread_{idx}",
            "status": st
        }).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(patch_legacy, range(10)))

    successes = results.count(200)
    conflicts = results.count(409) + results.count(422)
    assert successes >= 1
    assert successes + conflicts == 10

    doc = app.main.db.attendance_logs.find_one({"emp_code": "EMP0002", "date": "2026-07-14"})
    assert "history" in doc
    assert len(doc["history"]) == successes
    assert "half_day" in doc
