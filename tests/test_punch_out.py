import pytest
from datetime import datetime, timezone
import concurrent.futures
from app.main import to_epoch_ms, IST

def test_punch_out_normal_and_overtime(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@a.com", 
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })

    # Normal punch-out
    # 09:28 IST -> 03:58 UTC
    pi1 = datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi1)})
    
    # out 18:35 IST -> 13:05 UTC
    po1 = datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc)
    res1 = client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(po1)})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["work_hours"] == 9.12
    assert data1["overtime_minutes"] == 0
    assert data1["half_day"] is False

    # Overtime punch-out
    # 09:30 IST -> 04:00 UTC (Next day)
    pi2 = datetime(2026, 7, 7, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(pi2)})
    
    # out 19:10 IST -> 13:40 UTC
    po2 = datetime(2026, 7, 7, 13, 40, tzinfo=timezone.utc)
    res2 = client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": to_epoch_ms(po2)})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["overtime_minutes"] == 40

def test_punch_out_half_day(client):
    client.post("/employees", json={
        "emp_code": "EMP0002", "name": "B", "email": "b@b.com", 
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    })

    # Under 4.5 hours
    pi1 = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0002", "punched_at": to_epoch_ms(pi1)})
    
    po1 = datetime(2026, 7, 6, 8, 29, tzinfo=timezone.utc)
    res1 = client.post("/attendance/punch-out", json={"emp_code": "EMP0002", "punched_at": to_epoch_ms(po1)})
    assert res1.json()["half_day"] is True

    # Exactly 4.5 hours
    pi2 = datetime(2026, 7, 7, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0002", "punched_at": to_epoch_ms(pi2)})
    
    po2 = datetime(2026, 7, 7, 8, 30, tzinfo=timezone.utc)
    res2 = client.post("/attendance/punch-out", json={"emp_code": "EMP0002", "punched_at": to_epoch_ms(po2)})
    assert res2.json()["half_day"] is False

def test_punch_out_overnight(client):
    client.post("/employees", json={
        "emp_code": "EMP0003", "name": "C", "email": "c@c.com", 
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "22:00", "shift_end": "06:00"
    })
    
    # in 21:55 IST
    pi = datetime(2026, 7, 6, 21, 55, tzinfo=IST).astimezone(timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0003", "punched_at": to_epoch_ms(pi)})
    
    # out 06:40 IST next day
    po = datetime(2026, 7, 7, 6, 40, tzinfo=IST).astimezone(timezone.utc)
    res = client.post("/attendance/punch-out", json={"emp_code": "EMP0003", "punched_at": to_epoch_ms(po)})
    assert res.status_code == 200
    data = res.json()
    assert data["overtime_minutes"] == 40
    assert data["work_hours"] == 8.75

def test_punch_out_errors(client):
    client.post("/employees", json={
        "emp_code": "EMP0004", "name": "D", "email": "d@d.com", 
        "department": "IT", "joined_on": "2026-07-06"
    })
    
    # No open punch-in
    res = client.post("/attendance/punch-out", json={"emp_code": "EMP0004"})
    assert res.status_code == 404
    
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(pi)})
    
    # punched_at before punch_in
    po_early = datetime(2026, 7, 6, 3, 59, tzinfo=timezone.utc)
    assert client.post("/attendance/punch-out", json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(po_early)}).status_code == 422
    
    # punched_at exactly punch_in
    assert client.post("/attendance/punch-out", json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(pi)}).status_code == 422

    # more than 24 hours
    po_late = datetime(2026, 7, 7, 4, 1, tzinfo=timezone.utc)
    assert client.post("/attendance/punch-out", json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(po_late)}).status_code == 422
    
    # Successful punch out
    po_good = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    assert client.post("/attendance/punch-out", json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(po_good)}).status_code == 200
    
    # Second punch out -> 404 (Sequential)
    res2 = client.post("/attendance/punch-out", json={"emp_code": "EMP0004", "punched_at": to_epoch_ms(po_good)})
    assert res2.status_code == 404

def test_punch_out_concurrency(client):
    client.post("/employees", json={
        "emp_code": "EMP0005", "name": "E", "email": "e@e.com", 
        "department": "IT", "joined_on": "2026-07-06"
    })
    
    pi = datetime(2026, 7, 6, 4, 0, tzinfo=timezone.utc)
    client.post("/attendance/punch-in", json={"emp_code": "EMP0005", "punched_at": to_epoch_ms(pi)})
    
    po = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    payload = {"emp_code": "EMP0005", "punched_at": to_epoch_ms(po)}
    
    def punch():
        return client.post("/attendance/punch-out", json=payload).status_code
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: punch(), range(10)))
        
    assert results.count(200) == 1
    assert results.count(409) + results.count(404) == 9
