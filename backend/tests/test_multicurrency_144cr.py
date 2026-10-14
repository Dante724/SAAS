"""Build 144cr — multi-currency: registry + history, currency accounts, FX transfer, total-money."""
import pytest


async def _base(client, h):
    r = await client.get("/api/finance/currencies", headers=h)
    return r.json()["base"]["code"]


@pytest.mark.asyncio
async def test_currency_registry_and_history(client, tenant):
    h = tenant
    base = await _base(client, h)
    add = "EUR" if base != "EUR" else "GBP"
    r = await client.post("/api/finance/currencies", headers=h, json={"code": add, "symbol": "€", "rate": 1.1})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    # dup rejected
    assert (await client.post("/api/finance/currencies", headers=h, json={"code": add, "rate": 1.1})).status_code == 409
    # edit rate -> history grows; same rate -> no new row
    await client.patch(f"/api/finance/currencies/{cid}", headers=h, json={"rate": 1.2})
    await client.patch(f"/api/finance/currencies/{cid}", headers=h, json={"rate": 1.2})
    hist = (await client.get(f"/api/finance/currencies/{cid}/history", headers=h)).json()["items"]
    assert len(hist) == 2 and hist[0]["rate"] == 1.2   # newest first


@pytest.mark.asyncio
async def test_fx_transfer_math(client, tenant):
    h = tenant
    base = await _base(client, h)
    sec = "EUR" if base != "EUR" else "GBP"
    await client.post("/api/finance/currencies", headers=h, json={"code": sec, "symbol": "€", "rate": 1.2})
    a_base = (await client.post("/api/cash/accounts", headers=h, json={"name": "Base cash"})).json()
    a_sec = (await client.post("/api/cash/accounts", headers=h, json={"name": "FX", "currency_code": sec})).json()
    assert a_sec["currency_code"] == sec
    # base -> secondary : divide (100 / 1.25 = 80)
    r = await client.post("/api/cash/transfer", headers=h, json={
        "from_account_id": a_base["id"], "to_account_id": a_sec["id"], "amount": 100, "rate": 1.25})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["debited"] == 100 and body["credited"] == 80.0 and body["rate"] == 1.25
    # secondary -> base : multiply (50 * 1.25 = 62.5)
    r2 = await client.post("/api/cash/transfer", headers=h, json={
        "from_account_id": a_sec["id"], "to_account_id": a_base["id"], "amount": 50, "rate": 1.25})
    assert r2.json()["credited"] == 62.5


@pytest.mark.asyncio
async def test_secondary_to_secondary_rejected(client, tenant):
    h = tenant
    base = await _base(client, h)
    s1 = "EUR" if base != "EUR" else "AUD"
    s2 = "GBP" if base != "GBP" else "AUD"
    await client.post("/api/finance/currencies", headers=h, json={"code": s1, "rate": 1.2})
    await client.post("/api/finance/currencies", headers=h, json={"code": s2, "rate": 1.3})
    a1 = (await client.post("/api/cash/accounts", headers=h, json={"name": "A1", "currency_code": s1})).json()
    a2 = (await client.post("/api/cash/accounts", headers=h, json={"name": "A2", "currency_code": s2})).json()
    r = await client.post("/api/cash/transfer", headers=h, json={
        "from_account_id": a1["id"], "to_account_id": a2["id"], "amount": 10, "rate": 1.1})
    assert r.status_code == 400 and "base currency" in r.text


@pytest.mark.asyncio
async def test_total_money_per_currency_and_delete_guard(client, tenant):
    h = tenant
    base = await _base(client, h)
    sec = "EUR" if base != "EUR" else "GBP"
    cur = (await client.post("/api/finance/currencies", headers=h, json={"code": sec, "rate": 1.2})).json()
    await client.post("/api/cash/accounts", headers=h, json={"name": "Base", "currency_code": None})
    await client.post("/api/cash/accounts", headers=h, json={"name": "Sec", "currency_code": sec})
    tm = (await client.get("/api/cash/total-money", headers=h)).json()
    codes = {c["code"] for c in tm["columns"]}
    assert base in codes and sec in codes
    assert tm["columns"][0]["is_base"] is True   # base column first
    # cannot delete a currency an account uses
    d = await client.delete(f"/api/finance/currencies/{cur['id']}", headers=h)
    assert d.status_code == 409
