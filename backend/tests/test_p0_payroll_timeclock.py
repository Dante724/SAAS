"""P0 audit fixes (2026-11-12): wage posting, the time clock and billing tracked time.

- LAB-01: reopening an approved entry left its wage posted; re-approval paid it again or dropped
  the correction.
- LAB-02: attendance wages for overlapping windows paid the same shift twice.
- LAB-03: an entry approved on its own was never paid.
- LAB-04: staff could lengthen or overlap their own entries; edit and the v1 API had no overlap check.
- RPT-01 / LAB-06 / RPT-04: timesheet days, daily overtime and the attendance window were UTC days.
- WO-02: billing tracked time had no gate, took the rate from the request, worked on closed jobs
  and billed the same hours again on every call.

Wages are posted per source (time entry / attendance shift) into the append-only ledger. A reopen
posts a reversing row; nothing is ever deleted.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


async def _register(client):
    email = f"p0-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    H = {"Authorization": f"Bearer {body['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, body["org_id"], me["id"]


async def _role(db, org_id, perms=None):
    from backend.models import Role
    if perms is None:       # the shop's full-access admin role
        return (await db.execute(select(Role.id).where(Role.org_id == org_id, Role.name == "admin"))).scalar_one()
    role = Role(org_id=org_id, name=f"r-{uuid.uuid4().hex[:4]}", permissions=perms)
    db.add(role)
    await db.commit()
    return role.id


async def _teammate(client, H, role_id, **extra):
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tech", "email": email, "password": "pw-test-12345", "role_id": role_id, **extra}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return r.json()["id"], {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _wages(db, org, emp):
    from backend.models import EmployeeBalanceLedger as L
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(L.amount), 0)).where(
        L.org_id == org, L.employee_id == emp, L.entry_type == "hourly_wage"))).scalar())


async def _wage_rows(db, org, emp):
    from backend.models import EmployeeBalanceLedger as L
    db.expire_all()
    return (await db.execute(select(L).where(L.org_id == org, L.employee_id == emp,
                                             L.entry_type == "hourly_wage"))).scalars().all()


async def _entry(db, org, emp, when, hours, rate=20.0, state="stopped", **kw):
    from backend.models import TimeEntry
    start = datetime.fromisoformat(when)
    e = TimeEntry(org_id=org, employee_id=emp, started_at=start, ended_at=start + timedelta(hours=hours),
                  duration_sec=int(hours * 3600), cost_rate=rate, state=state, source="manual", **kw)
    db.add(e)
    await db.commit()
    return e.id


async def _approve_window(client, H, emp, d0, d1):
    return await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": d0, "date_to": d1}, headers=H)


async def _set_tz(client, H, tz):
    r = await client.put("/api/staff/company", json={"timezone": tz}, headers=H)
    assert r.status_code == 200, r.text


# ── LAB-01 ──────────────────────────────────────────────────────────────────────────────────────
async def test_lab01_reopen_fix_reapprove_pays_the_corrected_wage_once(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-03T09:00:00", 8)
    assert (await _approve_window(client, H, emp, "2026-08-03", "2026-08-03")).status_code == 200
    assert await _wages(db, org, emp) == 160
    rows_before = len(await _wage_rows(db, org, emp))

    r = await client.post(f"/api/timeclock/entries/{eid}/reopen", headers=H)
    assert r.status_code == 200, r.text
    # the reopen reverses the wage with a new ledger row; the original row is kept
    assert await _wages(db, org, emp) == 0
    rows = await _wage_rows(db, org, emp)
    assert len(rows) == rows_before + 1 and any(float(x.amount) == -160 for x in rows)

    r = await client.patch(f"/api/timeclock/entries/{eid}", json={"ended_at": "2026-08-03T18:00:00"}, headers=H)
    assert r.status_code == 200, r.text
    r = await _approve_window(client, H, emp, "2026-08-01", "2026-08-31")
    assert r.status_code == 200, r.text
    assert await _wages(db, org, emp) == 190          # 8h + 1h overtime at 1.5x: was 160 (correction dropped)
    assert r.json()["wage_posted"] == 190 and r.json()["ledger_posted"] is True

    # approving the same window again posts nothing more
    r = await _approve_window(client, H, emp, "2026-08-01", "2026-08-31")
    assert r.status_code == 404
    assert await _wages(db, org, emp) == 190


async def test_lab01_reopen_one_of_two_entries_does_not_pay_it_again(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    a = await _entry(db, org, emp, "2026-08-06T08:00:00", 3)
    await _entry(db, org, emp, "2026-08-06T12:00:00", 3)
    assert (await _approve_window(client, H, emp, "2026-08-06", "2026-08-06")).status_code == 200
    assert await _wages(db, org, emp) == 120
    assert (await client.post(f"/api/timeclock/entries/{a}/reopen", headers=H)).status_code == 200
    assert await _wages(db, org, emp) == 60
    assert (await _approve_window(client, H, emp, "2026-08-06", "2026-08-06")).status_code == 200
    assert await _wages(db, org, emp) == 120          # was 180


async def test_lab01_reopen_moves_overtime_between_entries_of_the_day(client, db):
    """Reopening the first entry of a 10h day: the later entry no longer runs into overtime, so
    its wage is corrected too, and the day always holds exactly what its approved entries earn."""
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    a = await _entry(db, org, emp, "2026-08-07T06:00:00", 6)
    await _entry(db, org, emp, "2026-08-07T13:00:00", 4)
    assert (await _approve_window(client, H, emp, "2026-08-07", "2026-08-07")).status_code == 200
    assert await _wages(db, org, emp) == 6 * 20 + 2 * 20 + 2 * 30            # 220
    assert (await client.post(f"/api/timeclock/entries/{a}/reopen", headers=H)).status_code == 200
    assert await _wages(db, org, emp) == 4 * 20
    assert (await client.post(f"/api/timeclock/entries/{a}/approve", headers=H)).status_code == 200
    assert await _wages(db, org, emp) == 220


async def test_lab01_reopen_of_a_day_paid_before_per_entry_wages(client, db):
    """Entries approved before this change were paid by one row per employee and UTC day. Reopening
    one takes its share off that row, so re-approving pays the corrected wage once."""
    from backend.models import EmployeeBalanceLedger as L
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-03T09:00:00", 8, state="approved")
    db.add(L(org_id=org, employee_id=emp, entry_type="hourly_wage", amount=160, ref_type="timesheet",
             ref_id=f"{emp}:2026-08-03:2026-08-03", idempotency_key=f"hourly:{emp}:2026-08-03:abc"))
    await db.commit()
    assert (await client.post(f"/api/timeclock/entries/{eid}/reopen", headers=H)).status_code == 200
    assert await _wages(db, org, emp) == 0
    r = await client.patch(f"/api/timeclock/entries/{eid}", json={"ended_at": "2026-08-03T18:00:00"}, headers=H)
    assert r.status_code == 200, r.text
    assert (await _approve_window(client, H, emp, "2026-08-03", "2026-08-03")).status_code == 200
    assert await _wages(db, org, emp) == 190          # 8h + 1h overtime, once


async def test_lab01_reopened_wage_lands_in_the_payroll_run(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-03T09:00:00", 8)
    assert (await _approve_window(client, H, emp, "2026-08-03", "2026-08-03")).status_code == 200
    assert (await client.post(f"/api/timeclock/entries/{eid}/reopen", headers=H)).status_code == 200
    assert (await client.post(f"/api/timeclock/entries/{eid}/approve", headers=H)).status_code == 200
    run = await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31"}, headers=H)
    assert run.status_code in (200, 201), run.text
    assert run.json()["hourly_total"] == 160


# ── LAB-03 ──────────────────────────────────────────────────────────────────────────────────────
async def test_lab03_single_entry_approval_pays_the_entry_once(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-04T09:00:00", 8)
    r = await client.post(f"/api/timeclock/entries/{eid}/approve", headers=H)
    assert r.status_code == 200, r.text
    assert await _wages(db, org, emp) == 160                   # was 0
    # the weekly timesheet approval afterwards has nothing left to pay
    assert (await _approve_window(client, H, emp, "2026-08-01", "2026-08-31")).status_code == 404
    assert await _wages(db, org, emp) == 160


async def test_lab03_single_approval_pays_nothing_for_attendance_paid_staff(client, db):
    from backend.models import Employee
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    (await db.execute(select(Employee).where(Employee.id == emp))).scalar_one().hourly_basis = "attendance"
    await db.commit()
    eid = await _entry(db, org, emp, "2026-08-04T09:00:00", 8)
    assert (await client.post(f"/api/timeclock/entries/{eid}/approve", headers=H)).status_code == 200
    assert await _wages(db, org, emp) == 0


async def test_lab03_nobody_approves_and_pays_their_own_entry(client, db):
    H, org, _ = await _register(client)
    _, MH = await _teammate(client, H, await _role(db, org))
    r = await client.post("/api/timeclock/entries", json={
        "started_at": "2026-08-04T09:00:00", "ended_at": "2026-08-04T17:00:00", "category": "admin"}, headers=MH)
    assert r.status_code == 201, r.text
    assert (await client.post(f"/api/timeclock/entries/{r.json()['id']}/approve", headers=MH)).status_code == 403


# ── LAB-02 / RPT-04 ─────────────────────────────────────────────────────────────────────────────
async def _attendance_emp(client, H, db, org):
    from backend.models import Employee
    emp, _ = await _teammate(client, H, await _role(db, org))
    e = (await db.execute(select(Employee).where(Employee.id == emp))).scalar_one()
    e.hourly_basis = "attendance"
    e.hourly_cost_rate = 20
    await db.commit()
    return emp


async def _shift(db, org, emp, start, hours):
    from backend.models import Location, Shift
    loc = (await db.execute(select(Location.id).where(Location.org_id == org))).scalars().first()
    s0 = datetime.fromisoformat(start)
    s = Shift(employee_id=emp, location_id=loc, started_at=s0, ended_at=s0 + timedelta(hours=hours),
              hours_worked=hours)
    db.add(s)
    await db.commit()
    return s.id


async def test_lab02_overlapping_attendance_windows_pay_each_shift_once(client, db):
    H, org, _ = await _register(client)
    emp = await _attendance_emp(client, H, db, org)
    await _shift(db, org, emp, "2026-08-03T09:00:00", 8)
    a = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-15"}, headers=H)
    assert a.status_code == 201 and a.json()["ledger_posted"] is True, a.text
    await _shift(db, org, emp, "2026-08-20T09:00:00", 4)
    b = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}, headers=H)
    assert b.status_code == 201, b.text
    assert b.json()["wage_posted"] == 80 and b.json()["hours"] == 4      # only the new shift
    assert await _wages(db, org, emp) == 240                              # was 400 (8h paid twice)


async def test_lab02_window_posted_before_per_shift_wages_is_not_paid_again(client, db):
    from backend.models import EmployeeBalanceLedger as L
    H, org, _ = await _register(client)
    emp = await _attendance_emp(client, H, db, org)
    await _shift(db, org, emp, "2026-08-03T09:00:00", 8)
    db.add(L(org_id=org, employee_id=emp, entry_type="hourly_wage", amount=160, ref_type="attendance",
             ref_id=f"{emp}:2026-08-01:2026-08-15", idempotency_key=f"attendance:{emp}:2026-08-01:2026-08-15",
             created_at=datetime(2026, 8, 15, 12)))
    await db.commit()
    b = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}, headers=H)
    assert b.status_code == 201 and b.json()["ledger_posted"] is False, b.text
    assert await _wages(db, org, emp) == 160


async def test_rpt04_attendance_window_is_the_shops_month(client, db):
    """New York shop: a shift at 21:00 local on 31 Aug (01:00 UTC on 1 Sep) is August's."""
    H, org, _ = await _register(client)
    await _set_tz(client, H, "America/New_York")
    emp = await _attendance_emp(client, H, db, org)
    await _shift(db, org, emp, "2026-09-01T01:00:00", 2)
    sep = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": emp, "date_from": "2026-09-01", "date_to": "2026-09-30"}, headers=H)
    assert sep.status_code == 201 and sep.json()["ledger_posted"] is False, sep.text
    aug = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}, headers=H)
    assert aug.json()["wage_posted"] == 40, aug.text


# ── LAB-04 ──────────────────────────────────────────────────────────────────────────────────────
async def test_lab04_own_edit_cannot_overlap_another_entry(client, db):
    H, org, _ = await _register(client)
    emp, TH = await _teammate(client, H, await _role(db, org))
    e1 = await _entry(db, org, emp, "2026-08-05T09:00:00", 2)
    await _entry(db, org, emp, "2026-08-05T12:00:00", 2)
    r = await client.patch(f"/api/timeclock/entries/{e1}", json={"ended_at": "2026-08-05T20:00:00"}, headers=TH)
    assert r.status_code == 409, r.text                     # was 200
    r = await client.patch(f"/api/timeclock/entries/{e1}", json={"ended_at": "2026-08-05T20:00:00"}, headers=H)
    assert r.status_code == 409, r.text                     # a manager can't make overlapping hours either
    r = await client.patch(f"/api/timeclock/entries/{e1}", json={"ended_at": "2026-08-05T12:00:00"}, headers=H)
    assert r.status_code == 200, r.text                     # touching end-to-start is fine


async def test_lab04_own_time_change_without_payroll_rights_is_flagged_for_review(client, db):
    H, org, _ = await _register(client)
    emp, TH = await _teammate(client, H, await _role(db, org, {"can_manage_payroll": False}))
    e1 = await _entry(db, org, emp, "2026-08-05T09:00:00", 2)
    r = await client.patch(f"/api/timeclock/entries/{e1}", json={"note": "fixed"}, headers=TH)
    assert r.status_code == 200 and r.json()["needs_review"] is False, r.text
    r = await client.patch(f"/api/timeclock/entries/{e1}", json={"ended_at": "2026-08-05T15:00:00"}, headers=TH)
    assert r.status_code == 200, r.text
    assert r.json()["needs_review"] is True


async def test_lab04_v1_api_refuses_overlapping_time(client, db):
    H, org, me = await _register(client)
    k = await client.post("/api/api-keys", json={"name": "zap", "scopes": ["read", "write"]}, headers=H)
    assert k.status_code == 201, k.text
    A = {"Authorization": f"Bearer {k.json()['key']}"}
    r = await client.post("/api/v1/time-entries", headers=A, json={
        "employee_id": me, "started_at": "2026-10-20T09:00:00", "duration_min": 120})
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/time-entries", headers=A, json={
        "employee_id": me, "started_at": "2026-10-20T10:00:00", "duration_min": 30})
    assert r.status_code == 409, r.text


# ── RPT-01 / LAB-06 ─────────────────────────────────────────────────────────────────────────────
async def _ny_day(client, H):
    """One local working day in New York (EST, UTC-5), 3 Nov 2026: 09:00-17:00, then 19:30-22:00.
    In UTC the evening entry is on 4 Nov."""
    await _set_tz(client, H, "America/New_York")
    for s, e in (("2026-11-03T14:00:00Z", "2026-11-03T22:00:00Z"), ("2026-11-04T00:30:00Z", "2026-11-04T03:00:00Z")):
        r = await client.post("/api/timeclock/entries", headers=H,
                              json={"started_at": s, "ended_at": e, "category": "job"})
        assert r.status_code == 201, r.text


async def test_rpt01_timesheet_counts_the_shops_day(client, tenant):
    await _ny_day(client, tenant)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    ts = await client.get(f"/api/timeclock/timesheet?employee_id={me['id']}&date_from=2026-11-03&date_to=2026-11-03",
                          headers=tenant)
    assert ts.status_code == 200, ts.text
    body = ts.json()
    assert body["entry_count"] == 2 and body["total_hours"] == 10.5     # was 1 entry, 8.0 h
    assert body["overtime_hours"] == 2.5                               # was 0.0
    rep = await client.get("/api/reports/run/labor_cost?date_from=2026-11-03&date_to=2026-11-03", headers=tenant)
    assert rep.status_code == 200, rep.text
    rows = dict((r[0], r[1]) for r in rep.json()["rows"])
    assert rows["Overtime hours (>8h/day)"] == 2.5                    # was 0.0


async def test_rpt01_timesheet_approval_pays_the_shops_day_overtime(client, db):
    from backend.models import Employee
    H, org, _ = await _register(client)
    emp, TH = await _teammate(client, H, await _role(db, org))
    e = (await db.execute(select(Employee).where(Employee.id == emp))).scalar_one()
    e.hourly_cost_rate = 20
    await db.commit()
    await _ny_day(client, TH)
    r = await _approve_window(client, H, emp, "2026-11-03", "2026-11-03")
    assert r.status_code == 200, r.text
    assert r.json()["approved"] == 2
    assert r.json()["overtime_hours"] == 2.5
    assert await _wages(db, org, emp) == 8 * 20 + 2.5 * 30            # 235


# ── WO-02 ───────────────────────────────────────────────────────────────────────────────────────
async def _job_with_time(client, H, db, *, status=None, rate=50):
    from backend.models import TimeEntry
    from backend.services.dates import now_utc
    me = (await client.get("/api/auth/me", headers=H)).json()
    cid = (await client.post("/api/clients", json={"name": "T"}, headers=H)).json()["id"]
    oid = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=H)).json()["id"]
    if status:
        r = await client.patch(f"/api/orders/{oid}/status", json={"status": status}, headers=H)
        assert r.status_code == 200, r.text
    st = now_utc() - timedelta(hours=2)
    db.add(TimeEntry(org_id=me["org_id"], employee_id=me["id"], order_id=oid, started_at=st,
                     ended_at=st + timedelta(hours=1), duration_sec=3600, billable=True,
                     billable_rate=rate, state="stopped"))
    await db.commit()
    return oid, me["org_id"]


async def _lines(db, oid):
    from backend.models import OrderLine
    db.expire_all()
    return (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all()


async def test_wo02_billing_tracked_time_twice_bills_it_once(client, tenant, db):
    from backend.models import Order
    oid, _ = await _job_with_time(client, tenant, db)
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 50
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=tenant)
    assert r.status_code == 409, r.text                              # was 200 and a second line
    lines = await _lines(db, oid)
    assert len(lines) == 1 and float(lines[0].total) == 50
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    assert float(o.total) == 50


async def test_wo02_rate_comes_from_the_tracked_time_not_the_request(client, tenant, db):
    oid, _ = await _job_with_time(client, tenant, db, rate=50)
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={"rate": 1}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["rate"] == 50 and r.json()["amount"] == 50


async def test_wo02_closed_job_needs_edit_closed_ticket(client, tenant, db):
    H = tenant
    oid, org = await _job_with_time(client, H, db, status="done")
    # a teammate who can't edit closed tickets can't bill time onto a Done job
    _, TH = await _teammate(client, H, await _role(db, org, {"can_edit_closed_ticket": False}))
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=TH)
    assert r.status_code == 403, r.text
    assert await _lines(db, oid) == []


async def test_wo02_billing_needs_the_custom_service_right(client, tenant, db):
    oid, org = await _job_with_time(client, tenant, db)
    _, TH = await _teammate(client, tenant, await _role(db, org, {"can_add_custom_service": False}))
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=TH)
    assert r.status_code == 403, r.text
    assert await _lines(db, oid) == []
