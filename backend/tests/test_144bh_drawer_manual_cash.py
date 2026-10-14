"""144bh — manual cash income/expense recorded on the till during an open shift must feed the
drawer's expected cash, so the blind-count close agrees with the cashbox balance (not treat the
whole till as a surplus). Income on a non-cash box (card/UPI) must NOT touch the drawer."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_manual_cash_feeds_expected(client, tenant):
    loc = await _loc(client, tenant)
    acc = (await client.post("/api/cash/accounts",
           json={"name": "Till 144bh", "payment_option": "cash", "location_id": loc}, headers=tenant)).json()
    sid = (await client.post("/api/finance/shifts/open",
           json={"location_id": loc, "opening_float": 0}, headers=tenant)).json()["id"]
    assert (await client.post("/api/cash/transactions",
            json={"account_id": acc["id"], "txn_type": "income", "amount": 1000}, headers=tenant)).status_code == 201
    assert (await client.post("/api/cash/transactions",
            json={"account_id": acc["id"], "txn_type": "expense", "amount": 200}, headers=tenant)).status_code == 201
    # expected = 0 opening + 1000 income − 200 expense = 800 → counting 800 is balanced
    r = await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 800}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expected_cash"] == 800.0, body
    assert body["discrepancy"] == 0.0, body


@pytest.mark.asyncio
async def test_company_wide_till_single_open_shift(client, tenant):
    # a company-wide cash box (no location_id) is the till; with exactly one open shift org-wide the
    # manual cash is unambiguous and must feed that drawer.
    loc = await _loc(client, tenant)
    acc = (await client.post("/api/cash/accounts",
           json={"name": "Company Till 144bh", "payment_option": "cash", "is_company": True}, headers=tenant)).json()
    assert acc.get("location_id") in (None, ""), acc
    sid = (await client.post("/api/finance/shifts/open",
           json={"location_id": loc, "opening_float": 0}, headers=tenant)).json()["id"]
    await client.post("/api/cash/transactions",
                      json={"account_id": acc["id"], "txn_type": "income", "amount": 500}, headers=tenant)
    r = await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 500}, headers=tenant)
    assert r.json()["expected_cash"] == 500.0 and r.json()["discrepancy"] == 0.0, r.json()


@pytest.mark.asyncio
async def test_non_cash_box_not_counted(client, tenant):
    loc = await _loc(client, tenant)
    card = (await client.post("/api/cash/accounts",
            json={"name": "Card 144bh", "payment_option": "card", "location_id": loc}, headers=tenant)).json()["id"]
    sid = (await client.post("/api/finance/shifts/open",
           json={"location_id": loc, "opening_float": 100}, headers=tenant)).json()["id"]
    await client.post("/api/cash/transactions",
                      json={"account_id": card, "txn_type": "income", "amount": 500}, headers=tenant)
    r = await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 100}, headers=tenant)
    assert r.json()["expected_cash"] == 100.0, r.json()   # card income ignored by the drawer
