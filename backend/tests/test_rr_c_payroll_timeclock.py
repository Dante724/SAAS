"""Re-review fixes (13 Nov 2026), group C: payroll, time clock, media performance.

- H2 / N-1: a "Tracked labor" line made by /timeclock/order/{id}/bill could not be deleted (500: the
  time entries' order_line_id FK), and the time could never be billed again.
- H3 / N-2: reopening an entry paid by a pre-2026-10-19 window row (`<emp>:<from>:<to>`) reversed
  nothing, so re-approval paid it twice.
- N-3: void/edit checked the entry's state before taking the employee lock (race with approve).
- N-4: a first payment dated into an already accrued month was never picked up by any run.
- N-5: a legacy reopen left the other legacy entries of the day at their old overtime.
- N-6 / N-7: thumbnails, base64 decoding, boto3 client creation and local file I/O ran on the loop.
- N-8: media stayed in browser caches for an hour after erasure.
- N-9: audit retention deleted the rows the LAB-02 legacy check reads.
- N-10: the timer paths never ran the overlap check.
- N-11: labour-cost report zone, `wage_reversed`, and `_post` rolling back the caller's session.
- LAB-08: labour billing rounded hours to 0.01 h before pricing, and swallowed failures silently.
"""
import base64
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

from backend.tests.test_p0_payroll_timeclock import (
    _approve_window,
    _entry,
    _job_with_time,
    _lines,
    _register,
    _role,
    _teammate,
    _wage_rows,
    _wages,
)

pytestmark = pytest.mark.asyncio


# ── H2 ──────────────────────────────────────────────────────────────────────────────────────────
async def test_h2_billed_tracked_labour_line_can_be_deleted_and_billed_again(client, tenant, db):
    oid, _ = await _job_with_time(client, tenant, db)
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=tenant)
    assert r.status_code == 200, r.text
    lid = r.json()["line_id"]
    r = await client.delete(f"/api/orders/{oid}/lines/{lid}", headers=tenant)
    assert r.status_code == 200, r.text                          # was 500 (FK)
    assert not await _lines(db, oid)
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=tenant)
    assert r.status_code == 200, r.text                          # was 409 "already billed"
    assert len(await _lines(db, oid)) == 1


async def test_h2_kit_holding_billed_time_can_be_deleted(client, tenant, db):
    from backend.models import OrderLine
    oid, _ = await _job_with_time(client, tenant, db)
    lid = (await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=tenant)).json()["line_id"]
    (await db.execute(select(OrderLine).where(OrderLine.id == lid))).scalar_one().bundle_group_id = "kit-x"
    await db.commit()
    r = await client.delete(f"/api/orders/{oid}/kits/kit-x", headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=tenant)).status_code == 200


# ── H3 / N-5 ────────────────────────────────────────────────────────────────────────────────────
def _window_row(org, emp, amount, d0, d1):
    """What approve_timesheet posted before af1aaaff (2026-10-19): one row per window."""
    from backend.models import EmployeeBalanceLedger as L
    return L(org_id=org, employee_id=emp, entry_type="hourly_wage", amount=amount, ref_type="timesheet",
             ref_id=f"{emp}:{d0}:{d1}", idempotency_key=f"hourly:{emp}:{d0}:{d1}")


async def test_h3_reopen_of_entry_paid_by_a_window_row_pays_once(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-03T09:00:00", 8, state="approved")
    db.add(_window_row(org, emp, 160, "2026-08-01", "2026-08-31"))
    await db.commit()
    r = await client.post(f"/api/timeclock/entries/{eid}/reopen", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["wage_reversed"] == 160                      # was 0
    assert await _wages(db, org, emp) == 0
    assert (await _approve_window(client, H, emp, "2026-08-01", "2026-08-31")).status_code == 200
    assert await _wages(db, org, emp) == 160                     # was 320


async def test_h3_window_row_reversal_leaves_other_days_paid_and_never_overdraws(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    a = await _entry(db, org, emp, "2026-08-03T09:00:00", 8, state="approved")
    b = await _entry(db, org, emp, "2026-08-04T09:00:00", 8, state="approved")
    db.add(_window_row(org, emp, 200, "2026-08-01", "2026-08-31"))    # under-paid window (old rate rule)
    await db.commit()
    rows_before = len(await _wage_rows(db, org, emp))
    assert (await client.post(f"/api/timeclock/entries/{a}/reopen", headers=H)).status_code == 200
    assert await _wages(db, org, emp) == 40                      # b's day stays paid (what's left of it)
    assert (await client.post(f"/api/timeclock/entries/{b}/reopen", headers=H)).status_code == 200
    assert await _wages(db, org, emp) == 0                       # capped: never below what was paid
    # append-only: the window row is untouched, reversals are new rows
    rows = await _wage_rows(db, org, emp)
    assert len(rows) == rows_before + 2
    assert any(float(x.amount) == 200 and x.accrual_key is None for x in rows)
    assert (await _approve_window(client, H, emp, "2026-08-01", "2026-08-31")).status_code == 200
    assert await _wages(db, org, emp) == 320


async def test_h3_window_row_does_not_cover_entries_outside_it(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-09-03T09:00:00", 8, state="approved")
    db.add(_window_row(org, emp, 160, "2026-08-01", "2026-08-31"))
    await db.commit()
    r = await client.post(f"/api/timeclock/entries/{eid}/reopen", headers=H)
    assert r.status_code == 200 and r.json()["wage_reversed"] == 0, r.text
    assert await _wages(db, org, emp) == 160


async def test_n5_legacy_reopen_reprices_the_other_legacy_entries_overtime(client, db):
    """6h + 4h day paid 220 by a legacy row (the 4h entry ran 2h into overtime). Reopening the 6h
    entry leaves the 4h entry at 80, not 100; approving it again restores the day to 220."""
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    a = await _entry(db, org, emp, "2026-08-07T06:00:00", 6, state="approved")
    await _entry(db, org, emp, "2026-08-07T13:00:00", 4, state="approved")
    db.add(_window_row(org, emp, 220, "2026-08-07", "2026-08-07"))
    await db.commit()
    r = await client.post(f"/api/timeclock/entries/{a}/reopen", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["wage_reversed"] == 120
    assert await _wages(db, org, emp) == 80                      # was 100 (overtime kept)
    assert (await client.post(f"/api/timeclock/entries/{a}/approve", headers=H)).status_code == 200
    assert await _wages(db, org, emp) == 220


# ── N-11: wage_reversed ─────────────────────────────────────────────────────────────────────────
async def test_n11_wage_reversed_is_this_entrys_wage_not_the_days_net(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    a = await _entry(db, org, emp, "2026-08-07T06:00:00", 6)
    await _entry(db, org, emp, "2026-08-07T13:00:00", 4)
    assert (await _approve_window(client, H, emp, "2026-08-07", "2026-08-07")).status_code == 200
    r = await client.post(f"/api/timeclock/entries/{a}/reopen", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["wage_reversed"] == 120                      # was 140 (incl. the other entry's OT)
    assert r.json()["day_repriced"] == -20


# ── N-3 ─────────────────────────────────────────────────────────────────────────────────────────
async def _approved_meanwhile(monkeypatch, db, eid):
    """While the handler waits for the employee lock, another request approves the entry."""
    from backend.models import TimeEntry
    from backend.routers import timeclock
    real = timeclock._lock_employee

    async def lock(session, employee_id):
        await session.execute(update(TimeEntry).where(TimeEntry.id == eid).values(state="approved")
                              .execution_options(synchronize_session=False))
        await real(session, employee_id)
    monkeypatch.setattr(timeclock, "_lock_employee", lock)


async def test_n3_void_rechecks_state_after_the_lock(client, db, monkeypatch):
    from backend.models import TimeEntry
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-04T09:00:00", 8)
    await _approved_meanwhile(monkeypatch, db, eid)
    r = await client.delete(f"/api/timeclock/entries/{eid}", headers=H)
    assert r.status_code == 409, r.text                          # was 200: paid and voided
    db.expire_all()
    # (the simulated approval was inside the refused request, so it rolled back with it)
    assert (await db.execute(select(TimeEntry.state).where(TimeEntry.id == eid))).scalar() != "void"


async def test_n3_edit_rechecks_state_after_the_lock(client, db, monkeypatch):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-04T09:00:00", 8)
    await _approved_meanwhile(monkeypatch, db, eid)
    r = await client.patch(f"/api/timeclock/entries/{eid}", json={"note": "x"}, headers=H)
    assert r.status_code == 409, r.text


async def test_n3_a_voided_entry_that_was_paid_is_reversed_on_the_next_sync(client, db):
    from backend.models import TimeEntry
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    a = await _entry(db, org, emp, "2026-08-04T09:00:00", 4)
    await _entry(db, org, emp, "2026-08-04T14:00:00", 2)
    assert (await client.post(f"/api/timeclock/entries/{a}/approve", headers=H)).status_code == 200
    assert await _wages(db, org, emp) == 80
    # the lost race: the paid entry ended up void
    await db.execute(update(TimeEntry).where(TimeEntry.id == a).values(state="void"))
    await db.commit()
    assert (await _approve_window(client, H, emp, "2026-08-04", "2026-08-04")).status_code == 200
    assert await _wages(db, org, emp) == 40                      # only the 2h entry


# ── N-4 ─────────────────────────────────────────────────────────────────────────────────────────
async def _accrue(client, H, emp, d0, d1, draft=False):
    r = await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": d0, "period_end": d1, "draft": draft}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _this_month(db, emp_id):
    """This month on the employee's shop clock, the same "today" the wage code dates with
    (today_in(shop tz)). Using UTC made the test fail every evening on a shop west of UTC."""
    from sqlalchemy import select as _select

    from backend.models import Employee
    from backend.services.dates import shop_timezone, today_in
    emp = (await db.execute(_select(Employee).where(Employee.id == emp_id))).scalar_one()
    t = today_in(await shop_timezone(db, emp))
    nxt = (t.replace(day=28) + timedelta(days=4)).replace(day=1)
    return t, t.replace(day=1).isoformat(), (nxt - timedelta(days=1)).isoformat()


async def test_n4_late_approval_into_an_accrued_month_lands_in_the_next_open_run(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    await _accrue(client, H, emp, "2026-08-01", "2026-08-31")
    eid = await _entry(db, org, emp, "2026-08-28T09:00:00", 8)
    assert (await client.post(f"/api/timeclock/entries/{eid}/approve", headers=H)).status_code == 200
    today, m0, m1 = await _this_month(db, emp)
    rows = [x for x in await _wage_rows(db, org, emp) if float(x.amount) == 160]
    assert rows and rows[0].ref_id.endswith(f"{today}:{today}"), rows[0].ref_id   # was dated 2026-08-28
    assert (await _accrue(client, H, emp, m0, m1, draft=True))["hourly_total"] == 160


async def test_n4_first_payment_into_an_open_month_keeps_the_work_day(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    eid = await _entry(db, org, emp, "2026-08-28T09:00:00", 8)
    assert (await client.post(f"/api/timeclock/entries/{eid}/approve", headers=H)).status_code == 200
    assert (await _accrue(client, H, emp, "2026-08-01", "2026-08-31"))["hourly_total"] == 160


async def test_n4_attendance_shift_paid_after_its_month_was_accrued_lands_in_the_next_run(client, db):
    from backend.tests.test_p0_payroll_timeclock import _attendance_emp, _shift
    H, org, _ = await _register(client)
    emp = await _attendance_emp(client, H, db, org)
    await _accrue(client, H, emp, "2026-08-01", "2026-08-31")
    await _shift(db, org, emp, "2026-08-20T09:00:00", 4)
    r = await client.post("/api/payroll/attendance-wages", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}, headers=H)
    assert r.status_code == 201 and r.json()["wage_posted"] == 80, r.text
    _, m0, m1 = await _this_month(db, emp)
    assert (await _accrue(client, H, emp, m0, m1, draft=True))["hourly_total"] == 80


# ── N-9 ─────────────────────────────────────────────────────────────────────────────────────────
async def test_n9_audit_retention_keeps_the_attendance_wage_evidence(db, client):
    from backend.models import AuditLog, Organisation
    from backend.services import retention
    H, org, owner = await _register(client)
    old = datetime(2025, 1, 1, 12)
    db.add(AuditLog(org_id=org, actor_id=owner, action="payroll.attendance_wages", entity_type="employee",
                    entity_id=owner, diff={"window": ["2024-12-01", "2024-12-31"]}, created_at=old))
    db.add(AuditLog(org_id=org, actor_id=owner, action="order.update", entity_type="order",
                    entity_id="x", created_at=old))
    o = (await db.execute(select(Organisation).where(Organisation.id == org))).scalar_one()
    o.retention_days = {"audit": 90}
    await db.commit()
    await retention.purge_org(db, retention._Org(org, {"audit": 90}))
    db.expire_all()
    left = (await db.execute(select(AuditLog.action).where(
        AuditLog.org_id == org, AuditLog.created_at == old))).scalars().all()
    assert left == ["payroll.attendance_wages"]


# ── N-10 ────────────────────────────────────────────────────────────────────────────────────────
async def test_n10_starting_a_timer_inside_an_entry_is_refused(client, tenant, db):
    from backend.services.dates import now_utc
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    now = now_utc()
    await _entry(db, me["org_id"], me["id"], (now - timedelta(hours=1)).isoformat(), 2)
    r = await client.post("/api/timeclock/start", json={"category": "admin"}, headers=tenant)
    assert r.status_code == 409, r.text


async def test_n10_stopping_a_timer_stops_it_where_a_later_entry_begins(client, tenant, db):
    from backend.models import TimeEntry
    from backend.services.dates import now_utc
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    now = now_utc().replace(microsecond=0)
    r = await client.post("/api/timeclock/start", json={"category": "admin"}, headers=tenant)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    (await db.execute(select(TimeEntry).where(TimeEntry.id == tid))).scalar_one().started_at = now - timedelta(hours=3)
    await db.commit()
    # logged while the timer ran, for a time that was still ahead of it then
    await _entry(db, me["org_id"], me["id"], (now - timedelta(hours=2)).isoformat(), 1)
    r = await client.post("/api/timeclock/stop", headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    t = (await db.execute(select(TimeEntry).where(TimeEntry.id == tid))).scalar_one()
    assert t.ended_at == now - timedelta(hours=2) and t.duration_sec == 3600   # was 3h, overlapping 1h
    assert t.needs_review is True


# ── N-11: report zone, _post savepoint ──────────────────────────────────────────────────────────
async def test_n11_labor_cost_overtime_uses_each_employees_branch_zone(client, db):
    from backend.models import Employee, Location
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _role(db, org))
    e = (await db.execute(select(Employee).where(Employee.id == emp))).scalar_one()
    loc = (await db.execute(select(Location).where(Location.org_id == org))).scalars().first()
    loc.timezone = "America/New_York"
    e.location_id = loc.id
    await db.commit()
    # NY: 3 Aug 21:00-03:00 (6h) and 4 Aug 06:00-10:00 (4h) are two days; in UTC both are 4 Aug.
    await _entry(db, org, emp, "2026-08-04T01:00:00", 6)
    await _entry(db, org, emp, "2026-08-04T10:00:00", 4)
    rep = await client.get("/api/reports/run/labor_cost?date_from=2026-08-01&date_to=2026-08-31", headers=H)
    assert rep.status_code == 200, rep.text
    rows = dict((r[0], r[1]) for r in rep.json()["rows"])
    assert rows["Overtime hours (>8h/day)"] == 0                 # was 2.0 (the shop's UTC day)


async def test_n11_post_conflict_does_not_roll_back_the_callers_session(client, db, monkeypatch):
    from backend.models import Employee
    from backend.models import EmployeeBalanceLedger as L
    from backend.services.payroll import CommissionEngine
    H, org, owner = await _register(client)
    db.add(L(org_id=org, employee_id=owner, entry_type="bonus", amount=5, ref_type="manual",
             ref_id="x", idempotency_key="dup-key"))
    await db.commit()
    me = (await db.execute(select(Employee).where(Employee.id == owner))).scalar_one()
    me.name = "Renamed in this transaction"
    await db.flush()

    real, calls = db.execute, []

    class _Miss:
        def scalar_one_or_none(self):
            return None

    async def execute(stmt, *a, **k):       # the duplicate check loses a race with another request
        if not calls:
            calls.append(1)
            return _Miss()
        return await real(stmt, *a, **k)
    monkeypatch.setattr(db, "execute", execute)
    ok = await CommissionEngine(db, org)._post(
        employee_id=owner, entry_type="bonus", amount=5, event=None, rule_id=None, ref_type="manual",
        ref_id="x", idempotency_key="dup-key")
    monkeypatch.setattr(db, "execute", real)
    assert ok is False
    await db.commit()
    db.expire_all()
    assert (await db.execute(select(Employee.name).where(Employee.id == owner))).scalar() == \
        "Renamed in this transaction"                            # was rolled back with the conflict


# ── LAB-08 ──────────────────────────────────────────────────────────────────────────────────────
async def _labour_billing_on(db, org):
    from backend.models import Organisation
    (await db.execute(select(Organisation).where(Organisation.id == org))).scalar_one().clock_labour_billing = True
    await db.commit()


async def test_lab08_labour_billing_prices_seconds_like_payroll(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await _labour_billing_on(db, me["org_id"])
    cid = (await client.post("/api/clients", json={"name": "T"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    from backend.models import Employee
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.billable_rate = 37
    await db.commit()
    r = await client.post("/api/timeclock/entries", headers=tenant, json={
        "order_id": oid, "started_at": "2026-08-04T09:00:00", "ended_at": "2026-08-04T09:16:41"})  # 1001 s
    assert r.status_code == 201, r.text
    lines = [x for x in await _lines(db, oid) if x.line_type == "labour"]
    assert len(lines) == 1
    assert float(lines[0].total) == 10.29                       # was 10.36 (0.28 h × 37)


async def test_lab08_a_labour_billing_failure_is_logged_and_reported(client, tenant, db, monkeypatch, caplog):
    from backend.routers import timeclock
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    cid = (await client.post("/api/clients", json={"name": "T"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    assert me

    async def boom(*a, **k):
        raise RuntimeError("billing broke")
    monkeypatch.setattr(timeclock, "_clock_labour_billing_on", boom)
    with caplog.at_level("ERROR"):
        r = await client.post("/api/timeclock/entries", headers=tenant, json={
            "order_id": oid, "started_at": "2026-08-04T09:00:00", "ended_at": "2026-08-04T10:00:00"})
    assert r.status_code == 201, r.text
    assert r.json().get("labour_billing_warning")              # was silent
    assert any("labour billing" in rec.getMessage().lower() for rec in caplog.records)


# ── N-7 / N-8: media off the loop, short cache ──────────────────────────────────────────────────
RAW = b"\x89PNG\r\n\x1a\n" + b"B" * 50_000
PNG = "data:image/png;base64," + base64.b64encode(RAW).decode()


def _record(monkeypatch, mod, name, seen):
    real = getattr(mod, name)

    def wrapped(*a, **k):
        seen.append((name, threading.get_ident()))
        return real(*a, **k)
    monkeypatch.setattr(mod, name, wrapped)


async def test_n7_local_media_io_and_decoding_run_off_the_event_loop(client, tenant, db, monkeypatch, tmp_path):
    from backend.config import settings
    from backend.models import MediaBlob
    from backend.services import media
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    seen: list = []
    for name in ("_decode_payload", "_write_file", "_read_file", "_remove_file"):
        _record(monkeypatch, media, name, seen)
    loop = threading.get_ident()
    r = await client.post("/api/media", headers=tenant, json={"data": PNG, "name": "p.png"})
    assert r.status_code == 201, r.text
    m = await client.get(r.json()["url"], headers=tenant)
    assert m.status_code == 200 and m.content == RAW
    names = {n for n, _ in seen}
    assert {"_decode_payload", "_write_file", "_read_file"} <= names, names
    assert all(t != loop for _, t in seen), seen
    seen.clear()
    blob = (await db.execute(select(MediaBlob).where(MediaBlob.id == r.json()["id"]))).scalar_one()
    await media.erase(db, [blob])
    await db.commit()
    assert [n for n, _ in seen] == ["_remove_file"] and seen[0][1] != loop, seen


async def test_n7_boto3_client_is_built_off_the_event_loop(client, tenant, monkeypatch):
    import sys
    import types

    from backend.config import settings
    from backend.services import media
    from backend.tests.test_p1_media_track import _FakeS3
    fake, made = _FakeS3(), []
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(
        client=lambda *a, **k: made.append(threading.get_ident()) or fake))
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "s3")
    monkeypatch.setattr(settings, "MEDIA_S3_BUCKET", "bucket")
    media._s3_client.cache_clear()
    try:
        r = await client.post("/api/media", headers=tenant, json={"data": PNG, "name": "p.png"})
        assert r.status_code == 201, r.text
        assert made and threading.get_ident() not in made     # was built on the loop thread
    finally:
        media._s3_client.cache_clear()


async def test_n8_media_is_only_briefly_cacheable(client, tenant, monkeypatch, tmp_path):
    import re

    from backend.config import settings
    monkeypatch.setattr(settings, "MEDIA_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_LOCAL_DIR", str(tmp_path))
    r = await client.post("/api/media", headers=tenant, json={"data": PNG, "name": "p.png"})
    cc = (await client.get(r.json()["url"], headers=tenant)).headers["cache-control"]
    assert "private" in cc and "public" not in cc
    assert int(re.search(r"max-age=(\d+)", cc).group(1)) <= 60, cc     # was 3600


# ── SEC-02 (re-review): payroll void and cashbox scope ──────────────────────────────────────────
async def test_payroll_void_refuses_a_cashbox_the_role_may_not_use(client, db):
    from sqlalchemy import func

    from backend.models import EmployeeBalanceLedger, PayrollRun
    from backend.tests.test_batchC_payroll_money import _account_balance, _employee, _till
    from backend.tests.test_p1_money_audit import _staff
    H, org, _ = await _register(client)
    emp = await _employee(client, H)
    till = await _till(client, H)
    safe = (await client.post("/api/cash/accounts", json={"name": "Back safe", "payment_option": "cash"},
                              headers=H)).json()["id"]
    run = (await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31",
        "base_salary": 1000}, headers=H)).json()
    assert (await client.post(f"/api/payroll/runs/{run['id']}/pay", json={"account_id": till},
                              headers=H)).status_code == 200
    M = await _staff(client, H, account_ids=[safe])          # payroll rights, but only the safe
    r = await client.post(f"/api/payroll/runs/{run['id']}/void", json={"reason": "wrong"}, headers=M)
    assert r.status_code == 403, r.text                       # was 200: moved money in the till
    assert "Till" in r.json()["detail"]
    assert await _account_balance(client, H, till) == -1000
    db.expire_all()
    assert (await db.execute(select(PayrollRun.status).where(PayrollRun.id == run["id"]))).scalar() == "paid"
    assert (await db.execute(select(func.count(EmployeeBalanceLedger.id)).where(
        EmployeeBalanceLedger.ref_type == "payroll_run_void",
        EmployeeBalanceLedger.ref_id == run["id"]))).scalar() == 0
    # a role that may use the till voids it
    r = await client.post(f"/api/payroll/runs/{run['id']}/void", json={"reason": "wrong"}, headers=H)
    assert r.status_code == 200, r.text
