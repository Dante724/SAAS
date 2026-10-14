"""144bf — supplier payment: settle AP balance with a linked cash expense + contact-ledger row."""
import pytest
from sqlalchemy import select


async def _account(client, h):
    r = await client.get("/api/cash/accounts", headers=h)
    items = r.json().get("items", [])
    if items:
        return items[0]["id"]
    return (await client.post("/api/cash/accounts", json={"name": "Till", "is_company": True}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_pay_supplier_reduces_balance_and_cash(client, tenant, db):
    from backend.models import Account, Supplier
    acc_id = await _account(client, tenant)
    acc0 = (await db.execute(select(Account).where(Account.id == acc_id))).scalar_one()
    start_cash = float(acc0.balance or 0)

    sup = (await client.post("/api/suppliers", json={"name": "Acme Parts"}, headers=tenant)).json()
    sid = sup["id"]
    # put them in credit: we owe 100 (set the AP balance directly as a receipt would)
    srow = (await db.execute(select(Supplier).where(Supplier.id == sid))).scalar_one()
    srow.balance = 100.0
    await db.commit()

    r = await client.post(f"/api/suppliers/{sid}/pay",
                          json={"amount": 60, "account_id": acc_id, "note": "part payment"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == pytest.approx(40.0)     # 100 - 60

    db.expire_all()   # endpoint committed on its own session; drop the test session's cached rows
    # supplier balance persisted
    srow2 = (await db.execute(select(Supplier).where(Supplier.id == sid))).scalar_one()
    assert float(srow2.balance) == pytest.approx(40.0)
    # cash account dropped by 60
    acc1 = (await db.execute(select(Account).where(Account.id == acc_id))).scalar_one()
    assert float(acc1.balance) == pytest.approx(start_cash - 60.0)
    # ledger shows the settlement on the mirrored contact
    led = (await client.get(f"/api/suppliers/{sid}/ledger", headers=tenant)).json()
    assert any(e["ref_type"] == "supplier_payment" and e["amount"] == pytest.approx(60.0) for e in led["entries"])


@pytest.mark.asyncio
async def test_pay_supplier_rejects_nonpositive(client, tenant):
    sid = (await client.post("/api/suppliers", json={"name": "Z"}, headers=tenant)).json()["id"]
    acc_id = await _account(client, tenant)
    r = await client.post(f"/api/suppliers/{sid}/pay", json={"amount": 0, "account_id": acc_id}, headers=tenant)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_pay_supplier_bad_account(client, tenant):
    sid = (await client.post("/api/suppliers", json={"name": "Y"}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/suppliers/{sid}/pay", json={"amount": 10, "account_id": "nope"}, headers=tenant)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_overpay_tips_to_credit(client, tenant, db):
    from backend.models import Supplier
    acc_id = await _account(client, tenant)
    sid = (await client.post("/api/suppliers", json={"name": "Credit Co"}, headers=tenant)).json()["id"]
    srow = (await db.execute(select(Supplier).where(Supplier.id == sid))).scalar_one()
    srow.balance = 30.0
    await db.commit()
    r = await client.post(f"/api/suppliers/{sid}/pay", json={"amount": 50, "account_id": acc_id}, headers=tenant)
    assert r.status_code == 200
    assert r.json()["balance"] == pytest.approx(-20.0)    # supplier now owes us 20
