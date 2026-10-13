# DECISIONS.md

**1. Indexes.** employees: unique `emp_code` (duplicates, joins) and `(department, joined_on)` (headcount). attendance_logs: unique `(emp_code, date)` (one log per day, employee queries), `(date desc, emp_code)` (list sort, date ranges, trend), `(date, late_minutes)` (leaderboard), and `(status, date desc, emp_code)` (status filters; documents examined dropped from 3,392 to 20). Rejected: a standalone `joined_on` index, because no query used it.

**2. Punch-in race.** There is no check first. Both requests call `insert_one`. The unique `(emp_code, date)` index lets one succeed (201), and the other raises `DuplicateKeyError`, which I return as 409.

**3. Ties.** `$rank` gives 1, 2, 2, 4. The limit is applied after ranking (`rank <= limit`), so everyone tied at the cutoff is returned, ordered by `emp_code`.

**4. Headcount.** The pipeline starts from `employees` (`joined_on` on or before month end), then uses `$lookup` into the logs, so employees with no logs still count.

**5. At 100x.** Trend and leaderboard scan every department's logs in the date range and filter by department afterwards (33,850 documents in my load test). I would store `department` on each log and index `(department, date)`.
