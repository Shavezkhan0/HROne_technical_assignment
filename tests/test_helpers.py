import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from app.main import (
    IST, to_epoch_ms, from_epoch_ms, floor_to_second, half_up,
    ist_date_for_punch_in, shift_bounds_for_date, late_minutes,
    overtime_minutes, work_hours, is_half_day, count_working_days,
    compute_derived
)

def test_late_minutes():
    # shift 09:30: 09:40:00 -> 0, 09:40:01 -> 10, 10:15:59 -> 45, 09:28 -> 0
    shift_start = datetime(2026, 7, 6, 9, 30, tzinfo=IST)
    assert late_minutes(datetime(2026, 7, 6, 9, 40, 0, tzinfo=IST), shift_start) == 0
    assert late_minutes(datetime(2026, 7, 6, 9, 40, 1, tzinfo=IST), shift_start) == 10
    assert late_minutes(datetime(2026, 7, 6, 10, 15, 59, tzinfo=IST), shift_start) == 45
    assert late_minutes(datetime(2026, 7, 6, 9, 28, 0, tzinfo=IST), shift_start) == 0

def test_truncation():
    # sub-second truncation: 09:40:00.900 is treated as 09:40:00
    dt = datetime(2026, 7, 6, 9, 40, 0, 900000, tzinfo=IST)
    truncated = floor_to_second(dt)
    assert truncated.microsecond == 0

def test_overtime_minutes():
    # overtime: 29 min -> 0, 30 min -> 30, punch-out before shift_end -> 0
    shift_end = datetime(2026, 7, 6, 18, 30, tzinfo=IST)
    assert overtime_minutes(datetime(2026, 7, 6, 18, 59, 59, tzinfo=IST), shift_end) == 0
    assert overtime_minutes(datetime(2026, 7, 6, 19, 0, 0, tzinfo=IST), shift_end) == 30
    assert overtime_minutes(datetime(2026, 7, 6, 18, 28, 0, tzinfo=IST), shift_end) == 0

def test_overnight_shift():
    # overnight shift 22:00-06:00: punch-in 05:59 IST belongs to previous date, 06:00 belongs to the same day; 
    # overtime uses shift_end on the next day
    shift_start, shift_end = "22:00", "06:00"
    
    # punch-in 05:59 IST on Jul 7
    pi_559 = datetime(2026, 7, 7, 5, 59, tzinfo=IST)
    assert ist_date_for_punch_in(pi_559, shift_start, shift_end) == "2026-07-06"
    
    # punch-in 06:00 IST on Jul 7
    pi_600 = datetime(2026, 7, 7, 6, 0, tzinfo=IST)
    assert ist_date_for_punch_in(pi_600, shift_start, shift_end) == "2026-07-07"
    
    # shift_bounds for "2026-07-06" should return end time on Jul 7 06:00
    st, et = shift_bounds_for_date("2026-07-06", shift_start, shift_end)
    assert st == datetime(2026, 7, 6, 22, 0, tzinfo=IST)
    assert et == datetime(2026, 7, 7, 6, 0, tzinfo=IST)

def test_work_hours():
    # work_hours exact half-up boundary, and 4.50 is NOT a half day, 4.49 is
    assert half_up(4.495, 2) == Decimal("4.50")
    assert half_up(4.494, 2) == Decimal("4.49")
    
    assert not is_half_day(Decimal("4.50"))
    assert is_half_day(Decimal("4.49"))

def test_working_days():
    # July 2026 = 23, from 2026-07-13 = 15, joined on a Saturday starts counting on the next Monday
    assert count_working_days("2026-07-01", "2026-07-31") == 23
    assert count_working_days("2026-07-13", "2026-07-31") == 15
    # 2026-07-04 is a Saturday. 
    assert count_working_days("2026-07-04", "2026-07-10") == 5 # Mon-Fri

def test_sample_data():
    # sample data: EMP0001 Jul 6 -> work_hours 9.12; 
    employee1 = {"shift_start": "09:30", "shift_end": "18:30"}
    in_1 = datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc)
    out_1 = datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc)
    derived_1 = compute_derived(in_1, out_1, employee1, "2026-07-06")
    assert derived_1["work_hours"] == 9.12
    assert not derived_1["half_day"]

    # EMP0003 -> late_minutes 35, overtime 40; 
    employee3 = {"shift_start": "09:30", "shift_end": "18:30"}
    in_3 = datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc)  # 10:05 IST
    out_3 = datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc) # 19:10 IST
    derived_3 = compute_derived(in_3, out_3, employee3, "2026-07-06")
    assert derived_3["late_minutes"] == 35
    assert derived_3["overtime_minutes"] == 40
    
    # EMP0004 -> work_hours 3.50 and half_day true
    employee4 = {"shift_start": "09:30", "shift_end": "18:30"}
    in_4 = datetime(2026, 7, 7, 4, 0, tzinfo=timezone.utc)   # 09:30 IST
    out_4 = datetime(2026, 7, 7, 7, 30, tzinfo=timezone.utc) # 13:00 IST
    derived_4 = compute_derived(in_4, out_4, employee4, "2026-07-07")
    assert derived_4["work_hours"] == 3.50
    assert derived_4["half_day"]
