import os
import pytest
from fastapi.testclient import TestClient

# Must set test DB before importing app
os.environ["MONGO_DB"] = "attendance_db_test"

from app.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        # DB is initialized by startup event
        from app.main import db
        db.employees.delete_many({})
        yield c
        db.employees.delete_many({})

def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

def test_create_employee_validation(client):
    # Missing required fields
    res = client.post("/employees", json={"emp_code": "EMP0001"})
    assert res.status_code == 422
    
    # Bad emp_code pattern
    res = client.post("/employees", json={
        "emp_code": "WRONG01", "name": "A", "email": "a@b.com", 
        "department": "IT", "joined_on": "2026-07-06"
    })
    assert res.status_code == 422
    
    # Same shift start and end
    res = client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@b.com", 
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "09:30"
    })
    assert res.status_code == 422

def test_create_employee_success(client):
    payload = {
        "emp_code": "EMP0001", "name": "A", "email": "a@b.com", 
        "department": "IT", "joined_on": "2026-07-06"
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == "EMP0001"
    assert "created_at" in data
    assert isinstance(data["created_at"], int) # epoch ms
    assert "_id" not in data

def test_create_employee_duplicate(client):
    payload = {
        "emp_code": "EMP0001", "name": "A", "email": "a@b.com", 
        "department": "IT", "joined_on": "2026-07-06"
    }
    client.post("/employees", json=payload)
    res2 = client.post("/employees", json=payload)
    assert res2.status_code == 409

def test_list_employees(client):
    client.post("/employees", json={
        "emp_code": "EMP0001", "name": "A", "email": "a@b.com", 
        "department": "IT", "joined_on": "2026-07-06"
    })
    client.post("/employees", json={
        "emp_code": "EMP0002", "name": "B", "email": "b@b.com", 
        "department": "HR", "joined_on": "2026-07-06"
    })
    
    # Sort and format check
    res = client.get("/employees")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["items"][0]["emp_code"] == "EMP0001"
    assert "_id" not in data["items"][0]
    
    # Filter by department
    res = client.get("/employees?department=HR")
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["emp_code"] == "EMP0002"
    
    # Pagination skip
    res = client.get("/employees?page=2&page_size=1")
    data = res.json()
    assert data["total"] == 2 # total of filtered set (all docs)
    assert len(data["items"]) == 1
    assert data["items"][0]["emp_code"] == "EMP0002"

def test_create_employee_validation_tight(client):
    base_payload = {
        "emp_code": "EMP0003", "name": "A", "email": "a@b.com", 
        "department": "IT", "joined_on": "2026-07-06",
        "shift_start": "09:30", "shift_end": "18:30"
    }
    
    # 1. joined_on must be a real date
    for bad_date in ["3989-87-38", "2026-13-45", "2026-02-30"]:
        p = base_payload.copy()
        p["joined_on"] = bad_date
        assert client.post("/employees", json=p).status_code == 422
        
    # 2. shift_start/shift_end invalid HH:MM
    for bad_time in ["25:00", "9:30", "09:60"]:
        p = base_payload.copy()
        p["shift_start"] = bad_time
        assert client.post("/employees", json=p).status_code == 422
        
    # 3. name and department empty after trimming
    for bad_str in [" ", "   ", "\t"]:
        p = base_payload.copy()
        p["name"] = bad_str
        assert client.post("/employees", json=p).status_code == 422
        
        p = base_payload.copy()
        p["department"] = bad_str
        assert client.post("/employees", json=p).status_code == 422

