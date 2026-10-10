import os
import pytest
from fastapi.testclient import TestClient

os.environ["MONGO_DB"] = "attendance_db_test"

import app.main

@pytest.fixture
def client():
    with TestClient(app.main.app) as c:
        assert app.main.db.name == "attendance_db_test", f"Safety check failed: DB is {app.main.db.name}"
        app.main.db.employees.delete_many({})
        app.main.db.attendance_logs.delete_many({})
        
        yield c
        
        assert app.main.db.name == "attendance_db_test", f"Safety check failed: DB is {app.main.db.name}"
        app.main.db.employees.delete_many({})
        app.main.db.attendance_logs.delete_many({})
