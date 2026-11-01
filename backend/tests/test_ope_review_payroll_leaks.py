"""Integration review fixes for team E.

P1 — payroll scope also covers pay outside payroll.py: the staff list and staff create/update,
     timeclock (entries, timesheet, approvals, the order time rollup) and the labour reports.
P3 — /payroll/me needs can_view_own_compensation too; ope1 only grants can_view_own_payroll to
     roles that already had their own compensation.
P7 — nobody accrues their own pay through /run, /run-all or /calculation/accrue, and /run only
     takes this org's employees.

Every P1 case runs for an own-scope and a selected-scope payroll manager.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import Employee, TimeEntry

PAST = "2026-06"


async def _register(client):
    email = f"rv-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, me["org_id"], me["id"]


async def _role(client, H, name, template="Manager", **extra):
    r = await client.post("/api/settings/rbac/roles", json={"name": name, "template": template, **extra}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _person(client, H, name, role_id, wage=100):
    email = f"p-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": name, "email": email, "password": "pw-test-12345", "role_id": role_id,
        "wage_type": "hourly", "wage_value": wage, "tax_id": f"TAX-{name}"}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return r.json()["id"], {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _entry(db, org, emp, *, order_id=None, state="stopped", when="2026-06-03T09:00:00", rate=25.0):
    start = datetime.fromisoformat(when)
    e = TimeEntry(org_id=org, employee_id=emp, order_id=order_id, started_at=start,
                  ended_at=start + timedelta(hours=2), duration_sec=7200, cost_rate=rate,
                  billable_rate=60, billable=True, state=state, source="manual",
                  category="job" if order_id else "admin")
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e.id


async def _setup(client, db, scope):
    """Owner, a payroll manager with `scope`, a teammate inside the scope and one outside."""
    H, org, owner = await _register(client)
    staff = await _role(client, H, "Staff", template="Specialist")
    inside, _ = await _person(client, H, "Inside", staff["id"], wage=11)
    outside, _ = await _person(client, H, "Outside", staff["id"], wage=22)
    extra = {"payroll_scope": scope}
    if scope == "selected":
        extra["payroll_employee_ids"] = [inside]
    mgr_role = await _role(client, H, f"Mgr {scope}", **extra)
    mgr, M = await _person(client, H, "Mgr", mgr_role["id"], wage=33)
    visible = {inside} if scope == "selected" else set()
    return H, org, owner, inside, outside, mgr, M, visible


SCOPES = ["own", "selected"]


# ── P1: staff ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("scope", SCOPES)
async def test_staff_list_hides_pay_outside_scope(client, db, scope):
    H, org, owner, inside, outside, mgr, M, visible = await _setup(client, db, scope)
    rows = {r["id"]: r for r in (await client.get("/api/staff/employees", headers=M)).json()}
    assert rows[outside]["wage_value"] is None and rows[outside]["tax_id"] is None
    assert rows[mgr]["wage_value"] == 33                                       # their own
    assert (rows[inside]["wage_value"] == 11) == (inside in visible)
    # the owner still sees everyone
    rows = {r["id"]: r for r in (await client.get("/api/staff/employees", headers=H)).json()}
    assert rows[outside]["wage_value"] == 22


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", SCOPES)
async def test_staff_update_and_create_cant_write_pay_outside_scope(client, db, scope):
    H, org, owner, inside, outside, mgr, M, visible = await _setup(client, db, scope)
    for emp, before in ((outside, 22), (mgr, 33)):                             # outsider, and themself
        r = await client.patch(f"/api/staff/employees/{emp}", json={"wage_value": 999, "phone": "555"}, headers=M)
        assert r.status_code == 200, r.text
        db.expire_all()
        e = (await db.execute(select(Employee).where(Employee.id == emp))).scalar_one()
        assert e.wage_value == before and e.phone == "555"                      # the rest still saves
    if scope == "selected":
        assert (await client.patch(f"/api/staff/employees/{inside}", json={"wage_value": 12}, headers=M)).status_code == 200
        db.expire_all()
        assert (await db.execute(select(Employee.wage_value).where(Employee.id == inside))).scalar_one() == 12

    staff_role = next(r["id"] for r in (await client.get("/api/settings/rbac/roles", headers=H)).json()["items"]
                      if r["name"] == "Staff")
    r = await client.post("/api/staff/employees", json={
        "name": "New", "email": f"n-{uuid.uuid4().hex[:6]}@t.dev", "password": "pw-test-12345",
        "role_id": staff_role, "wage_type": "hourly", "wage_value": 500, "tax_id": "X"}, headers=M)
    assert r.status_code == 201, r.text
    db.expire_all()
    new = (await db.execute(select(Employee).where(Employee.id == r.json()["id"]))).scalar_one()
    assert not new.wage_value and not new.tax_id


# ── P1: timeclock ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("scope", SCOPES)
async def test_timeclock_manage_paths_respect_payroll_scope(client, db, scope):
    H, org, owner, inside, outside, mgr, M, visible = await _setup(client, db, scope)
    out_stopped = await _entry(db, org, outside)
    out_approved = await _entry(db, org, outside, state="approved", when="2026-06-04T09:00:00")
    out_open = await _entry(db, org, outside, when="2026-06-05T09:00:00")
    in_stopped = await _entry(db, org, inside)
    await _entry(db, org, mgr)

    listed = {e["employee_id"] for e in (await client.get("/api/timeclock/entries", headers=M)).json()["items"]}
    assert outside not in listed and mgr in listed
    assert (inside in listed) == (inside in visible)

    denied = [
        ("get", f"/api/timeclock/timesheet?employee_id={outside}&date_from=2026-06-01&date_to=2026-06-30", None),
        ("post", "/api/timeclock/timesheet/approve", {"employee_id": outside, "date_from": "2026-06-01", "date_to": "2026-06-30"}),
        ("post", f"/api/timeclock/entries/{out_stopped}/approve", None),
        ("post", f"/api/timeclock/entries/{out_approved}/reopen", None),
        ("post", f"/api/timeclock/entries/{out_open}/resolve-dispute", None),
        ("patch", f"/api/timeclock/entries/{out_open}", {"note": "x"}),
        ("delete", f"/api/timeclock/entries/{out_open}", None),
        ("get", f"/api/timeclock/entries/{out_open}/audit", None),
        ("post", "/api/timeclock/entries", {"employee_id": outside, "started_at": "2026-06-10T09:00:00", "duration_min": 30}),
    ]
    for method, url, body in denied:
        kw = {"headers": M}
        if body is not None:
            kw["json"] = body
        r = await getattr(client, method)(url, **kw)
        assert r.status_code == 403, (scope, method, url, r.status_code, r.text)
    db.expire_all()
    assert (await db.execute(select(TimeEntry.state).where(TimeEntry.id == out_stopped))).scalar_one() == "stopped"

    in_sheet = await client.get(f"/api/timeclock/timesheet?employee_id={inside}&date_from=2026-06-01&date_to=2026-06-30", headers=M)
    assert in_sheet.status_code == (200 if inside in visible else 403)
    in_approve = await client.post(f"/api/timeclock/entries/{in_stopped}/approve", headers=M)
    assert in_approve.status_code == (200 if inside in visible else 403)


@pytest.mark.asyncio
async def test_order_time_rollup_hides_other_peoples_rates(client, db):
    H, org, owner, inside, outside, mgr, M, visible = await _setup(client, db, "selected")
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=H)).json()
    await _entry(db, org, inside, order_id=o["id"])
    await _entry(db, org, outside, order_id=o["id"], when="2026-06-04T09:00:00")

    body = (await client.get(f"/api/timeclock/order/{o['id']}", headers=M)).json()
    rates = {e["employee_id"]: e["cost_rate"] for e in body["entries"]}
    assert rates[inside] == 25 and rates[outside] is None
    assert body["labor_cost"] is None                       # the total would give the hidden rate away
    tech_role = await _role(client, H, "Tech", template="Specialist")
    _, T = await _person(client, H, "Tess", tech_role["id"])
    body = (await client.get(f"/api/timeclock/order/{o['id']}", headers=T)).json()
    assert all(e["cost_rate"] is None for e in body["entries"]) and body["labor_cost"] is None
    body = (await client.get(f"/api/timeclock/order/{o['id']}", headers=H)).json()
    assert body["labor_cost"] == 100                         # owner: 2h × 25 × 2


# ── P1: labour reports ────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("scope", SCOPES)
async def test_labour_reports_are_payroll_reports(client, db, scope):
    H, org, owner, inside, outside, mgr, M, visible = await _setup(client, db, scope)
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=H)).json()
    await _entry(db, org, inside, order_id=o["id"])
    await _entry(db, org, outside, order_id=o["id"], when="2026-06-04T09:00:00")
    rng = "date_from=2026-06-01&date_to=2026-06-30"

    util = (await client.get(f"/api/reports/run/labor_utilization?{rng}", headers=M)).json()
    assert {r[0] for r in util["rows"]} == ({"Inside"} if inside in visible else set())
    by_job = (await client.get(f"/api/reports/run/labor_by_job?{rng}", headers=M)).json()
    assert [r[3] for r in by_job["rows"]] == ([50] if inside in visible else [])   # 2h × 25, Inside only
    for key in ("labor_utilization", "labor_by_job"):
        r = await client.get(f"/api/reports/export-csv/{key}?{rng}&employee_id={outside}", headers=M)
        assert r.status_code == 403

    no_pay = await _role(client, H, "Reports only", permissions={"can_view_payroll": False})
    _, N = await _person(client, H, "Rex", no_pay["id"])
    for key in ("labor_utilization", "labor_by_job"):
        assert (await client.get(f"/api/reports/run/{key}?{rng}", headers=N)).status_code == 403


# ── P3 ────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_my_earnings_needs_own_compensation_too(client, db):
    H, org, _ = await _register(client)
    role = await _role(client, H, "No own pay", template="Specialist",
                       permissions={"can_view_own_compensation": False})
    assert role["permissions"]["can_view_own_payroll"] is True
    _, T = await _person(client, H, "Ned", role["id"])
    r = await client.get("/api/payroll/me", headers=T)
    assert r.status_code == 403 and "can_view_own_compensation" in r.text


def test_migration_grants_own_payroll_only_with_own_compensation(tmp_path):
    import json
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    path = tmp_path / "mig.db"
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=f"sqlite+aiosqlite:///{path}")
    env.pop("TEST_DATABASE_URL", None)

    def alembic(target):
        r = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
                           cwd=repo, env=env, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]

    alembic("ls144r4")
    con = sqlite3.connect(path)
    for rid, perms in (("yes", {"can_view_own_compensation": True}),
                       ("no", {"can_view_own_compensation": False}),
                       ("unset", {"can_create_tickets": True})):
        con.execute("INSERT INTO roles (id, org_id, name, permissions, perms_explicit, location_scope, contact_scope, "
                    "order_scope, warehouse_scope) VALUES (?, 'o1', ?, ?, 1, 'all', 'all', 'all', 'all')",
                    (rid, rid, json.dumps(perms)))
    con.commit()
    con.close()
    alembic("ope1")
    con = sqlite3.connect(path)
    got = {rid: json.loads(p) for rid, p in con.execute("SELECT id, permissions FROM roles")}
    con.close()
    assert got["yes"].get("can_view_own_payroll") is True
    assert "can_view_own_payroll" not in got["no"]
    assert "can_view_own_payroll" not in got["unset"]         # explicit role without it: still off


# ── P7 ────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_nobody_accrues_their_own_pay(client, db):
    H, org, owner = await _register(client)
    staff = await _role(client, H, "Staff", template="Specialist")
    other, _ = await _person(client, H, "Other", staff["id"])
    mgr_role = await _role(client, H, "Mgr own", payroll_scope="own")
    mgr, M = await _person(client, H, "Mgr", mgr_role["id"])

    r = await client.post("/api/payroll/run", json={
        "employee_id": mgr, "period_start": "2026-05-01", "period_end": "2026-05-31", "base_salary": 99999}, headers=M)
    assert r.status_code == 403
    r = await client.post("/api/payroll/calculation/accrue", json={"employee_id": mgr, "month": PAST}, headers=M)
    assert r.status_code == 403
    r = await client.post("/api/payroll/run-all", json={"period_start": "2026-05-01", "period_end": "2026-05-31"}, headers=M)
    assert r.status_code == 201 and r.json()["accrued"] == 0
    assert r.json()["results"][0]["status"] == "skipped"
    assert (await client.get("/api/payroll/runs", headers=H)).json()["items"] == []

    # the owner can still do it for them, and for anyone
    assert (await client.post("/api/payroll/calculation/accrue", json={"employee_id": mgr, "month": PAST}, headers=H)).status_code == 201
    assert (await client.post("/api/payroll/run", json={
        "employee_id": other, "period_start": "2026-04-01", "period_end": "2026-04-30"}, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_run_refuses_another_orgs_employee(client, db):
    H, org, _ = await _register(client)
    _, _, foreign = await _register(client)
    r = await client.post("/api/payroll/run", json={
        "employee_id": foreign, "period_start": "2026-05-01", "period_end": "2026-05-31", "base_salary": 10}, headers=H)
    # refused up front, not by a database integrity error after the run was half written
    assert r.status_code == 404 and r.json()["detail"] == "Employee not found"


@pytest.mark.asyncio
async def test_payroll_scope_only_matters_for_roles_with_payroll_rights(client, db):
    """A payroll-scoped manager can still hire into, and create, roles that see no payroll; giving
    out a role that does see payroll is still capped at their own scope."""
    H, org, owner, inside, outside, mgr, _, visible = await _setup(client, db, "own")
    editor = await _role(client, H, "Role editor own", template="Full access", payroll_scope="own")
    _, M = await _person(client, H, "Edna", editor["id"])
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Front desk", "template": "Specialist"}, headers=M)
    assert r.status_code == 201, r.text
    desk = r.json()["id"]
    r = await client.post("/api/staff/employees", json={
        "name": "Hire", "email": f"h-{uuid.uuid4().hex[:6]}@t.dev", "password": "pw-test-12345", "role_id": desk}, headers=M)
    assert r.status_code == 201, r.text
    r = await client.post("/api/settings/rbac/roles", json={"name": "Payroll all", "template": "Manager"}, headers=M)
    assert r.status_code == 403 and "payroll scope" in r.text
    r = await client.post("/api/settings/rbac/roles", json={"name": "Payroll own", "template": "Manager",
                                                             "payroll_scope": "own"}, headers=M)
    assert r.status_code == 201, r.text
