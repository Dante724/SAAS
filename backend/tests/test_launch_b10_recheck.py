"""Launch B10/B11 recheck: the side doors the second review still walked through.

* A zero-permission Viewer billed their own tracked time onto a job at a rate of -500
  (POST /api/timeclock/order/{id}/bill had no permission or rate check).
* A teammate with no role was still the owner for timesheets and payroll statements.
* "Finish & take" offered the old amount while a running timer billed more on close."""
import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.models import Employee, Invite, Organisation, Role, TimeEntry
from backend.services.auth import hash_password
from backend.services.dates import now_utc


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _login(client, email, pw="pw-staff-12345"):
    r = await client.post("/api/auth/token", data={"username": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _staff(client, tenant, db, name, perms, rate=100):
    me = await _me(client, tenant)
    role = Role(org_id=me["org_id"], name=name, permissions=perms, perms_explicit=True)
    db.add(role)
    await db.flush()
    email = f"{name.lower()}@b10r.dev"
    db.add(Employee(org_id=me["org_id"], location_id=me["location_id"], role_id=role.id, name=name,
                    email=email, password_hash=hash_password("pw-staff-12345"), active=True,
                    billable_rate=rate))
    await db.commit()
    return await _login(client, email)


async def _order(client, tenant, price=80):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    oid = (await client.post("/api/orders", json={"location_id": loc}, headers=tenant)).json()["id"]
    if price:
        r = await client.post(f"/api/orders/{oid}/lines", json={"kind": "service", "description": "Fix",
                                                                "qty": 1, "unit_price": price}, headers=tenant)
        assert r.status_code in (200, 201), r.text
    return oid


async def _log_hour(client, h, oid):
    r = await client.post("/api/timeclock/entries", json={
        "order_id": oid, "category": "job", "billable": True,
        "started_at": "2026-08-01T09:00:00", "duration_min": 60}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_viewer_cannot_bill_tracked_time_onto_a_job(client, tenant, db):
    vh = await _staff(client, tenant, db, "Viewer", {})
    oid = await _order(client, tenant)
    await _log_hour(client, vh, oid)               # logging your own time is still fine

    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={"rate": -500}, headers=vh)
    assert r.status_code in (403, 422), r.text
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=vh)
    assert r.status_code == 403, r.text
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["total"] == 80 and len(o["lines"]) == 1


@pytest.mark.asyncio
async def test_billing_tracked_time_takes_the_line_rights(client, tenant, db):
    # can add services, can't change prices: bills at the tracked rate, not one of their own
    th = await _staff(client, tenant, db, "Tech", {"can_add_services": True, "can_edit_sale_price": False})
    oid = await _order(client, tenant)
    await _log_hour(client, th, oid)

    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={"rate": -500}, headers=th)
    assert r.status_code == 422, r.text            # never a negative rate, whoever asks
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={"rate": 5}, headers=th)
    assert r.status_code == 403, r.text            # a rate of your own is a price edit
    r = await client.post(f"/api/timeclock/order/{oid}/bill", json={}, headers=th)
    assert r.status_code == 200, r.text
    assert r.json()["amount"] == 100
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["total"] == 180                       # totals come from the lines
    assert sum(ln["total"] for ln in o["lines"]) == 180


@pytest.mark.asyncio
async def test_teammate_with_no_role_is_not_the_owner_for_time_and_pay(client, tenant, db):
    owner = await _me(client, tenant)
    r = await client.post("/api/staff/invites", json={"email": "norole@b10r.dev"}, headers=tenant)
    assert r.status_code == 201, r.text
    inv = (await db.execute(select(Invite).where(Invite.email == "norole@b10r.dev"))).scalar_one()
    inv.token_hash = hashlib.sha256(b"b10r-token").hexdigest()
    await db.commit()
    r = await client.post("/api/auth/invite/accept", json={"token": "b10r-token", "name": "No Role",
                                                           "password": "pw-norole-12345"})
    assert r.status_code == 200, r.text
    nh = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # the owner's own entry
    r = await client.post("/api/timeclock/entries", json={
        "category": "admin", "started_at": "2026-08-02T09:00:00", "duration_min": 30}, headers=tenant)
    assert r.status_code == 201, r.text
    owner_eid = r.json()["id"]

    r = await client.post("/api/timeclock/entries", json={
        "employee_id": owner["id"], "category": "admin",
        "started_at": "2026-08-03T09:00:00", "duration_min": 600}, headers=nh)
    assert r.status_code == 403, r.text
    assert (await client.patch(f"/api/timeclock/entries/{owner_eid}", json={"note": "x"},
                               headers=nh)).status_code == 403
    assert (await client.delete(f"/api/timeclock/entries/{owner_eid}", headers=nh)).status_code == 403
    items = (await client.get("/api/timeclock/entries", headers=nh)).json()["items"]
    assert all(e["employee_id"] != owner["id"] for e in items)
    assert (await client.get("/api/timeclock/roster", headers=nh)).status_code == 403
    assert (await client.get(f"/api/print/payroll-statement/{owner['id']}", headers=nh)).status_code == 403

    # their own time still works, and the owner keeps full access
    r = await client.post("/api/timeclock/entries", json={
        "category": "admin", "started_at": "2026-08-04T09:00:00", "duration_min": 30}, headers=nh)
    assert r.status_code == 201, r.text
    assert (await client.get("/api/timeclock/roster", headers=tenant)).status_code == 200
    assert (await client.patch(f"/api/timeclock/entries/{owner_eid}", json={"note": "ok"},
                               headers=tenant)).status_code == 200


async def _cash_account(client, H):
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    return [a for a in (await client.get("/api/cash/accounts", headers=H)).json()["items"]
            if a["name"] == "Till"][0]["id"]


async def _running_hour(client, tenant, db, oid):
    me = await _me(client, tenant)
    org = (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()
    org.clock_labour_billing = True
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.billable_rate = 100
    await db.commit()
    r = await client.post("/api/timeclock/start", json={"order_id": oid}, headers=tenant)
    assert r.status_code == 201, r.text
    e = (await db.execute(select(TimeEntry).where(TimeEntry.order_id == oid))).scalar_one()
    e.started_at = now_utc() - timedelta(hours=1)
    await db.commit()


@pytest.mark.asyncio
async def test_finish_and_take_sees_the_running_timer(client, tenant, db):
    oid = await _order(client, tenant, price=178)
    await _running_hour(client, tenant, db, oid)
    aid = await _cash_account(client, tenant)

    chk = (await client.post(f"/api/orders/{oid}/status/check?assume_paid=true",
                             json={"status": "done"}, headers=tenant)).json()
    assert chk["ok"] is False and chk["code"] == "timers_running", chk
    assert chk["owed"] == 278
    assert (await client.get("/api/timeclock/active", headers=tenant)).json()["active"]   # dry run only

    r = await client.post(f"/api/orders/{oid}/finish", json={
        "payment": {"method": "cash", "account_id": aid, "amount": 178}}, headers=tenant)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "timers_running", r.text
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["status"] == "new" and o["total"] == 178          # nothing taken, nothing billed
    assert (await client.get(f"/api/cash/orders/{oid}/payment-context", headers=tenant)).json()["remaining"] == 178

    # taking what's really owed closes it, with the timer billed once
    r = await client.post(f"/api/orders/{oid}/finish", json={
        "payment": {"method": "cash", "account_id": aid, "amount": 278}}, headers=tenant)
    assert r.status_code == 200, r.text
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["status"] == "done" and o["total"] == 278
    assert (await client.get(f"/api/cash/orders/{oid}/payment-context", headers=tenant)).json()["remaining"] == 0


@pytest.mark.asyncio
async def test_status_check_without_payment_still_passes_with_a_timer(client, tenant, db):
    oid = await _order(client, tenant, price=178)
    await _running_hour(client, tenant, db, oid)
    chk = (await client.post(f"/api/orders/{oid}/status/check", json={"status": "done"}, headers=tenant)).json()
    assert chk["ok"] is True, chk
