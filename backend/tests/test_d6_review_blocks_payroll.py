"""Owner decision 6: time flagged for review blocks payroll approval for its period.

A time entry (job clock) or attendance shift with needs_review=True stops every payroll approval
whose period holds it: approving a timesheet window or one entry, posting attendance wages for a
window, and accruing a payroll run (single, month calculation, or the bulk run). The refusal is a
409 that names each flagged item by person, shop day and why it was flagged. A manager resolves it
with the edit endpoint (PATCH /timeclock/entries/{id} or PATCH /timeclock/shifts/{id}); that edit
clears the flag and writes an audit row.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _register(client):
    email = f"d6-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    H = {"Authorization": f"Bearer {body['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, body["org_id"], me["id"]


async def _role(db, org_id, perms=None):
    from backend.models import Role
    if perms is None:
        return (await db.execute(select(Role.id).where(Role.org_id == org_id, Role.name == "admin"))).scalar_one()
    role = Role(org_id=org_id, name=f"r-{uuid.uuid4().hex[:4]}", permissions=perms)
    db.add(role)
    await db.commit()
    return role.id


async def _teammate(client, H, role_id, name="Ana Silva"):
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": name, "email": email, "password": "pw-test-12345", "role_id": role_id}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return r.json()["id"], {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _entry(db, org, emp, when, hours, rate=20.0, state="stopped", **kw):
    from backend.models import TimeEntry
    start = datetime.fromisoformat(when)
    e = TimeEntry(org_id=org, employee_id=emp, started_at=start, ended_at=start + timedelta(hours=hours),
                  duration_sec=int(hours * 3600), cost_rate=rate, state=state, source="manual", **kw)
    db.add(e)
    await db.commit()
    return e.id


async def _shift(db, org, emp, start, hours, **kw):
    from backend.models import Location, Shift
    loc = (await db.execute(select(Location.id).where(Location.org_id == org))).scalars().first()
    s0 = datetime.fromisoformat(start)
    s = Shift(employee_id=emp, location_id=loc, started_at=s0, ended_at=s0 + timedelta(hours=hours),
              hours_worked=hours, **kw)
    db.add(s)
    await db.commit()
    return s.id


async def _attendance(db, emp):
    from backend.models import Employee
    e = (await db.execute(select(Employee).where(Employee.id == emp))).scalar_one()
    e.hourly_basis = "attendance"
    e.hourly_cost_rate = 20
    await db.commit()


async def _approve_window(client, H, emp, d0, d1):
    return await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": d0, "date_to": d1}, headers=H)


async def _fresh(db, model, rid):
    return (await db.execute(select(model).where(model.id == rid)
                             .execution_options(populate_existing=True))).scalar_one()


def _blocked(r, n=1):
    """The 409 body: a readable message and the flagged items (name, day, why), never ids only."""
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert isinstance(d, dict) and isinstance(d["message"], str), d
    assert len(d["flagged"]) == n, d
    for f in d["flagged"]:
        assert f["employee_name"] and f["date"] and f["reason"], f
        assert f["employee_name"] in d["message"] and f["reason"] in d["message"], d
    return d


# ── timesheet approval (job clock) ───────────────────────────────────────────────────────────────
async def test_flagged_entry_blocks_timesheet_approval_with_names_days_and_reasons(client, db):
    from backend.models import EmployeeBalanceLedger, TimeEntry
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    ok = await _entry(db, org, emp, "2026-08-03T09:00:00", 4)
    bad = await _entry(db, org, emp, "2026-08-05T09:00:00", 12, needs_review=True)
    r = await _approve_window(client, H, emp, "2026-08-01", "2026-08-31")
    d = _blocked(r)
    [f] = d["flagged"]
    assert f["employee_name"] == "Ana Silva" and f["date"] == "2026-08-05" and f["kind"] == "time_entry"
    assert "12 h" in f["reason"]                           # a timer stopped at the 12 h cap
    assert (f["started_at"], f["ended_at"]) == ("2026-08-05T09:00:00Z", "2026-08-05T21:00:00Z")   # for Edit
    assert "5 Aug 2026" in d["message"]
    # nothing was approved or paid
    assert (await _fresh(db, TimeEntry, ok)).state == "stopped"
    assert (await _fresh(db, TimeEntry, bad)).state == "stopped"
    assert not (await db.execute(select(EmployeeBalanceLedger.id).where(
        EmployeeBalanceLedger.employee_id == emp))).first()


async def test_only_flagged_entries_inside_the_window_block(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    other, _ = await _teammate(client, H, await _role(db, org), name="Ben")
    await _entry(db, org, emp, "2026-08-03T09:00:00", 4)
    await _entry(db, org, emp, "2026-08-10T09:00:00", 2, needs_review=True)        # later in August
    await _entry(db, org, other, "2026-08-03T13:00:00", 2, needs_review=True)      # someone else
    await _entry(db, org, emp, "2026-08-04T09:00:00", 2, needs_review=True, state="void")   # voided
    r = await _approve_window(client, H, emp, "2026-08-01", "2026-08-05")
    assert r.status_code == 200, r.text
    assert r.json()["approved"] == 1


async def test_window_is_the_shops_days(client, db):
    """New York shop: 01:00 UTC on 1 Sep is 31 Aug there, so it blocks August, not September."""
    H, org, _ = await _register(client)
    r = await client.put("/api/staff/company", json={"timezone": "America/New_York"}, headers=H)
    assert r.status_code == 200, r.text
    emp, _ = await _teammate(client, H, await _role(db, org))
    await _entry(db, org, emp, "2026-09-01T01:00:00", 1, needs_review=True)
    await _entry(db, org, emp, "2026-09-02T14:00:00", 1)
    d = _blocked(await _approve_window(client, H, emp, "2026-08-31", "2026-08-31"))
    assert d["flagged"][0]["date"] == "2026-08-31"
    assert (await _approve_window(client, H, emp, "2026-09-01", "2026-09-30")).status_code == 200


async def test_reasons_say_why_each_entry_was_flagged(client, db):
    H, org, _ = await _register(client)
    emp, TH = await _teammate(client, H, await _role(db, org, {"can_manage_payroll": False}))
    own = await _entry(db, org, emp, "2026-08-03T09:00:00", 2)
    r = await client.patch(f"/api/timeclock/entries/{own}", json={"ended_at": "2026-08-03T15:00:00"}, headers=TH)
    assert r.status_code == 200 and r.json()["needs_review"] is True, r.text
    await _entry(db, org, emp, "2026-08-04T09:00:00", 0, needs_review=True)
    await _entry(db, org, emp, "2026-08-05T09:00:00", 3, needs_review=True)
    d = _blocked(await _approve_window(client, H, emp, "2026-08-01", "2026-08-31"), n=3)
    by_day = {f["date"]: f["reason"] for f in d["flagged"]}
    assert "changed" in by_day["2026-08-03"] and "Ana Silva" in by_day["2026-08-03"]
    assert "no time" in by_day["2026-08-04"]
    assert "cut short" in by_day["2026-08-05"]
    assert [f["date"] for f in d["flagged"]] == ["2026-08-03", "2026-08-04", "2026-08-05"]


async def test_manager_edit_resolves_the_flag_with_an_audit_row(client, db):
    from backend.models import TimeEntry, TimeEntryAudit
    H, org, me = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-05T09:00:00", 12, needs_review=True)
    r = await client.patch(f"/api/timeclock/entries/{eid}", json={"ended_at": "2026-08-05T17:00:00"}, headers=H)
    assert r.status_code == 200 and r.json()["needs_review"] is False, r.text
    assert (await _fresh(db, TimeEntry, eid)).needs_review is False
    rows = (await db.execute(select(TimeEntryAudit).where(
        TimeEntryAudit.time_entry_id == eid, TimeEntryAudit.field == "needs_review"))).scalars().all()
    assert len(rows) == 1 and rows[0].actor_id == me
    assert (rows[0].old_value, rows[0].new_value) == ("True", "False")
    r = await _approve_window(client, H, emp, "2026-08-01", "2026-08-31")
    assert r.status_code == 200 and r.json()["approved"] == 1, r.text


async def test_manager_can_confirm_a_flagged_entry_as_it_is(client, db):
    from backend.models import TimeEntry, TimeEntryAudit
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-05T09:00:00", 12, needs_review=True)
    r = await client.patch(f"/api/timeclock/entries/{eid}", json={}, headers=H)
    assert r.status_code == 200 and r.json()["needs_review"] is False, r.text
    e = await _fresh(db, TimeEntry, eid)
    assert e.needs_review is False and e.duration_sec == 12 * 3600
    assert (await db.execute(select(TimeEntryAudit.id).where(
        TimeEntryAudit.time_entry_id == eid, TimeEntryAudit.field == "needs_review"))).first()


async def test_the_employees_own_edit_does_not_resolve_it(client, db):
    from backend.models import TimeEntry
    H, org, _ = await _register(client)
    emp, TH = await _teammate(client, H, await _role(db, org))          # has payroll rights itself
    eid = await _entry(db, org, emp, "2026-08-05T09:00:00", 12, needs_review=True)
    r = await client.patch(f"/api/timeclock/entries/{eid}", json={"ended_at": "2026-08-05T17:00:00"}, headers=TH)
    assert r.status_code == 200 and r.json()["needs_review"] is True, r.text
    assert (await _fresh(db, TimeEntry, eid)).needs_review is True


async def test_flagged_entry_blocks_approving_it_alone(client, db):
    from backend.models import TimeEntry
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-05T09:00:00", 12, needs_review=True)
    _blocked(await client.post(f"/api/timeclock/entries/{eid}/approve", headers=H))
    assert (await _fresh(db, TimeEntry, eid)).state == "stopped"
    assert (await client.patch(f"/api/timeclock/entries/{eid}", json={}, headers=H)).status_code == 200
    r = await client.post(f"/api/timeclock/entries/{eid}/approve", headers=H)
    assert r.status_code == 200 and r.json()["state"] == "approved", r.text


# ── attendance shifts ────────────────────────────────────────────────────────────────────────────
async def test_capped_clock_out_sets_the_flag_on_the_shift(client, db):
    from backend.models import Shift
    from backend.services.dates import now_utc
    H, org, me = await _register(client)
    sid = await _shift(db, org, me, (now_utc() - timedelta(hours=40)).isoformat(), 0)
    s = await _fresh(db, Shift, sid)
    s.ended_at = None
    await db.commit()
    r = await client.post("/api/timeclock/shift/clock-out", headers=H)
    assert r.status_code == 200 and r.json()["needs_review"] is True, r.text
    assert (await _fresh(db, Shift, sid)).needs_review is True


async def test_flagged_shift_blocks_attendance_wages_until_corrected(client, db):
    from backend.models import AuditLog, Shift
    H, org, me = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    await _attendance(db, emp)
    await _shift(db, org, emp, "2026-08-03T09:00:00", 8)
    sid = await _shift(db, org, emp, "2026-08-04T08:00:00", 16, needs_review=True)
    db.add(AuditLog(org_id=org, actor_id=emp, action="shift.auto_capped", entity_type="shift", entity_id=sid,
                    diff={"elapsed_hours": 26.5, "cap_hours": 16}))
    await db.commit()
    body = {"employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}
    d = _blocked(await client.post("/api/payroll/attendance-wages", json=body, headers=H))
    [f] = d["flagged"]
    assert f["kind"] == "shift" and f["date"] == "2026-08-04" and "16 h" in f["reason"]
    assert "4 Aug 2026" in d["message"]
    # a window without it is fine
    r = await client.post("/api/payroll/attendance-wages", headers=H,
                          json={**body, "date_to": "2026-08-03"})
    assert r.status_code == 201 and r.json()["shifts_paid"] == 1, r.text
    r = await client.patch(f"/api/timeclock/shifts/{sid}", headers=H, json={
        "ended_at": "2026-08-04T17:00:00Z", "reason": "left at 5"})
    assert r.status_code == 200 and r.json()["shift"]["needs_review"] is False, r.text
    assert (await _fresh(db, Shift, sid)).needs_review is False
    a = (await db.execute(select(AuditLog).where(
        AuditLog.entity_id == sid, AuditLog.action == "shift.correct"))).scalar_one()
    assert a.actor_id == me and a.diff["needs_review"] == [True, False]
    r = await client.post("/api/payroll/attendance-wages", json=body, headers=H)
    assert r.status_code == 201 and r.json()["shifts_paid"] == 1, r.text


async def test_a_flagged_empty_duplicate_shift_can_be_confirmed(client, db):
    """w4s01 closed leaked duplicate open shifts at their own start with 0 hours and flagged them.
    The manager can confirm one as it is (just a reason), which resolves it."""
    from backend.models import Shift
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    await _attendance(db, emp)
    sid = await _shift(db, org, emp, "2026-08-04T08:00:00", 0, needs_review=True)
    body = {"employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}
    d = _blocked(await client.post("/api/payroll/attendance-wages", json=body, headers=H))
    assert "no time" in d["flagged"][0]["reason"]
    r = await client.patch(f"/api/timeclock/shifts/{sid}", headers=H, json={"reason": "duplicate, nothing worked"})
    assert r.status_code == 200, r.text
    assert (await _fresh(db, Shift, sid)).needs_review is False
    assert (await client.post("/api/payroll/attendance-wages", json=body, headers=H)).status_code == 201


# ── payroll accruals ─────────────────────────────────────────────────────────────────────────────
async def test_flagged_time_blocks_accruing_that_month(client, db):
    from backend.models import PayrollRun
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-05T09:00:00", 12, needs_review=True)
    run = {"employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31"}
    _blocked(await client.post("/api/payroll/run", json=run, headers=H))
    _blocked(await client.post("/api/payroll/calculation/accrue", json={"employee_id": emp, "month": "2026-08"},
                               headers=H))
    bulk = await client.post("/api/payroll/run-all", headers=H, json={
        "period_start": "2026-08-01", "period_end": "2026-08-31", "employee_ids": [emp]})
    assert bulk.status_code == 201, bulk.text
    [res] = bulk.json()["results"]
    assert res["status"] == "skipped" and "Ana Silva" in res["reason"] and "5 Aug 2026" in res["reason"]
    assert res["flagged"][0]["date"] == "2026-08-05"
    assert not (await db.execute(select(PayrollRun.id).where(
        PayrollRun.employee_id == emp, PayrollRun.status == "accrued"))).first()
    # a draft is not an approval, and another month is not blocked
    assert (await client.post("/api/payroll/run", json={**run, "draft": True}, headers=H)).status_code == 201
    r = await client.post("/api/payroll/run", headers=H, json={
        "employee_id": emp, "period_start": "2026-07-01", "period_end": "2026-07-31"})
    assert r.status_code == 201, r.text
    # resolved: the month accrues
    assert (await client.patch(f"/api/timeclock/entries/{eid}", json={}, headers=H)).status_code == 200
    assert (await client.post("/api/payroll/run", json=run, headers=H)).status_code == 201


async def test_flagged_shift_blocks_accruing_that_month(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    await _attendance(db, emp)
    await _shift(db, org, emp, "2026-08-04T08:00:00", 16, needs_review=True)
    d = _blocked(await client.post("/api/payroll/calculation/accrue", headers=H,
                                   json={"employee_id": emp, "month": "2026-08"}))
    assert d["flagged"][0]["kind"] == "shift"


async def test_review_reasons_use_the_clocks_own_limits():
    from backend.routers import timeclock
    from backend.services import time_review
    assert time_review.AUTO_STOP_HOURS == timeclock.AUTO_STOP_HOURS
    assert time_review.SHIFT_CAP_HOURS == timeclock.SHIFT_CAP_HOURS


# ── only the clock that pays the person blocks (owner, on #255) ──────────────────────────────────
# hourly_basis "attendance" is paid from shifts; anything else (null = labour) from job time entries.
async def test_labour_staff_are_not_blocked_by_a_flagged_shift(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    await _entry(db, org, emp, "2026-08-03T09:00:00", 4)
    await _shift(db, org, emp, "2026-08-04T08:00:00", 16, needs_review=True)
    assert (await _approve_window(client, H, emp, "2026-08-01", "2026-08-31")).status_code == 200
    r = await client.post("/api/payroll/calculation/accrue", json={"employee_id": emp, "month": "2026-08"}, headers=H)
    assert r.status_code == 201, r.text


async def test_attendance_staff_are_not_blocked_by_a_flagged_time_entry(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    await _attendance(db, emp)
    eid = await _entry(db, org, emp, "2026-08-05T09:00:00", 12, needs_review=True)
    assert (await _approve_window(client, H, emp, "2026-08-01", "2026-08-31")).status_code == 200
    r = await client.post("/api/payroll/run", headers=H, json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31"})
    assert r.status_code == 201, r.text
    from backend.models import TimeEntry
    assert (await _fresh(db, TimeEntry, eid)).needs_review is True      # still flagged, just not blocking


async def test_attendance_staff_can_approve_a_flagged_entry_alone(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    await _attendance(db, emp)
    eid = await _entry(db, org, emp, "2026-08-05T09:00:00", 12, needs_review=True)
    r = await client.post(f"/api/timeclock/entries/{eid}/approve", headers=H)
    assert r.status_code == 200 and r.json()["wage_posted"] == 0, r.text


async def test_flags_on_both_clocks_only_the_paying_one_blocks(client, db):
    H, org, _ = await _register(client)
    lab, _ = await _teammate(client, H, await _role(db, org), name="Lena Labour")
    att, _ = await _teammate(client, H, await _role(db, org), name="Alex Attend")
    await _attendance(db, att)
    for p in (lab, att):
        await _entry(db, org, p, "2026-08-05T09:00:00", 12, needs_review=True)
        await _shift(db, org, p, "2026-08-06T08:00:00", 16, needs_review=True)
    d = _blocked(await client.post("/api/payroll/calculation/accrue", headers=H,
                                   json={"employee_id": lab, "month": "2026-08"}))
    assert [(f["kind"], f["date"]) for f in d["flagged"]] == [("time_entry", "2026-08-05")]
    d = _blocked(await client.post("/api/payroll/calculation/accrue", headers=H,
                                   json={"employee_id": att, "month": "2026-08"}))
    assert [(f["kind"], f["date"]) for f in d["flagged"]] == [("shift", "2026-08-06")]


async def test_run_all_blocks_each_person_only_on_their_paying_clock(client, db):
    H, org, _ = await _register(client)
    lab_ok, _ = await _teammate(client, H, await _role(db, org), name="Lena Labour")
    att_ok, _ = await _teammate(client, H, await _role(db, org), name="Alex Attend")
    lab_bad, _ = await _teammate(client, H, await _role(db, org), name="Lou Labour")
    att_bad, _ = await _teammate(client, H, await _role(db, org), name="Ari Attend")
    for p in (att_ok, att_bad):
        await _attendance(db, p)
    await _shift(db, org, lab_ok, "2026-08-04T08:00:00", 16, needs_review=True)     # not their clock
    await _entry(db, org, att_ok, "2026-08-05T09:00:00", 12, needs_review=True)     # not their clock
    await _entry(db, org, lab_bad, "2026-08-05T09:00:00", 12, needs_review=True)
    await _shift(db, org, att_bad, "2026-08-04T08:00:00", 16, needs_review=True)
    r = await client.post("/api/payroll/run-all", headers=H, json={
        "period_start": "2026-08-01", "period_end": "2026-08-31",
        "employee_ids": [lab_ok, att_ok, lab_bad, att_bad]})
    assert r.status_code == 201, r.text
    res = {x["employee_id"]: x for x in r.json()["results"]}
    assert res[lab_ok]["status"] == "accrued" and res[att_ok]["status"] == "accrued", res
    assert res[lab_bad]["status"] == "skipped" and res[lab_bad]["flagged"][0]["kind"] == "time_entry"
    assert res[att_bad]["status"] == "skipped" and res[att_bad]["flagged"][0]["kind"] == "shift"
    assert r.json()["accrued"] == 2 and r.json()["skipped"] == 2
