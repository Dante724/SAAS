"""Batch A: payroll double-pay, self-dealing, role templates, owner lock-out, coupons.

- Approving a wider timesheet window re-priced hours that were already paid.
- A timesheet spanning two months was counted in both months' runs.
- Pay runs for overlapping periods (1–15 and 1–31) both accrued.
- Anyone with payroll rights could approve, reward or pay themself; a run could be paid twice.
- The Specialist template (technicians) allowed payroll and money actions.
- The owner's explicit sign-up role would deny any permission added later.
- Any login could mint a client coupon of any size.
"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select


async def _register(client):
    email = f"pa-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    H = {"Authorization": f"Bearer {body['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, body["org_id"], me["id"]


async def _till(client, H):
    r = await client.post("/api/cash/accounts", json={"name": "Till"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _admin_role_id(db, org_id):
    from backend.models import Role
    return (await db.execute(select(Role.id).where(Role.org_id == org_id, Role.name == "admin"))).scalar_one()


async def _teammate(client, H, role_id):
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tech", "email": email, "password": "pw-test-12345", "role_id": role_id}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return r.json()["id"], {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _entry(db, org, emp, when: str, hours: float, rate: float = 20):
    from backend.models import TimeEntry
    start = datetime.fromisoformat(when)
    db.add(TimeEntry(org_id=org, employee_id=emp, started_at=start, ended_at=start,
                     duration_sec=int(hours * 3600), cost_rate=rate, state="stopped", source="manual"))
    await db.commit()


async def _hourly_total(db, org, emp):
    from backend.models import EmployeeBalanceLedger as L
    return float((await db.execute(select(func.coalesce(func.sum(L.amount), 0)).where(
        L.org_id == org, L.employee_id == emp, L.entry_type == "hourly_wage"))).scalar())


# ── double pay ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wider_timesheet_window_does_not_repay_hours(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _admin_role_id(db, org))
    await _entry(db, org, emp, "2026-08-03T09:00:00", 8)
    await _entry(db, org, emp, "2026-08-20T09:00:00", 8)

    r = await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-15"}, headers=H)
    assert r.status_code == 200, r.text
    assert await _hourly_total(db, org, emp) == 160
    r = await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}, headers=H)
    assert r.status_code == 200, r.text
    assert await _hourly_total(db, org, emp) == 320          # was 480: the 3rd was paid twice


@pytest.mark.asyncio
async def test_timesheet_across_months_counts_in_one_month_each(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _admin_role_id(db, org))
    await _entry(db, org, emp, "2026-07-31T09:00:00", 8)      # July
    await _entry(db, org, emp, "2026-08-01T09:00:00", 8)      # August
    await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": "2026-07-25", "date_to": "2026-08-05"}, headers=H)

    july = await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-07-01", "period_end": "2026-07-31"}, headers=H)
    aug = await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31"}, headers=H)
    assert july.status_code == 201 and aug.status_code == 201, (july.text, aug.text)
    assert july.json()["hourly_total"] == 160 and aug.json()["hourly_total"] == 160


@pytest.mark.asyncio
async def test_overlapping_runs_are_refused_and_paid_runs_stay_paid(client, db):
    H, org, _ = await _register(client)
    emp, _ = await _teammate(client, H, await _admin_role_id(db, org))
    first = await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-15", "base_salary": 500}, headers=H)
    assert first.status_code == 201, first.text
    again = await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31", "base_salary": 1000}, headers=H)
    assert again.status_code == 409

    run_id = first.json()["id"]
    pay = {"account_id": await _till(client, H)}
    assert (await client.post(f"/api/payroll/runs/{run_id}/pay", json=pay, headers=H)).status_code == 200
    assert (await client.post(f"/api/payroll/runs/{run_id}/pay", json=pay, headers=H)).status_code == 409


# ── separation of duties ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_nobody_approves_rewards_or_pays_themself(client, db):
    H, org, _ = await _register(client)
    emp, T = await _teammate(client, H, await _admin_role_id(db, org))
    await _entry(db, org, emp, "2026-08-03T09:00:00", 8)

    r = await client.post("/api/timeclock/timesheet/approve", json={
        "employee_id": emp, "date_from": "2026-08-01", "date_to": "2026-08-31"}, headers=T)
    assert r.status_code == 403
    r = await client.post("/api/payroll/adjustment", json={
        "employee_id": emp, "kind": "bonus", "amount": 500, "note": "me"}, headers=T)
    assert r.status_code == 403
    run = (await client.post("/api/payroll/run", json={
        "employee_id": emp, "period_start": "2026-08-01", "period_end": "2026-08-31"}, headers=H)).json()
    pay = {"account_id": await _till(client, H)}
    assert (await client.post(f"/api/payroll/runs/{run['id']}/pay", json=pay, headers=T)).status_code == 403
    # the owner can still do it for them
    assert (await client.post(f"/api/payroll/runs/{run['id']}/pay", json=pay, headers=H)).status_code == 200


# ── roles ─────────────────────────────────────────────────────────

def test_specialist_and_partner_templates_keep_money_and_payroll_off():
    from backend.services.permissions import new_role_permissions
    for template in ("Specialist", "Partner"):
        perms = new_role_permissions(template)
        for code in ("can_manage_payroll", "can_view_payroll", "can_view_finance",
                     "can_transfer_money", "can_export_clients", "can_delete_orders"):
            assert perms[code] is False, (template, code)


@pytest.mark.asyncio
async def test_owner_keeps_permissions_added_after_signup(client, db):
    from backend.models import Role
    H, org, _ = await _register(client)
    role_id = await _admin_role_id(db, org)
    _, T = await _teammate(client, H, role_id)
    # Simulate a code shipped after sign-up: the explicit role doesn't list it, so it's denied.
    role = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one()
    role.permissions = {k: v for k, v in (role.permissions or {}).items() if k != "can_manage_payroll"}
    await db.commit()

    assert (await client.get("/api/payroll/runs", headers=T)).status_code == 403
    assert (await client.get("/api/payroll/runs", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_client_coupons_need_discount_rights_and_sane_values(client):
    H, _, _ = await _register(client)
    cl = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    no_discount = (await client.post("/api/settings/rbac/roles", json={
        "name": "Desk", "permissions": {"can_set_discount": False}}, headers=H)).json()["id"]
    _, D = await _teammate(client, H, no_discount)

    assert (await client.post(f"/api/clients/{cl}/coupons", json={"value": 10}, headers=D)).status_code == 403
    assert (await client.post(f"/api/clients/{cl}/coupons", json={"value": 150}, headers=H)).status_code == 422
    assert (await client.post(f"/api/clients/{cl}/coupons", json={"value": 10, "max_uses": 100000}, headers=H)).status_code == 422
    assert (await client.post(f"/api/clients/{cl}/coupons", json={"value": 10}, headers=H)).status_code == 201
