"""Build 144bb — blind cash count. The person counting the drawer must NOT see the expected
total (industry best practice + fraud control): a cashier counts blind, only a finance-permitted
user sees expected vs. counted and the discrepancy. Expected is still computed + stored for the
admin; it's just withheld from the response for non-finance roles."""
import pytest
from sqlalchemy import select


async def _open_paid_shift(client, h):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    sid = (await client.post("/api/finance/shifts/open",
                             json={"location_id": loc, "opening_float": 100}, headers=h)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 50}, headers=h)).json()
    await client.post(f"/api/finance/invoices/{inv['id']}/pay",
                      json={"method": "cash", "amount": 50, "shift_id": sid}, headers=h)
    return sid


@pytest.mark.asyncio
async def test_finance_user_sees_expected_and_discrepancy(client, tenant):
    sid = await _open_paid_shift(client, tenant)
    r = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=150", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "expected_cash" in body and body["expected_cash"] == 150.0
    assert "discrepancy" in body and body["discrepancy"] == 0.0


@pytest.mark.asyncio
async def test_non_finance_role_closes_blind(client, tenant, db):
    from backend.models import Employee, Role
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    # make the current user a cashier who cannot view finance
    role = Role(org_id=me["org_id"], name="Cashier", permissions={"can_view_finance": False})
    db.add(role)
    await db.flush()
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.role_id = role.id
    await db.commit()

    sid = await _open_paid_shift(client, tenant)
    r = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=150", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    # blind: the counter is not shown expected or discrepancy
    assert "expected_cash" not in body, body
    assert "discrepancy" not in body, body
