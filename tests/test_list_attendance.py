import pytest
from datetime import datetime, timezone
from app.main import to_epoch_ms

def test_list_attendance_filters_and_paging(client):
    from app.main import db
    # Create some legacy docs manually via db
    db.attendance_logs.insert_many([
        {"emp_code": "EMP0001", "date": "2026-07-06", "status": "PRESENT", "punch_in": datetime(2026, 7, 6, 4, tzinfo=timezone.utc), "punch_out": None},
        {"emp_code": "EMP0002", "date": "2026-07-06", "status": "ABSENT", "punch_in": None, "punch_out": None},
        {"emp_code": "EMP0001", "date": "2026-07-07", "status": "PRESENT", "punch_in": datetime(2026, 7, 7, 4, tzinfo=timezone.utc), "punch_out": None},
        {"emp_code": "EMP0003", "date": "2026-07-07", "status": "PRESENT", "punch_in": datetime(2026, 7, 7, 5, tzinfo=timezone.utc), "punch_out": None},
    ])
    
    # All records, sorted date desc, emp_code asc
    res1 = client.get("/attendance")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["total"] == 4
    items1 = data1["items"]
    # Order: EMP0001 7-07, EMP0003 7-07, EMP0001 7-06, EMP0002 7-06
    assert items1[0]["emp_code"] == "EMP0001" and items1[0]["date"] == "2026-07-07"
    assert items1[1]["emp_code"] == "EMP0003" and items1[1]["date"] == "2026-07-07"
    assert items1[2]["emp_code"] == "EMP0001" and items1[2]["date"] == "2026-07-06"
    assert items1[3]["emp_code"] == "EMP0002" and items1[3]["date"] == "2026-07-06"
    
    # Check legacy fields
    for d in items1:
        assert "id" not in d
        assert "_id" not in d
        assert d["half_day"] is False
        assert d["history"] == []
        if d["punch_in"]:
            assert isinstance(d["punch_in"], int)
            
    # Filters: date_from
    res2 = client.get("/attendance?date_from=2026-07-07")
    data2 = res2.json()
    assert data2["total"] == 2
    
    # Filters: date_to
    res3 = client.get("/attendance?date_to=2026-07-06")
    data3 = res3.json()
    assert data3["total"] == 2
    
    # Filters: status
    res4 = client.get("/attendance?status=ABSENT")
    data4 = res4.json()
    assert data4["total"] == 1
    assert data4["items"][0]["emp_code"] == "EMP0002"

    # Paging boundaries
    res5 = client.get("/attendance?page=2&page_size=2")
    data5 = res5.json()
    assert data5["total"] == 4
    assert len(data5["items"]) == 2
    assert data5["items"][0]["emp_code"] == "EMP0001" and data5["items"][0]["date"] == "2026-07-06"

def test_list_attendance_validation(client):
    # 422 Invalid date
    assert client.get("/attendance?date_from=2026-13-45").status_code == 422
    assert client.get("/attendance?date_from=2026-02-30").status_code == 422
    
    # 422 date_from > date_to
    assert client.get("/attendance?date_from=2026-07-10&date_to=2026-07-05").status_code == 422
    
    # 422 Invalid status
    assert client.get("/attendance?status=INVALID_STATUS").status_code == 422
    
    # 422 Page constraints
    assert client.get("/attendance?page=0").status_code == 422
    assert client.get("/attendance?page_size=101").status_code == 422
