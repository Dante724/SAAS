# ADR: timestamps stay naive UTC; "today" is the shop's day

Date: 31 Oct 2026. Context: the 29 Oct 2026 full audit (timestamps item): 211+ `datetime.utcnow()`
calls, no timezone-aware columns, and "today" computed with the server's `date.today()`.

## Decision

1. **Storage stays naive UTC** (`DateTime` without a zone) on SQLite and Postgres. We do **not** move
   to `timestamptz` now.
2. **One clock:** `backend.services.dates.now_utc()` is the only source of "now" for stored
   timestamps. It reads an aware clock (`datetime.now(timezone.utc)`) and drops the zone, so values
   compare with and store into the existing columns unchanged. `datetime.utcnow()` (deprecated in
   3.12) is gone from the code. Tests freeze time by patching `dates._clock`.
3. **"Today" is the shop's day.** Any decision about what day it is goes through `services/dates.py`
   with the org's `timezone`: `today_in(tz)`, `org_today(org)`, `await shop_today(db, org_id)`, and
   `day_start_utc(day, tz)` / `day_end_utc(day, tz)` to turn a shop day into a naive-UTC range for
   queries. `date.today()` is not used in request code.
4. **Platform-owned dates are UTC days** (`platform_today()`): subscription start/end, platform
   coupons and the staff access windows set from the admin portal. `Organisation.timezone` is set by
   the tenant, so evaluating these in it would let a shop stretch its own subscription by up to
   ~12h (security review, 31 Oct 2026).

## Why not timestamptz now

- **SQLite can't hold it.** SQLAlchemy's `DateTime(timezone=True)` on SQLite stores text and returns
  naive values, so the dev/test database would behave differently from Postgres: the same comparison
  would be naive-vs-naive in tests and aware-vs-aware in production. Most of the suite runs on SQLite.
- **Every comparison flips at once.** Aware values from the database can't be compared with naive
  values in Python (`TypeError`). ~290 call sites, plus every `datetime.fromisoformat(...)` from
  request input and every naive literal in tests, would have to change in the same deploy. A
  missed one is a 500 in production, not a failed test, wherever coverage is thin.
- **asyncpg rejects aware values for `timestamp` columns and naive for `timestamptz`**, so a
  half-migrated schema breaks at write time.
- **Scheduled times are deliberately wall-clock** (`Order.scheduled_for`, `Appointment.scheduled_for`
  are shop-local; see `dates.to_wall_clock`). They must stay naive whatever happens to the rest.

## How to do it later (if wanted)

1. Postgres only: `ALTER TABLE … ALTER COLUMN … TYPE timestamptz USING col AT TIME ZONE 'UTC'` per
   column (keep the scheduled wall-clock columns as `timestamp`).
2. Switch `now_utc()` to return aware, add a SQLAlchemy `TypeDecorator` that attaches UTC to values
   read from SQLite, and make `parse_dt` always return aware UTC for instants.
3. Run the whole suite on Postgres and SQLite; grep for naive literals in tests.

## Addendum, 11 Nov 2026: task due times are UTC instants (migration tk02)

`tasks.due_at` is a naive **UTC instant**, like `created_at`, not a wall-clock scheduled time.
The backend always read it that way (overdue flag, on-time commission, automations), but the web
Tasks screen and Tekly Go used to send the shop's wall time with no offset, which was stored as if
it were UTC (in an IST shop, a task due 18:00 read as 23:30).

- **Input** (`routers/tasks._parse_due`): a date alone (`YYYY-MM-DD`) is the end of that shop
  day, 23:59:59 local (`dates.range_edge(..., end=True)`, the reports' rule). A datetime with an
  offset is that instant. A datetime **without** an offset is shop wall time, converted with that
  day's offset, so older Go builds and other callers that send `datetime-local` values keep working.
- **Existing rows:** migration tk02 converted every `source = 'manual'` due time from the shop's
  wall time to UTC. A bare-date value stored at 00:00 became the end of that day. Automation,
  inquiry and missed-call tasks were already UTC and were not touched.
- **"Shop clock"** for tasks is `dates.shop_tzname(org)`, the org's zone, as the reports use. My day,
  date-only due dates, move-to-today, alert texts and the overdue sweep's working-hours check all
  read it. The sweep uses the branch's `working_hours` (opening times), but reads them on this one
  clock, so a branch whose `Location.timezone` differs from the org's still runs on the org's day.
- **Output** stays naive UTC ISO (unchanged shape). Clients convert to the shop zone to show it.
