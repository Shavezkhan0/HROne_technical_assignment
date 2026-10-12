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
import calendar
from datetime import datetime, timedelta, timezone, date
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from bson import json_util
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
    db.employees.create_index("joined_on")
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("date", 1), ("late_minutes", 1)])

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
def format_attendance_doc(d: dict) -> dict:
    d.pop("_id", None)
    d.pop("id", None)
    
    if "half_day" not in d:
        d["half_day"] = False
    if "history" not in d or d["history"] is None:
        d["history"] = []
    if "work_hours" not in d:
        d["work_hours"] = None
    if "late_minutes" not in d:
        d["late_minutes"] = 0
    if "overtime_minutes" not in d:
        d["overtime_minutes"] = 0

    if d.get("punch_in") is not None:
        if isinstance(d["punch_in"], datetime):
            d["punch_in"] = to_epoch_ms(d["punch_in"])
    else:
        d["punch_in"] = None

    if d.get("punch_out") is not None:
        if isinstance(d["punch_out"], datetime):
            d["punch_out"] = to_epoch_ms(d["punch_out"])
    else:
        d["punch_out"] = None

    formatted_history = []
    for h in d.get("history", []):
        h_copy = dict(h)
        if isinstance(h_copy.get("at"), datetime):
            h_copy["at"] = to_epoch_ms(h_copy["at"])
        elif isinstance(h_copy.get("timestamp"), datetime):
            h_copy["at"] = to_epoch_ms(h_copy.pop("timestamp"))
        
        changes = h_copy.get("changes", {})
        formatted_changes = {}
        for f, diff in changes.items():
            diff_copy = dict(diff)
            if f in ("punch_in", "punch_out"):
                for k in ("from", "to"):
                    if isinstance(diff_copy.get(k), datetime):
                        diff_copy[k] = to_epoch_ms(diff_copy[k])
            formatted_changes[f] = diff_copy
        h_copy["changes"] = formatted_changes
        formatted_history.append(h_copy)
    d["history"] = formatted_history
    return d


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

class Status(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"

class ExplainEndpointName(str, Enum):
    attendance_list = "attendance_list"
    employee_monthly = "employee_monthly"
    department_summary = "department_summary"
    late_leaderboard = "late_leaderboard"
    department_trend = "department_trend"

def validate_epoch_ms_value(v: any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("instant must be an integer epoch milliseconds")
    if v < 100000000000 or v > 4102444800000:
        raise ValueError("instant epoch milliseconds out of range")
    return v

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: PresenceStatus = PresenceStatus.PRESENT

    @field_validator("punched_at", mode="before")
    @classmethod
    def check_punched_at(cls, v):
        return validate_epoch_ms_value(v)

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at", mode="before")
    @classmethod
    def check_punched_at(cls, v):
        return validate_epoch_ms_value(v)

class RegularizeRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)
    status: Optional[Status] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None

    @field_validator("punch_in", "punch_out", mode="before")
    @classmethod
    def check_epoch_ms(cls, v):
        return validate_epoch_ms_value(v)


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

    return format_attendance_doc(doc)


def build_attendance_list_query(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None
) -> dict:
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
    return q


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: Optional[Status] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from:
        try:
            date.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(422, "invalid date_from")
    if date_to:
        try:
            date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(422, "invalid date_to")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be after date_to")

    q = build_attendance_list_query(
        emp_code=emp_code,
        date_from=date_from,
        date_to=date_to,
        status=status.value if status else None
    )

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    
    docs = list(
        db.attendance_logs.find(q)
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    items = [format_attendance_doc(d) for d in docs]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


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
    return format_attendance_doc(doc)


@app.patch("/attendance/{emp_code}/{date}", status_code=200)
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "attendance record not found")

    now_utc = floor_to_second(datetime.now(timezone.utc))

    sent_data = body.model_dump(exclude_unset=True)
    has_status_sent = "status" in sent_data and sent_data["status"] is not None
    has_punch_in_sent = "punch_in" in sent_data and sent_data["punch_in"] is not None
    has_punch_out_sent = "punch_out" in sent_data and sent_data["punch_out"] is not None

    target_status = sent_data["status"].value if has_status_sent else record["status"]

    # Rules: setting status ABSENT or LEAVE clears both punch times, so supplying punch_in or punch_out with it is 422
    if target_status in ("ABSENT", "LEAVE"):
        if has_punch_in_sent or has_punch_out_sent:
            raise HTTPException(422, f"status {target_status} cannot have punch times supplied")
        new_punch_in_dt = None
        new_punch_out_dt = None
    else:
        # Presence status requires a punch_in (supplied or already stored)
        if has_punch_in_sent:
            new_punch_in_dt = floor_to_second(from_epoch_ms(sent_data["punch_in"]))
        else:
            old_pi = record.get("punch_in")
            if not old_pi:
                raise HTTPException(422, "presence status requires a punch_in")
            if old_pi.tzinfo is None:
                old_pi = old_pi.replace(tzinfo=timezone.utc)
            new_punch_in_dt = old_pi

        if has_punch_out_sent:
            new_punch_out_dt = floor_to_second(from_epoch_ms(sent_data["punch_out"]))
        else:
            old_po = record.get("punch_out")
            if old_po is not None and old_po.tzinfo is None:
                old_po = old_po.replace(tzinfo=timezone.utc)
            new_punch_out_dt = old_po

        # punch_in must stay on the record's attendance date (R1)
        computed_date = ist_date_for_punch_in(new_punch_in_dt.astimezone(IST), emp["shift_start"], emp["shift_end"])
        if computed_date != date:
            raise HTTPException(422, f"punch_in falls on date {computed_date}, must stay on record date {date}")

        # punch_out must be after punch_in and within 24 hours
        if new_punch_out_dt is not None:
            if new_punch_out_dt <= new_punch_in_dt:
                raise HTTPException(422, "punch_out must be strictly after punch_in")
            if new_punch_out_dt > new_punch_in_dt + timedelta(hours=24):
                raise HTTPException(422, "punch_out cannot be more than 24 hours after punch_in")

    # Compute new derived values
    if target_status in ("ABSENT", "LEAVE"):
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        derived = compute_derived(new_punch_in_dt, new_punch_out_dt, emp, date)
        new_work_hours = float(derived["work_hours"]) if derived["work_hours"] is not None else None
        new_late_minutes = derived["late_minutes"]
        new_overtime_minutes = derived["overtime_minutes"]
        new_half_day = derived["half_day"]

    # Build changes diff comparing old and new values
    # Treat missing legacy fields
    old_status = record.get("status")
    old_pi = record.get("punch_in")
    if old_pi is not None and old_pi.tzinfo is None:
        old_pi = old_pi.replace(tzinfo=timezone.utc)
    old_po = record.get("punch_out")
    if old_po is not None and old_po.tzinfo is None:
        old_po = old_po.replace(tzinfo=timezone.utc)
    old_wh = record.get("work_hours")
    old_lm = record.get("late_minutes", 0)
    old_om = record.get("overtime_minutes", 0)
    old_hd = record.get("half_day", False)

    changes_bson = {}
    if old_status != target_status:
        changes_bson["status"] = {"from": old_status, "to": target_status}
    if old_pi != new_punch_in_dt:
        changes_bson["punch_in"] = {"from": old_pi, "to": new_punch_in_dt}
    if old_po != new_punch_out_dt:
        changes_bson["punch_out"] = {"from": old_po, "to": new_punch_out_dt}
    if old_wh != new_work_hours:
        changes_bson["work_hours"] = {"from": old_wh, "to": new_work_hours}
    if old_lm != new_late_minutes:
        changes_bson["late_minutes"] = {"from": old_lm, "to": new_late_minutes}
    if old_om != new_overtime_minutes:
        changes_bson["overtime_minutes"] = {"from": old_om, "to": new_overtime_minutes}
    if old_hd != new_half_day:
        changes_bson["half_day"] = {"from": old_hd, "to": new_half_day}

    # If nothing changed -> 422
    if not changes_bson:
        raise HTTPException(422, "regularization produced no changes")

    history_entry_bson = {
        "at": now_utc,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes_bson
    }

    set_updates = {
        "status": target_status,
        "punch_in": new_punch_in_dt,
        "punch_out": new_punch_out_dt,
        "work_hours": new_work_hours,
        "late_minutes": new_late_minutes,
        "overtime_minutes": new_overtime_minutes,
        "half_day": new_half_day,
    }

    current_history = record.get("history")
    current_history_len = len(current_history) if current_history is not None else 0

    filter_query = {"_id": record["_id"]}
    if current_history_len == 0:
        filter_query["$or"] = [{"history": {"$size": 0}}, {"history": {"$exists": False}}]
    else:
        filter_query["history"] = {"$size": current_history_len}

    update_query = {
        "$set": set_updates,
        "$push": {"history": history_entry_bson}
    }

    res = db.attendance_logs.update_one(filter_query, update_query)
    if res.matched_count == 0:
        raise HTTPException(409, "concurrent modification conflict")

    updated_doc = db.attendance_logs.find_one({"_id": record["_id"]})
    return format_attendance_doc(updated_doc)


def build_employee_monthly_pipeline(emp_code: str, first_day_str: str, last_day_str: str) -> list:
    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": first_day_str, "$lte": last_day_str}
            }
        },
        {
            "$project": {
                "date": 1,
                "status": 1,
                "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
                "parsed_date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                        "onError": None,
                        "onNull": None
                    }
                },
                "half_day_bool": {"$ifNull": ["$half_day", False]}
            }
        },
        {
            "$project": {
                "status": 1,
                "late_minutes": 1,
                "overtime_minutes": 1,
                "is_weekday": {
                    "$cond": [
                        {"$and": [
                            {"$ne": ["$parsed_date", None]},
                            {"$lte": [{"$isoDayOfWeek": "$parsed_date"}, 5]}
                        ]},
                        True,
                        False
                    ]
                },
                "is_present": {
                    "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
                },
                "half_day_bool": 1
            }
        },
        {
            "$project": {
                "status": 1,
                "late_minutes": 1,
                "overtime_minutes": 1,
                "present_contrib": {
                    "$cond": [
                        {"$and": ["$is_weekday", "$is_present"]},
                        {"$cond": ["$half_day_bool", 0.5, 1.0]},
                        0.0
                    ]
                },
                "is_leave": {
                    "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                },
                "is_late": {
                    "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {"$sum": "$present_contrib"},
                "leave_days": {"$sum": "$is_leave"},
                "late_count": {"$sum": "$is_late"},
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"}
            }
        }
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")

    try:
        year_str, month_str = month.split("-")
        y = int(year_str)
        m = int(month_str)
        _, last_day = calendar.monthrange(y, m)
    except Exception:
        raise HTTPException(422, "invalid month format")

    first_day_str = f"{month}-01"
    last_day_str = f"{month}-{last_day:02d}"

    pipeline = build_employee_monthly_pipeline(emp_code, first_day_str, last_day_str)
    agg_results = list(db.attendance_logs.aggregate(pipeline))

    if agg_results:
        raw_res = agg_results[0]
        present_days = float(half_up(raw_res.get("present_days", 0.0), 2))
        leave_days = int(raw_res.get("leave_days", 0))
        late_count = int(raw_res.get("late_count", 0))
        total_late_minutes = int(raw_res.get("total_late_minutes", 0))
        total_overtime_minutes = int(raw_res.get("total_overtime_minutes", 0))
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    joined_on = emp["joined_on"]
    if joined_on > last_day_str:
        working_days = 0
        attendance_pct = None
    else:
        effective_start = max(first_day_str, joined_on)
        working_days = count_working_days(effective_start, last_day_str)
        if working_days == 0:
            attendance_pct = None
        else:
            pct_val = (present_days / working_days) * 100.0
            attendance_pct = float(half_up(pct_val, 2))

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct
    }


def build_department_summary_pipeline(first_day_str: str, last_day_str: str, department: Optional[str] = None) -> list:
    match_emp = {
        "joined_on": {"$lte": last_day_str}
    }
    if department:
        match_emp["department"] = department

    return [
        {"$match": match_emp},
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "pipeline": [
                    {
                        "$match": {
                            "date": {"$gte": first_day_str, "$lte": last_day_str}
                        }
                    },
                    {
                        "$project": {
                            "date": 1,
                            "status": 1,
                            "work_hours": 1,
                            "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                            "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
                            "half_day_bool": {"$ifNull": ["$half_day", False]},
                            "parsed_date": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d",
                                    "onError": None,
                                    "onNull": None
                                }
                            }
                        }
                    },
                    {
                        "$project": {
                            "status": 1,
                            "late_minutes": 1,
                            "overtime_minutes": 1,
                            "is_weekday": {
                                "$cond": [
                                    {"$and": [
                                        {"$ne": ["$parsed_date", None]},
                                        {"$lte": [{"$isoDayOfWeek": "$parsed_date"}, 5]}
                                    ]},
                                    True,
                                    False
                                ]
                            },
                            "is_present": {
                                "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
                            },
                            "half_day_bool": 1,
                            "work_hours": 1
                        }
                    },
                    {
                        "$project": {
                            "status": 1,
                            "late_minutes": 1,
                            "overtime_minutes": 1,
                            "present_contrib": {
                                "$cond": [
                                    {"$and": ["$is_weekday", "$is_present"]},
                                    {"$cond": ["$half_day_bool", 0.5, 1.0]},
                                    0.0
                                ]
                            },
                            "is_leave": {
                                "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                            },
                            "is_on_duty": {
                                "$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]
                            },
                            "is_late": {
                                "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                            },
                            "has_wh": {
                                "$cond": [
                                    {"$and": ["$is_present", {"$ne": ["$work_hours", None]}]},
                                    1,
                                    0
                                ]
                            },
                            "wh_val": {
                                "$cond": [
                                    {"$and": ["$is_present", {"$ne": ["$work_hours", None]}]},
                                    "$work_hours",
                                    0.0
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$project": {
                "department": 1,
                "emp_present_days": {"$sum": "$logs.present_contrib"},
                "emp_late_count": {"$sum": "$logs.is_late"},
                "emp_late_minutes": {"$sum": "$logs.late_minutes"},
                "emp_leave_count": {"$sum": "$logs.is_leave"},
                "emp_on_duty_count": {"$sum": "$logs.is_on_duty"},
                "emp_wh_sum": {"$sum": "$logs.wh_val"},
                "emp_wh_count": {"$sum": "$logs.has_wh"}
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$emp_present_days"},
                "late_count": {"$sum": "$emp_late_count"},
                "total_late_minutes": {"$sum": "$emp_late_minutes"},
                "leave_count": {"$sum": "$emp_leave_count"},
                "on_duty_count": {"$sum": "$emp_on_duty_count"},
                "total_wh": {"$sum": "$emp_wh_sum"},
                "count_wh": {"$sum": "$emp_wh_count"}
            }
        },
        {"$sort": {"_id": 1}}
    ]


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None
):
    try:
        year_str, month_str = month.split("-")
        y = int(year_str)
        m = int(month_str)
        _, last_day = calendar.monthrange(y, m)
    except Exception:
        raise HTTPException(422, "invalid month format")

    first_day_str = f"{month}-01"
    last_day_str = f"{month}-{last_day:02d}"

    pipeline = build_department_summary_pipeline(first_day_str, last_day_str, department)
    dept_docs = list(db.employees.aggregate(pipeline))
    items = []
    for d in dept_docs:
        dept_name = d["_id"]
        headcount = d["headcount"]
        present_days = float(half_up(d["present_days"], 2))
        late_count = d["late_count"]
        total_late_minutes = d["total_late_minutes"]
        leave_count = d["leave_count"]
        on_duty_count = d["on_duty_count"]
        count_wh = d["count_wh"]
        total_wh = d["total_wh"]
        if count_wh > 0:
            avg_work_hours = float(half_up(total_wh / count_wh, 2))
        else:
            avg_work_hours = None

        items.append({
            "department": dept_name,
            "headcount": headcount,
            "present_days": present_days,
            "avg_work_hours": avg_work_hours,
            "late_count": late_count,
            "total_late_minutes": total_late_minutes,
            "leave_count": leave_count,
            "on_duty_count": on_duty_count
        })

    return {
        "month": month,
        "items": items
    }


def build_late_leaderboard_pipeline(
    first_day_str: str,
    last_day_str: str,
    limit: int = 10,
    department: Optional[str] = None
) -> list:
    pipeline = [
        {
            "$match": {
                "date": {"$gte": first_day_str, "$lte": last_day_str},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp"
            }
        },
        {"$unwind": "$emp"}
    ]

    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {
            "$match": {
                "rank": {"$lte": limit}
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": 1,
                "late_count": 1
            }
        }
    ])
    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None
):
    try:
        year_str, month_str = month.split("-")
        y = int(year_str)
        m = int(month_str)
        _, last_day = calendar.monthrange(y, m)
    except Exception:
        raise HTTPException(422, "invalid month format")

    first_day_str = f"{month}-01"
    last_day_str = f"{month}-{last_day:02d}"

    pipeline = build_late_leaderboard_pipeline(first_day_str, last_day_str, limit, department)
    items = list(db.attendance_logs.aggregate(pipeline))
    return {
        "month": month,
        "items": items
    }

def build_department_trend_pipeline(
    department: str,
    from_date_str: str,
    to_date_str: str
) -> list:
    return [
        {
            "$match": {
                "date": {"$gte": from_date_str, "$lte": to_date_str}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "pipeline": [
                    {"$match": {"department": department}}
                ],
                "as": "emp"
            }
        },
        {"$unwind": "$emp"},
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1.0]},
                            0.0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]
                    }
                }
            }
        },
        {"$sort": {"_id": 1}}
    ]


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date_str: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date_str: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$")
):
    try:
        from_d = date.fromisoformat(from_date_str)
    except ValueError:
        raise HTTPException(422, "invalid from date")

    try:
        to_d = date.fromisoformat(to_date_str)
    except ValueError:
        raise HTTPException(422, "invalid to date")

    if from_d > to_d:
        raise HTTPException(422, "from date cannot be after to date")

    days_span = (to_d - from_d).days + 1
    if days_span > 92:
        raise HTTPException(422, "date range exceeds maximum 92 days")

    # Check department existence
    dept_exists = db.employees.find_one({"department": department})
    if not dept_exists:
        raise HTTPException(404, "department not found")

    # 1. Main aggregation on attendance_logs (indexed via date_-1_emp_code_1)
    pipeline = build_department_trend_pipeline(department, from_date_str, to_date_str)
    log_groups = {row["_id"]: row for row in db.attendance_logs.aggregate(pipeline)}

    # 2. Dynamic daily headcount via DB aggregation on employees
    hc_pipeline = [
        {"$match": {"department": department, "joined_on": {"$lte": to_date_str}}},
        {"$project": {"_id": 0, "joined_on": 1}}
    ]
    dept_join_dates = [doc["joined_on"] for doc in db.employees.aggregate(hc_pipeline)]

    # 3. Assemble gap-free calendar days
    calendar_dates = [
        (from_d + timedelta(days=i)).isoformat()
        for i in range(days_span)
    ]

    rows = []
    for cur_date_str in calendar_dates:
        cur_d = date.fromisoformat(cur_date_str)
        is_wkday = cur_d.weekday() < 5
        hc = sum(1 for jd in dept_join_dates if jd <= cur_date_str)
        stats = log_groups.get(cur_date_str)
        pr_cnt = float(stats["present_count"]) if stats else 0.0
        lt_cnt = int(stats["late_count"]) if stats else 0

        if is_wkday and hc > 0:
            raw_rate = pr_cnt / hc
        else:
            raw_rate = None

        rows.append({
            "date": cur_date_str,
            "is_working_day": is_wkday,
            "headcount": hc,
            "present_count": pr_cnt,
            "late_count": lt_cnt,
            "attendance_rate": raw_rate
        })

    # Python precision rounding & window calculation:
    # 1. Round each attendance_rate with half_up(..., 4)
    # 2. moving_avg_7d is the mean of the already-rounded (4 decimals) non-null attendance_rate
    #    values among the current row and up to 6 preceding rows in the requested range,
    #    rounded to 4 decimals with half_up(..., 4). Null if no non-null rates in the window.
    items = []
    rounded_rates = []

    for idx, r in enumerate(rows):
        raw_rate = r["attendance_rate"]
        if raw_rate is not None:
            rate_val = float(half_up(raw_rate, 4))
        else:
            rate_val = None
        rounded_rates.append(rate_val)

        # 7-day window: [max(0, idx - 6) : idx + 1]
        window_start = max(0, idx - 6)
        window_rates = [val for val in rounded_rates[window_start : idx + 1] if val is not None]

        if window_rates:
            window_mean = sum(Decimal(str(v)) for v in window_rates) / Decimal(len(window_rates))
            moving_avg = float(half_up(window_mean, 4))
        else:
            moving_avg = None

        items.append({
            "date": r["date"],
            "is_working_day": r["is_working_day"],
            "headcount": r["headcount"],
            "present_count": float(half_up(r["present_count"], 2)),
            "late_count": r["late_count"],
            "attendance_rate": rate_val,
            "moving_avg_7d": moving_avg
        })

    return {
        "department": department,
        "items": items
    }

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: ExplainEndpointName,
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: Optional[int] = Query(10, ge=1, le=50),
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: Optional[Status] = None,
    from_date_str: Optional[str] = Query(None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date_str: Optional[str] = Query(None, alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    ep = endpoint.value

    if ep == "attendance_list":
        if date_from:
            try:
                date.fromisoformat(date_from)
            except ValueError:
                raise HTTPException(422, "invalid date_from")
        if date_to:
            try:
                date.fromisoformat(date_to)
            except ValueError:
                raise HTTPException(422, "invalid date_to")
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from cannot be after date_to")

        q = build_attendance_list_query(
            emp_code=emp_code,
            date_from=date_from,
            date_to=date_to,
            status=status.value if status else None
        )
        skip = (page - 1) * page_size
        find_cmd = {
            "find": "attendance_logs",
            "filter": q,
            "sort": {"date": -1, "emp_code": 1},
            "skip": skip,
            "limit": page_size
        }
        raw_explain = db.command("explain", find_cmd, verbosity="executionStats")
        clean_explain = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "attendance_logs",
            "explain": clean_explain
        }

    elif ep == "employee_monthly":
        if not emp_code:
            raise HTTPException(422, "emp_code is required for employee_monthly explain")
        if not month:
            raise HTTPException(422, "month is required for employee_monthly explain")
        try:
            year_str, month_str = month.split("-")
            y = int(year_str)
            m = int(month_str)
            _, last_day = calendar.monthrange(y, m)
        except Exception:
            raise HTTPException(422, "invalid month format")

        first_day_str = f"{month}-01"
        last_day_str = f"{month}-{last_day:02d}"

        pipeline = build_employee_monthly_pipeline(emp_code, first_day_str, last_day_str)
        raw_explain = db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {}
        }, verbosity="executionStats")
        clean_explain = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "attendance_logs",
            "explain": clean_explain
        }

    elif ep == "department_summary":
        if not month:
            raise HTTPException(422, "month is required for department_summary explain")
        try:
            year_str, month_str = month.split("-")
            y = int(year_str)
            m = int(month_str)
            _, last_day = calendar.monthrange(y, m)
        except Exception:
            raise HTTPException(422, "invalid month format")

        first_day_str = f"{month}-01"
        last_day_str = f"{month}-{last_day:02d}"

        pipeline = build_department_summary_pipeline(first_day_str, last_day_str, department)
        raw_explain = db.command("explain", {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {}
        }, verbosity="executionStats")
        clean_explain = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "employees",
            "explain": clean_explain
        }

    elif ep == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required for late_leaderboard explain")
        try:
            year_str, month_str = month.split("-")
            y = int(year_str)
            m = int(month_str)
            _, last_day = calendar.monthrange(y, m)
        except Exception:
            raise HTTPException(422, "invalid month format")

        first_day_str = f"{month}-01"
        last_day_str = f"{month}-{last_day:02d}"

        pipeline = build_late_leaderboard_pipeline(first_day_str, last_day_str, limit or 10, department)
        raw_explain = db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {}
        }, verbosity="executionStats")
        clean_explain = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "attendance_logs",
            "explain": clean_explain
        }

    elif ep == "department_trend":
        if not department:
            raise HTTPException(422, "department is required for department_trend explain")
        if not from_date_str:
            raise HTTPException(422, "from is required for department_trend explain")
        if not to_date_str:
            raise HTTPException(422, "to is required for department_trend explain")

        try:
            from_d = date.fromisoformat(from_date_str)
        except ValueError:
            raise HTTPException(422, "invalid from date")

        try:
            to_d = date.fromisoformat(to_date_str)
        except ValueError:
            raise HTTPException(422, "invalid to date")

        if from_d > to_d:
            raise HTTPException(422, "from date cannot be after to date")

        days_span = (to_d - from_d).days + 1
        if days_span > 92:
            raise HTTPException(422, "date range exceeds maximum 92 days")

        pipeline = build_department_trend_pipeline(department, from_date_str, to_date_str)
        raw_explain = db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {}
        }, verbosity="executionStats")
        clean_explain = json.loads(json_util.dumps(raw_explain))
        return {
            "endpoint": ep,
            "collection": "attendance_logs",
            "explain": clean_explain
        }

# --------------------------------------------------------------------------- #


