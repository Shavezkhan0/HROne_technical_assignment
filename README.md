# Employee Attendance & Analytics API

FastAPI service for employee records, attendance punch-in/out tracking, attendance regularization with audit trail, and department analytics backed by MongoDB.

## How to Run

1. **Virtual environment and dependencies:**
   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .\.venv\Scripts\Activate.ps1
   # Linux/macOS:
   source .venv/bin/activate

   pip install -r requirements.txt
   ```

2. **Environment configuration:**
   Create a `.env` file in the repository root (not committed to git) specifying your MongoDB credentials:
   ```env
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=attendance_db
   ```

3. **Start the server:**
   ```bash
   uvicorn app.main:app --port 8000
   ```
   Interactive OpenAPI documentation is available at `/docs` (http://localhost:8000/docs).

## How to Run Tests

Execute the test suite with pytest:
```bash
pytest -v
```
All tests run against the isolated test database `attendance_db_test`, configured in `tests/conftest.py` so development data is untouched.

## Implementation Status

All endpoints required by the specification are fully implemented:
- `GET /health` — Service health and database ping check
- `POST /employees` — Create employee
- `GET /employees` — List employees with pagination and department filter
- `POST /attendance/punch-in` — Record punch-in with shift-aware date and late calculation
- `POST /attendance/punch-out` — Record punch-out with work hours, overtime, and half-day calculation
- `GET /attendance` — List attendance logs with date range, status filters, and pagination
- `PATCH /attendance/{emp_code}/{date}` — Regularize attendance with audit trail
- `GET /analytics/employees/{emp_code}/monthly` — Monthly summary for an employee
- `GET /analytics/departments/summary` — Monthly department attendance summary
- `GET /analytics/leaderboard/late` — Monthly late arrival leaderboard with competition ranking
- `GET /analytics/departments/{department}/trend` — Department daily attendance and 7-day rolling average
- `GET /admin/explain/{endpoint}` — MongoDB execution plans for analytics and list queries
