"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import math
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from enum import Enum
from pydantic import BaseModel, Field, model_validator, field_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

# Delay DB connection until startup so tests can import app without a live DB
client = None
db = None

@app.on_event("startup")
def startup_db_client():
    global client, db
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
    db = client[os.getenv("MONGO_DB", "attendance_db")]
    
    # Idempotent index creation
    db.employees.create_index("emp_code", unique=True)
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)

@app.on_event("shutdown")
def shutdown_db_client():
    if client:
        client.close()

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)

def from_epoch_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

def floor_to_second(dt: datetime) -> datetime:
    return dt.replace(microsecond=0)

def half_up(value: float, decimals: int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(f"1e-{decimals}"), rounding=ROUND_HALF_UP)

def ist_date_for_punch_in(punch_in_ist: datetime, shift_start: str, shift_end: str) -> str:
    eh, em = map(int, shift_end.split(":"))
    end_time_today = punch_in_ist.replace(hour=eh, minute=em, second=0, microsecond=0)
    is_overnight = shift_end <= shift_start
    if is_overnight and punch_in_ist < end_time_today:
        return (punch_in_ist - timedelta(days=1)).strftime("%Y-%m-%d")
    return punch_in_ist.strftime("%Y-%m-%d")

def shift_bounds_for_date(date_str: str, shift_start: str, shift_end: str) -> tuple[datetime, datetime]:
    d = datetime.fromisoformat(date_str)
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    start_dt = d.replace(hour=sh, minute=sm, tzinfo=IST)
    end_dt = d.replace(hour=eh, minute=em, tzinfo=IST)
    if shift_end <= shift_start: # Overnight shift
        end_dt += timedelta(days=1)
    return start_dt, end_dt

def late_minutes(punch_in_ist: datetime, shift_start_ist: datetime) -> int:
    delta_seconds = (punch_in_ist - shift_start_ist).total_seconds()
    if delta_seconds > 600:
        return math.floor(delta_seconds / 60)
    return 0

def overtime_minutes(punch_out_ist: datetime, shift_end_ist: datetime) -> int:
    mins = math.floor((punch_out_ist - shift_end_ist).total_seconds() / 60)
    return mins if mins >= 30 else 0

def work_hours(punch_in_ist: datetime, punch_out_ist: datetime) -> Decimal:
    secs = (punch_out_ist - punch_in_ist).total_seconds()
    return half_up(secs / 3600.0, 2)

def is_half_day(wh: Decimal) -> bool:
    return wh < Decimal("4.50")

def count_working_days(start_date: str, end_date: str) -> int:
    s = datetime.fromisoformat(start_date).date()
    e = datetime.fromisoformat(end_date).date()
    days = 0
    curr = s
    while curr <= e:
        if curr.weekday() < 5:
            days += 1
        curr += timedelta(days=1)
    return days

def compute_derived(punch_in: datetime, punch_out: Optional[datetime], employee: dict, date_str: str) -> dict:
    punch_in_ist = punch_in.astimezone(IST)
    shift_start_ist, shift_end_ist = shift_bounds_for_date(date_str, employee["shift_start"], employee["shift_end"])
    
    lm = late_minutes(punch_in_ist, shift_start_ist)
    if not punch_out:
        return {"late_minutes": lm, "work_hours": None, "overtime_minutes": 0, "half_day": False}
        
    punch_out_ist = punch_out.astimezone(IST)
    om = overtime_minutes(punch_out_ist, shift_end_ist)
    wh = work_hours(punch_in_ist, punch_out_ist)
    hd = is_half_day(wh)
    return {"late_minutes": lm, "work_hours": float(wh), "overtime_minutes": om, "half_day": hd}


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field("18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("name", "department")
    @classmethod
    def trim_and_check_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty after trimming")
        return v
        
    @field_validator("joined_on")
    @classmethod
    def validate_real_date(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError("must be a real calendar date")
        return v

    @model_validator(mode='after')
    def check_shifts(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PresenceStatus(str, Enum):
    PRESENT = "PRESENT"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: PresenceStatus = PresenceStatus.PRESENT

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unreachable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = to_epoch_ms(doc["created_at"])
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None, 
    page: int = Query(1, ge=1), 
    page_size: int = Query(20, ge=1, le=100)
):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size))
    for d in items:
        if "created_at" in d:
            d["created_at"] = to_epoch_ms(d["created_at"])
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    if body.punched_at is not None:
        ts = from_epoch_ms(body.punched_at)
    else:
        ts = datetime.now(timezone.utc)
    ts = floor_to_second(ts)

    d = ist_date_for_punch_in(ts.astimezone(IST), emp["shift_start"], emp["shift_end"])
    derived = compute_derived(ts, None, emp, d)

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status.value,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": derived["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")

    doc.pop("_id", None)
    doc["punch_in"] = to_epoch_ms(doc["punch_in"])
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    if body.punched_at is not None:
        ts = from_epoch_ms(body.punched_at)
    else:
        ts = datetime.now(timezone.utc)
    ts = floor_to_second(ts)

    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_out": None},
        sort=[("punch_in", -1)]
    )
    if not record:
        raise HTTPException(404, "no open punch-in found")
        
    punch_in_ts = record["punch_in"]
    # Force UTC aware for robust comparison if it's not already
    if punch_in_ts.tzinfo is None:
        punch_in_ts = punch_in_ts.replace(tzinfo=timezone.utc)
        
    if ts <= punch_in_ts:
        raise HTTPException(422, "punched_at must be strictly after punch_in")
        
    if ts > punch_in_ts + timedelta(hours=24):
        raise HTTPException(422, "cannot punch out more than 24 hours after punch_in")

    derived = compute_derived(punch_in_ts, ts, emp, record["date"])
    
    update_data = {
        "punch_out": ts,
        "work_hours": float(derived["work_hours"]),
        "overtime_minutes": derived["overtime_minutes"],
        "half_day": derived["half_day"]
    }
    
    res = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": update_data}
    )
    if res.modified_count == 0:
        raise HTTPException(409, "already punched out")
        
    doc = db.attendance_logs.find_one({"_id": record["_id"]})
    doc.pop("_id", None)
    doc["punch_in"] = to_epoch_ms(doc["punch_in"])
    doc["punch_out"] = to_epoch_ms(doc["punch_out"])
    return doc

#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
