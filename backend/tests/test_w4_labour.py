"""Wave 4, group D (Labour): follow-ups from #209 / #215 / #227.

- Re-pricing a corrected attendance shift: a shift paid by a window row from before per-shift
  wages (#209, no accrual key) was never re-priced when a manager corrected it, and a correction
  that moved its end past the window's posting time made the next posting pay it a second time.
  The rate of a shift paid per shift is the shop's rate when that explains the posted amount,
  not the rounded amount divided by the hours (which drifted by cents).
- _sync_day_wages loaded each day's entries and wage groups with their own queries; a timesheet
  approval or a reopen over many days now loads the window once (same ledger, fewer queries).
- The N-10 clip (stop a timer where the person's next entry begins) is a public helper, so the
  job-close timer stop in orders.py can use it.
"""
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select

pytestmark = pytest.mark.asyncio


async def _register(client):
    email = f"w4d-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    H = {"Authorization": f"Bearer {body['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, body["org_id"], me["id"]


async def _teammate(client, H, db, org):
    from backend.models import Role
    role = (await db.execute(select(Role.id).where(Role.org_id == org, Role.name == "admin"))).scalar_one()
    r = await client.post("/api/staff/employees", json={
        "name": "Tech", "email": f"tm-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345",
        "role_id": role}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _attendance_emp(db, emp_id, rate):
    from backend.models import Employee
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id))).scalar_one()
    emp.hourly_basis, emp.hourly_cost_rate = "attendance", rate
    await db.commit()


async def _shift(db, emp_id, start, hours):
    from backend.models import Employee, Location, Shift
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id))).scalar_one()
    loc = emp.location_id or (await db.execute(select(Location.id).where(
        Location.org_id == emp.org_id))).scalars().first()
    s = Shift(employee_id=emp_id, location_id=loc, started_at=start,
              ended_at=start + timedelta(hours=hours), hours_worked=round(hours, 2))
    db.add(s)
    await db.commit()
    return s.id


async def _legacy_window_row(db, org, emp, d0, d1, amount, hours):
    """A row as post_attendance_wages wrote it before #209: one per window, no accrual key, dated
    noon on the window's last day."""
    from backend.models import EmployeeBalanceLedger as L
    end = datetime.fromisoformat(d1)
    db.add(L(org_id=org, employee_id=emp, entry_type="hourly_wage", amount=Decimal(str(amount)),
             ref_type="attendance", ref_id=f"{emp}:{d0}:{d1}", idempotency_key=f"attendance:{emp}:{d0}:{d1}",
             created_at=datetime(end.year, end.month, end.day, 12, 0, 0),
             note=f"Shop attendance wages {d0}–{d1} ({hours}h)"))
    await db.commit()


async def _wages(db, org, emp):
    from backend.models import EmployeeBalanceLedger as L
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(L.amount), 0)).where(
        L.org_id == org, L.employee_id == emp, L.entry_type == "hourly_wage"))).scalar())


# ── 1. re-pricing corrected attendance shifts ───────────────────────────────────────────────────
async def test_correcting_a_shift_paid_by_a_legacy_window_reprices_it(client, db):
    H, org, _me = await _register(client)
    tech = await _teammate(client, H, db, org)
    await _attendance_emp(db, tech, 20)
    sid = await _shift(db, tech, datetime(2026, 10, 2, 14), 8)
    await _legacy_window_row(db, org, tech, "2026-10-01", "2026-10-31", 160, 8.0)

    r = await client.patch(f"/api/timeclock/shifts/{sid}", headers=H, json={
        "ended_at": "2026-10-02T20:00:00Z", "reason": "left early"})
    assert r.status_code == 200, r.text
    assert r.json()["wage_change"] == -40.0          # was 0: the legacy-paid shift stayed at 8h
    assert await _wages(db, org, tech) == 120.0

    # the month posted again pays nothing more; correcting back pays the difference once
    r = await client.post("/api/payroll/attendance-wages", headers=H, json={
        "employee_id": tech, "date_from": "2026-10-01", "date_to": "2026-10-31"})
    assert r.status_code == 201 and r.json()["wage_posted"] == 0, r.text
    r = await client.patch(f"/api/timeclock/shifts/{sid}", headers=H, json={
        "ended_at": "2026-10-02T22:00:00Z", "reason": "was right"})
    assert r.json()["wage_change"] == 40.0
    assert await _wages(db, org, tech) == 160.0


async def test_moving_a_legacy_paid_shift_past_the_window_posting_does_not_pay_it_again(client, db):
    H, org, _me = await _register(client)
    tech = await _teammate(client, H, db, org)
    await _attendance_emp(db, tech, 20)
    sid = await _shift(db, tech, datetime(2026, 10, 31, 2), 8)     # ended 10:00, window posted at noon
    await _legacy_window_row(db, org, tech, "2026-10-01", "2026-10-31", 160, 8.0)
    # the real shift was 10:00-20:00: it now ends after the window was posted
    r = await client.patch(f"/api/timeclock/shifts/{sid}", headers=H, json={
        "started_at": "2026-10-31T10:00:00Z", "ended_at": "2026-10-31T20:00:00Z", "reason": "wrong times"})
    assert r.status_code == 200, r.text
    assert r.json()["wage_change"] == 40.0
    r = await client.post("/api/payroll/attendance-wages", headers=H, json={
        "employee_id": tech, "date_from": "2026-10-01", "date_to": "2026-10-31"})
    assert r.json()["wage_posted"] == 0, r.text        # was 200: paid a second time in full
    assert await _wages(db, org, tech) == 200.0


async def test_same_hours_moved_still_marks_a_legacy_paid_shift_paid(client, db):
    H, org, _me = await _register(client)
    tech = await _teammate(client, H, db, org)
    await _attendance_emp(db, tech, 20)
    sid = await _shift(db, tech, datetime(2026, 10, 31, 2), 8)
    await _legacy_window_row(db, org, tech, "2026-10-01", "2026-10-31", 160, 8.0)
    r = await client.patch(f"/api/timeclock/shifts/{sid}", headers=H, json={
        "started_at": "2026-10-31T12:00:00Z", "ended_at": "2026-10-31T20:00:00Z", "reason": "shifted"})
    assert r.status_code == 200 and r.json()["wage_change"] == 0, r.text
    r = await client.post("/api/payroll/attendance-wages", headers=H, json={
        "employee_id": tech, "date_from": "2026-10-01", "date_to": "2026-10-31"})
    assert r.json()["wage_posted"] == 0, r.text
    assert await _wages(db, org, tech) == 160.0


async def test_correcting_a_shift_uses_the_rate_it_was_paid_at_not_a_rounded_one(client, db):
    H, org, _me = await _register(client)
    tech = await _teammate(client, H, db, org)
    await _attendance_emp(db, tech, Decimal("15.55"))
    sid = await _shift(db, tech, datetime(2026, 10, 2, 14), 0.33)   # 0.33h x 15.55 = 5.1315 -> 5.13
    r = await client.post("/api/payroll/attendance-wages", headers=H, json={
        "employee_id": tech, "date_from": "2026-10-01", "date_to": "2026-10-31"})
    assert r.json()["wage_posted"] == 5.13, r.text
    r = await client.patch(f"/api/timeclock/shifts/{sid}", headers=H, json={
        "ended_at": "2026-10-02T22:00:00Z", "reason": "forgot to clock out"})
    assert r.status_code == 200, r.text
    assert await _wages(db, org, tech) == 124.40       # 8h x 15.55; was 124.36 (5.13 / 0.33 x 8)


# ── 2. _sync_day_wages loads the window once ───────────────────────────────────────────────────
async def _labour_days(db, org, emp, days, start=datetime(2026, 10, 5, 9)):
    from backend.models import TimeEntry
    ids = []
    for i in range(days):
        for h0, hours in ((0, 6), (7, 3.5)):          # 9.5h a day: 1.5h overtime on the second entry
            s = start + timedelta(days=i, hours=h0)
            e = TimeEntry(org_id=org, employee_id=emp, started_at=s, ended_at=s + timedelta(hours=hours),
                          duration_sec=int(hours * 3600), cost_rate=Decimal("20"), state="approved",
                          source="manual")
            db.add(e)
            await db.flush()
            ids.append(e.id)
    await db.commit()
    return ids


class _Sql:
    def __init__(self, engine):
        self.engine, self.n = engine, 0

    def _hit(self, *a):
        self.n += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)


async def test_sync_wages_over_a_window_matches_day_by_day_with_fewer_queries(client, db):
    from backend.models import EmployeeBalanceLedger as L
    from backend.routers import timeclock
    H, org, _me = await _register(client)
    a = await _teammate(client, H, db, org)
    b = await _teammate(client, H, db, org)
    days = 10
    ids_a = await _labour_days(db, org, a, days)
    ids_b = await _labour_days(db, org, b, days)
    tz = await timeclock._emp_tz(db, org, a)
    window = [(datetime(2026, 10, 5) + timedelta(days=i)).date() for i in range(days)]
    engine = db.bind.sync_engine if hasattr(db.bind, "sync_engine") else db.bind

    with _Sql(engine) as per_day:
        for d in window:
            await timeclock._sync_day_wages(db, org, a, d, tz, fresh=frozenset(ids_a))
    await db.commit()
    with _Sql(engine) as batched:
        amount, rows = await timeclock._sync_wages(db, org, b, window, tz, fresh=frozenset(ids_b))
    await db.commit()

    def shape(emp, ids):
        return [(ids.index(e), float(x), r.split(":", 1)[1]) for e, x, r in rows_of[emp]]
    rows_of = {}
    for emp in (a, b):
        rows_of[emp] = [(r.accrual_key.split(":", 1)[1], r.amount, r.ref_id) for r in (await db.execute(
            select(L).where(L.org_id == org, L.employee_id == emp)
            .order_by(L.ref_id))).scalars().all()]
    assert len(rows_of[b]) == rows == 2 * days
    assert float(amount) == days * (6 * 20 + 2 * 20 + 1.5 * 30)     # 205 a day
    assert sorted(shape(a, ids_a)) == sorted(shape(b, ids_b))
    # the window's entries, wage groups and payroll runs are read once, not once per day
    assert batched.n <= per_day.n - 2 * (days - 1), (batched.n, per_day.n)
    assert batched.n <= 4 + 2 * rows, batched.n      # 3 reads for the window, then a check + insert per row

    # syncing again posts nothing and stays cheap
    with _Sql(engine) as again:
        assert await timeclock._sync_wages(db, org, b, window, tz) == (Decimal("0"), 0)
    assert again.n <= 3, again.n


async def test_timesheet_approval_over_many_days_pays_each_shop_day(client, db):
    from backend.models import TimeEntry
    H, org, _me = await _register(client)
    tech = await _teammate(client, H, db, org)
    ids = await _labour_days(db, org, tech, 5)
    await db.execute(TimeEntry.__table__.update().where(TimeEntry.id.in_(ids)).values(state="stopped"))
    await db.commit()
    r = await client.post("/api/timeclock/timesheet/approve", headers=H, json={
        "employee_id": tech, "date_from": "2026-10-01", "date_to": "2026-10-31"})
    assert r.status_code == 200, r.text
    assert r.json()["wage_posted"] == 5 * 205.0
    assert await _wages(db, org, tech) == 5 * 205.0
    # reopen the first entry of day 3: that day's overtime moves onto nothing (3.5h straight)
    r = await client.post(f"/api/timeclock/entries/{ids[4]}/reopen", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["wage_reversed"] == 120.0
    assert await _wages(db, org, tech) == 4 * 205.0 + 70.0


# ── 4. the N-10 clip is a public helper ─────────────────────────────────────────────────────────
async def test_stop_clipped_is_public_and_clips_at_the_next_entry(client, db):
    from backend.models import TimeEntry
    from backend.routers.timeclock import stop_clipped
    from backend.services.dates import now_utc
    H, org, _me = await _register(client)
    tech = await _teammate(client, H, db, org)
    now = now_utc()
    run = TimeEntry(org_id=org, employee_id=tech, started_at=now - timedelta(hours=3), state="running",
                    source="timer", cost_rate=Decimal("20"))
    later = TimeEntry(org_id=org, employee_id=tech, started_at=now - timedelta(hours=1),
                      ended_at=now - timedelta(minutes=30), duration_sec=1800, state="stopped",
                      source="manual", cost_rate=Decimal("20"))
    db.add_all([run, later])
    await db.commit()
    await stop_clipped(db, run)                        # takes the employee lock itself
    await db.commit()
    assert run.state == "stopped" and run.needs_review is True
    assert run.ended_at == later.started_at and run.duration_sec == 2 * 3600
