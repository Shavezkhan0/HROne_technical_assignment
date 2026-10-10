import pytest
from datetime import datetime, timezone
import concurrent.futures
from app.main import to_epoch_ms, IST

def test_punch_in_invalid_body_and_404(client):
    # 422 Invalid body
    assert client.post("/attendance/punch-in", json={}).status_code == 422
    assert client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "status": "INVALID"}).status_code == 422

    # 404 Not Found
    assert client.post("/attendance/punch-in", json={"emp_code": "EMP9999"}).status_code == 404

def test_punch_in_normal_and_late(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com", 
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })

    # Normal punch-in (on time: 09:30 IST)
    # 2026-07-06 09:30 IST is 04:00 UTC
    pi_normal = datetime(2026, 7, 6, 4, 0, 0, 900000, tzinfo=timezone.utc)
    ms_normal = to_epoch_ms(pi_normal)
    
    res = client.post("/attendance/punch-in", json={
        "emp_code": "EMP0001",
        "punched_at": ms_normal
    })
    assert res.status_code == 201
    data = res.json()
    assert "_id" not in data
    assert data["emp_code"] == "EMP0001"
    assert data["date"] == "2026-07-06"
    assert data["status"] == "PRESENT"
    # truncated to 0 seconds
    pi_trunc = datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc)
    assert data["punch_in"] == to_epoch_ms(pi_trunc)
    assert data["late_minutes"] == 0
    assert data["punch_out"] is None
    assert data["work_hours"] is None
    assert data["overtime_minutes"] == 0
    assert data["half_day"] is False
    assert data["history"] == []
    
    # Late punch-in (10:05 IST -> 35 minutes late) on next day
    pi_late = datetime(2026, 7, 7, 4, 35, 0, tzinfo=timezone.utc)
    res2 = client.post("/attendance/punch-in", json={
        "emp_code": "EMP0001",
        "punched_at": to_epoch_ms(pi_late)
    })
    assert res2.status_code == 201
    assert res2.json()["late_minutes"] == 35

def test_punch_in_overnight(client):
    client.post("/employees", json={
        "emp_code": "EMP0002", "name": "B", "email": "b@b.com", 
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "22:00", "shift_end": "06:00"
    })
    
    # punch-in at 21:55 IST on 2026-07-06 -> belongs to 2026-07-06
    pi_1 = datetime(2026, 7, 6, 21, 55, tzinfo=IST).astimezone(timezone.utc)
    res1 = client.post("/attendance/punch-in", json={
        "emp_code": "EMP0002",
        "punched_at": to_epoch_ms(pi_1)
    })
    assert res1.status_code == 201
    assert res1.json()["date"] == "2026-07-06"
    
    # punch-in at 05:59 IST on 2026-07-08 -> belongs to previous date (2026-07-07)
    pi_2 = datetime(2026, 7, 8, 5, 59, tzinfo=IST).astimezone(timezone.utc)
    res2 = client.post("/attendance/punch-in", json={
        "emp_code": "EMP0002",
        "punched_at": to_epoch_ms(pi_2)
    })
    assert res2.status_code == 201
    assert res2.json()["date"] == "2026-07-07"

def test_punch_in_duplicate(client):
    client.post("/employees", json={
        "emp_code": "EMP0003", "name": "C", "email": "c@c.com", 
        "department": "IT", "joined_on": "2026-07-06"
    })
    
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    payload = {"emp_code": "EMP0003", "punched_at": to_epoch_ms(pi)}
    
    res1 = client.post("/attendance/punch-in", json=payload)
    assert res1.status_code == 201
    
    res2 = client.post("/attendance/punch-in", json=payload)
    assert res2.status_code == 409

def test_punch_in_concurrency(client):
    client.post("/employees", json={
        "emp_code": "EMP0004", "name": "D", "email": "d@d.com", 
        "department": "IT", "joined_on": "2026-07-06"
    })
    
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    payload = {"emp_code": "EMP0004", "punched_at": to_epoch_ms(pi)}
    
    def punch():
        return client.post("/attendance/punch-in", json=payload).status_code
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: punch(), range(10)))
        
    assert results.count(201) == 1
    assert results.count(409) == 9
