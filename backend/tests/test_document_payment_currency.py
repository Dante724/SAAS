"""Document payments must land in a base-currency account (security phase 4, audit B14).

An order/invoice/sale is priced in the org's base currency. Paying 100 of it into a till
held in EUR used to add 100 EUR, silently misstating that till by the exchange rate.
"""
import pytest


async def _setup(client, h):
    base = (await client.get("/api/finance/currencies", headers=h)).json()["base"]["code"]
    sec = "EUR" if base != "EUR" else "GBP"
    await client.post("/api/finance/currencies", headers=h, json={"code": sec, "symbol": "€", "rate": 1.2})
    fx = (await client.post("/api/cash/accounts", headers=h, json={"name": "FX till", "currency_code": sec})).json()
    home = (await client.post("/api/cash/accounts", headers=h, json={"name": "Home till"})).json()
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 100}, headers=h)).json()
    return fx, home, inv


async def _balance(client, h, aid):
    return [a for a in (await client.get("/api/cash/accounts", headers=h)).json()["items"]
            if a["id"] == aid][0]["balance"] or 0.0


@pytest.mark.asyncio
async def test_invoice_payment_into_foreign_currency_till_is_refused(client, tenant):
    fx, home, inv = await _setup(client, tenant)
    r = await client.post(f"/api/finance/invoices/{inv['id']}/pay",
                          json={"method": "cash", "amount": 100, "account_id": fx["id"]}, headers=tenant)
    assert r.status_code == 422, r.text
    assert await _balance(client, tenant, fx["id"]) == 0.0
    inv_after = (await client.get(f"/api/finance/invoices/{inv['id']}", headers=tenant)).json()
    assert float(inv_after.get("amount_paid") or 0) == 0.0


@pytest.mark.asyncio
async def test_base_currency_till_and_manual_foreign_entries_still_work(client, tenant):
    fx, home, inv = await _setup(client, tenant)
    r = await client.post(f"/api/finance/invoices/{inv['id']}/pay",
                          json={"method": "cash", "amount": 100, "account_id": home["id"]}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _balance(client, tenant, home["id"]) == 100.0
    # a transfer into the EUR till carries its own rate, so it stays allowed
    r = await client.post("/api/cash/transfer", headers=tenant, json={
        "from_account_id": home["id"], "to_account_id": fx["id"], "amount": 60, "rate": 1.2})
    assert r.status_code == 201, r.text
    assert await _balance(client, tenant, fx["id"]) == 50.0
