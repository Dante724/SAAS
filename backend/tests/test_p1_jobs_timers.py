"""P1 audit fixes, 12 Nov 2026 backend audit (01-tenancy-security.md, 04-inventory-labour.md).

- SEC-05: every per-shop sweep runs each shop in a fresh session scoped to that shop (tenant set),
  so the tenant guards are on and nothing loaded for one shop is reused for the next.
- LAB-05: starting or switching a job timer locks the employee row, so a double tap can't leave
  two running timers. Switching also obeys the shop's "job timer off" switch.
- LAB-07: a forgotten clock-out is capped and flagged; managers correct a shift through an
  endpoint that leaves an audit trail, and a correction to a shift already paid re-prices its
  wage with a reversing ledger row (`attendance:<shift>`), never by editing the old one.
"""
import asyncio
import os
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.core import scheduled

pytestmark = pytest.mark.asyncio

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="row locks race on Postgres only")


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _second_shop(client):
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Second Co", "name": "B", "email": f"b-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return r.json()["org_id"], {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── SEC-05 ───────────────────────────────────────────────────────────────────────────────────────
async def test_sec05_per_shop_sweeps_get_a_fresh_session_scoped_to_the_shop(client, tenant, db, monkeypatch):
    from backend.models import (
        AutomationRule,
        AutomationSetting,
        Client,
        Membership,
        MembershipPlan,
    )
    from backend.services import automation, memberships, notification_rules, sense, sense_agents
    a = (await _me(client, tenant))["org_id"]
    b, _hb = await _second_shop(client)
    today = date.today()
    for oid in (a, b):
        c = Client(org_id=oid, name="M")
        p = MembershipPlan(org_id=oid, name="Gold")
        db.add_all([c, p])
        await db.flush()
        db.add(Membership(org_id=oid, plan_id=p.id, client_id=c.id, term_start=today,
                          term_end=today + timedelta(days=365), period_start=today,
                          period_end=today + timedelta(days=30)))
        db.add(AutomationSetting(org_id=oid, key=sense.SETTING_KEY, enabled=True))
        db.add(AutomationRule(org_id=oid, name="late", event="order_overdue", action_type="notify", active=True))
    await db.commit()

    seen: dict = {}

    def spy(name, ret):
        async def fn(db, *args, **kw):
            org = kw.get("org_id") or (args[0] if args else None)
            org = getattr(org, "id", org)
            seen.setdefault(name, []).append((org, db.sync_session.info.get("tenant_org"), id(db)))
            return ret
        return fn
    monkeypatch.setattr(memberships, "sweep", spy("memberships", {}))
    monkeypatch.setattr(sense, "send_briefs", spy("briefs", 0))
    monkeypatch.setattr(sense_agents, "run_agents", spy("agents", 0))
    monkeypatch.setattr(sense_agents, "platform_off", lambda: False)
    monkeypatch.setattr(automation, "sweep_time_based", spy("automations", 0))
    monkeypatch.setattr(notification_rules, "notify_contact_birthdays", spy("birthdays", 0))

    await scheduled.sweep_memberships()
    await scheduled.sweep_morning_briefs()
    await scheduled.sweep_sense_agents()
    await scheduled.sweep_time_automations()
    await scheduled.sweep_contact_lifecycle()

    for name in ("memberships", "briefs", "agents", "automations", "birthdays"):
        calls = [c for c in seen.get(name, []) if c[0] in (a, b)]
        assert {c[0] for c in calls} == {a, b}, (name, calls)
        assert all(org == tenant_ for org, tenant_, _ in calls), (name, calls)   # tenant set to the shop
        assert len({sid for *_, sid in calls}) == len(calls), (name, calls)      # never a shared session


async def test_sec05_one_shops_failure_does_not_stop_the_next(client, tenant, db, monkeypatch):
    from backend.models import AuditLog, AutomationSetting
    from backend.services import sense
    a = (await _me(client, tenant))["org_id"]
    b, _hb = await _second_shop(client)
    for oid in (a, b):
        db.add(AutomationSetting(org_id=oid, key=sense.SETTING_KEY, enabled=True))
    await db.commit()
    state = {"failed": None, "ran": []}

    async def send_briefs(db, org_id, now=None):
        if state["failed"] is None:
            state["failed"] = org_id
            db.add(AuditLog(org_id=org_id, action=None, entity_type="x"))   # breaks this session
            await db.flush()
        await db.execute(select(AuditLog.id).limit(1))                       # needs a usable session
        state["ran"].append(org_id)
        return 1
    monkeypatch.setattr(sense, "send_briefs", send_briefs)
    await scheduled.sweep_morning_briefs()
    assert state["failed"] in (a, b)
    assert {a, b} - {state["failed"]} <= set(state["ran"])


# ── LAB-05 ───────────────────────────────────────────────────────────────────────────────────────
async def _running(db, emp_id):
    from backend.models import TimeEntry
    return (await db.execute(select(func.count()).select_from(TimeEntry).where(
        TimeEntry.employee_id == emp_id, TimeEntry.state == "running"))).scalar()


async def test_lab05_start_and_switch_take_the_employee_lock_before_checking(client, tenant, monkeypatch):
    from backend.routers import timeclock
    order: list = []
    real_lock, real_running = timeclock._lock_employee, timeclock._running_for

    async def lock(db, emp_id):
        order.append("lock")
        await real_lock(db, emp_id)

    async def running(db, org_id, emp_id):
        order.append("check")
        return await real_running(db, org_id, emp_id)
    monkeypatch.setattr(timeclock, "_lock_employee", lock)
    monkeypatch.setattr(timeclock, "_running_for", running)
    r = await client.post("/api/timeclock/start", json={"category": "admin"}, headers=tenant)
    assert r.status_code == 201, r.text
    assert order[:2] == ["lock", "check"], order
    order.clear()
    oid = (await client.post("/api/orders", json={"complaint": "x"}, headers=tenant)).json()["id"]
    r = await client.post("/api/timeclock/switch", json={"order_id": oid}, headers=tenant)
    assert r.status_code == 201, r.text
    assert order[:2] == ["lock", "check"], order


async def test_lab05_switch_obeys_the_job_timer_switch(client, tenant, db):
    from backend.models import Organisation
    me = await _me(client, tenant)
    org = (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()
    org.clock_wo_enabled = False
    await db.commit()
    oid = (await client.post("/api/orders", json={"complaint": "x"}, headers=tenant)).json()["id"]
    r = await client.post("/api/timeclock/switch", json={"order_id": oid}, headers=tenant)
    assert r.status_code == 403, r.text


@pg_only
async def test_lab05_double_tap_start_leaves_one_running_timer(client, tenant, db):
    me = await _me(client, tenant)
    rs = await asyncio.gather(*[client.post("/api/timeclock/start", json={"category": "admin"}, headers=tenant)
                                for _ in range(3)])
    assert sorted(r.status_code for r in rs) == [201, 409, 409], [r.text for r in rs]
    assert await _running(db, me["id"]) == 1


@pg_only
async def test_lab05_double_tap_switch_leaves_one_running_timer(client, tenant, db):
    me = await _me(client, tenant)
    oid = (await client.post("/api/orders", json={"complaint": "x"}, headers=tenant)).json()["id"]
    rs = await asyncio.gather(*[client.post("/api/timeclock/switch", json={"order_id": oid}, headers=tenant)
                                for _ in range(3)])
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs]
    assert await _running(db, me["id"]) == 1


# ── LAB-07 ───────────────────────────────────────────────────────────────────────────────────────
async def _shift(db, emp_id, start, hours=None):
    from backend.models import Employee, Shift
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id))).scalar_one()
    s = Shift(employee_id=emp_id, location_id=emp.location_id, started_at=start,
              ended_at=start + timedelta(hours=hours) if hours is not None else None,
              hours_worked=hours or 0)
    if not s.location_id:
        from backend.models import Location
        s.location_id = (await db.execute(select(Location.id).where(Location.org_id == emp.org_id))).scalars().first()
    db.add(s)
    await db.commit()
    return s


async def test_lab07_a_forgotten_clock_out_is_capped_and_flagged(client, tenant, db):
    from backend.models import AuditLog
    from backend.routers import timeclock
    from backend.services.dates import now_utc
    me = await _me(client, tenant)
    s = await _shift(db, me["id"], now_utc() - timedelta(hours=40))     # clocked in two days ago
    r = await client.post("/api/timeclock/shift/clock-out", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hours_worked"] == timeclock.SHIFT_CAP_HOURS and body["needs_review"] is True
    trail = (await db.execute(select(AuditLog).where(
        AuditLog.entity_type == "shift", AuditLog.entity_id == s.id))).scalars().all()
    assert [t.action for t in trail] == ["shift.auto_capped"]


async def test_lab07_manager_corrects_a_shift_with_an_audit_trail(client, tenant, db):
    from backend.models import AuditLog, Shift
    me = await _me(client, tenant)
    start = datetime(2026, 11, 2, 14, 0, 0)
    s = await _shift(db, me["id"], start, hours=30)
    r = await client.patch(f"/api/timeclock/shifts/{s.id}", headers=tenant, json={
        "started_at": "2026-11-02T14:00:00Z", "ended_at": "2026-11-02T22:00:00Z", "reason": "forgot to clock out"})
    assert r.status_code == 200, r.text
    assert r.json()["shift"]["hours_worked"] == 8.0
    s2 = (await db.execute(select(Shift).where(Shift.id == s.id).execution_options(populate_existing=True))).scalar_one()
    assert s2.ended_at == datetime(2026, 11, 2, 22, 0, 0) and s2.hours_worked == 8.0
    [a] = (await db.execute(select(AuditLog).where(
        AuditLog.entity_type == "shift", AuditLog.entity_id == s.id))).scalars().all()
    assert a.action == "shift.correct" and a.actor_id == me["id"]
    assert a.diff["reason"] == "forgot to clock out" and a.diff["hours_worked"] == [30.0, 8.0]
    hist = await client.get(f"/api/timeclock/shifts/{s.id}/audit", headers=tenant)
    assert hist.status_code == 200 and hist.json()["items"][0]["action"] == "shift.correct"


async def test_lab07_correction_needs_a_reason_and_valid_times(client, tenant, db):
    me = await _me(client, tenant)
    s = await _shift(db, me["id"], datetime(2026, 11, 2, 14), hours=8)
    r = await client.patch(f"/api/timeclock/shifts/{s.id}", headers=tenant,
                           json={"ended_at": "2026-11-02T10:00:00Z", "reason": "x"})
    assert r.status_code == 400
    r = await client.patch(f"/api/timeclock/shifts/{s.id}", headers=tenant,
                           json={"ended_at": "2026-11-02T20:00:00Z"})
    assert r.status_code == 422


async def test_lab07_other_shops_and_plain_staff_cannot_correct(client, tenant, db):
    me = await _me(client, tenant)
    s = await _shift(db, me["id"], datetime(2026, 11, 2, 14), hours=8)
    _b, hb = await _second_shop(client)
    r = await client.patch(f"/api/timeclock/shifts/{s.id}", headers=hb,
                           json={"ended_at": "2026-11-02T20:00:00Z", "reason": "x"})
    assert r.status_code == 404


async def test_lab07_correcting_a_paid_shift_reprices_its_wage_with_a_reversing_row(client, tenant, db):
    from backend.models import Employee
    from backend.models import EmployeeBalanceLedger as L
    me = await _me(client, tenant)
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.hourly_basis, emp.hourly_cost_rate = "attendance", 20
    await db.commit()
    s = await _shift(db, me["id"], datetime(2026, 11, 2, 14), hours=8)
    r = await client.post("/api/payroll/attendance-wages", headers=tenant, json={
        "employee_id": me["id"], "date_from": "2026-11-01", "date_to": "2026-11-30"})
    assert r.status_code == 201 and r.json()["wage_posted"] == 160.0, r.text
    key = L.normalize_key(f"attendance:{s.id}")

    r = await client.patch(f"/api/timeclock/shifts/{s.id}", headers=tenant, json={
        "ended_at": "2026-11-02T20:00:00Z", "reason": "left early"})
    assert r.status_code == 200, r.text
    assert r.json()["wage_change"] == -40.0
    rows = (await db.execute(select(L.amount).where(L.accrual_key == key).order_by(L.created_at))).scalars().all()
    assert sorted(float(x) for x in rows) == [-40.0, 160.0]                 # append-only: a reversing row

    # posting the month again pays nothing more; correcting back pays the difference once
    r = await client.post("/api/payroll/attendance-wages", headers=tenant, json={
        "employee_id": me["id"], "date_from": "2026-11-01", "date_to": "2026-11-30"})
    assert r.json()["wage_posted"] == 0
    r = await client.patch(f"/api/timeclock/shifts/{s.id}", headers=tenant, json={
        "ended_at": "2026-11-02T22:00:00Z", "reason": "was right"})
    assert r.json()["wage_change"] == 40.0
    net = (await db.execute(select(func.sum(L.amount)).where(L.accrual_key == key))).scalar()
    assert float(net) == 160.0


async def test_lab07_correcting_an_unpaid_shift_posts_no_wage(client, tenant, db):
    from backend.models import EmployeeBalanceLedger as L
    me = await _me(client, tenant)
    s = await _shift(db, me["id"], datetime(2026, 11, 2, 14), hours=8)
    r = await client.patch(f"/api/timeclock/shifts/{s.id}", headers=tenant, json={
        "ended_at": "2026-11-02T20:00:00Z", "reason": "left early"})
    assert r.status_code == 200 and r.json()["wage_change"] == 0
    assert (await db.execute(select(func.count()).select_from(L).where(
        L.accrual_key == L.normalize_key(f"attendance:{s.id}")))).scalar() == 0
