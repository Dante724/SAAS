"""Principal-engineer hardening probe: a corrected cashbox transaction vanishes from every total.

POST /api/cash/transactions/{id}/correct posts a signed delta row and flags the ORIGINAL row
status="corrected" (routers/cash.py). The account balance is right (original + delta), but every
aggregate that filters `Transaction.status == "active"` (drawer expected cash finance.py:853,
payout reconciliation finance.py:934/937, accountant export finance.py:1227, P&L fees
services/finance_pnl.py:59, sale refunds / opex services/revenue.py:347/410/446,
orders.py:3570 drawer check) now sees only the delta. A +5 correction of a 1000 cash income
makes the drawer expect 5 instead of 1005.
"""
import pytest


async def _last_txn_id(client, h, account_id):
    body = (await client.get("/api/cash/transactions", params={"account_id": account_id}, headers=h)).json()
    rows = body["items"] if isinstance(body, dict) else body
    return rows[0]["id"]


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_corrected_income_still_counts_in_drawer_expected_cash(client, tenant):
    loc = await _loc(client, tenant)
    acc = (await client.post("/api/cash/accounts",
           json={"name": "Till PE", "payment_option": "cash", "location_id": loc}, headers=tenant)).json()
    sid = (await client.post("/api/finance/shifts/open",
           json={"location_id": loc, "opening_float": 0}, headers=tenant)).json()["id"]
    r = await client.post("/api/cash/transactions",
                          json={"account_id": acc["id"], "txn_type": "income", "amount": 1000}, headers=tenant)
    assert r.status_code == 201, r.text
    txn_id = await _last_txn_id(client, tenant, acc["id"])
    r = await client.post(f"/api/cash/transactions/{txn_id}/correct",
                          json={"delta": 5, "reason": "miscounted"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    # The cashbox itself is right: 1000 + 5
    accts = (await client.get("/api/cash/accounts", headers=tenant)).json()
    bal = next(a for a in (accts["items"] if isinstance(accts, dict) else accts) if a["id"] == acc["id"])["balance"]
    assert float(bal) == 1005.0, bal
    # The drawer must expect the same money the cashbox holds.
    r = await client.post(f"/api/finance/shifts/{sid}/close", json={"closing_float": 1005}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expected_cash"] == 1005.0, body      # observed: 5.0 (the corrected original is dropped)
    assert body["discrepancy"] == 0.0, body


@pytest.mark.asyncio
async def test_corrected_fee_still_counts_in_pnl(client, tenant):
    """P&L processing fees: a corrected fee row disappears, overstating profit."""
    acc = (await client.post("/api/cash/accounts",
           json={"name": "Card PE", "payment_option": "card"}, headers=tenant)).json()
    r = await client.post("/api/cash/transactions",
                          json={"account_id": acc["id"], "txn_type": "expense", "amount": 300,
                                "comment": "rent"}, headers=tenant)
    assert r.status_code == 201, r.text
    txn_id = await _last_txn_id(client, tenant, acc["id"])
    before = (await client.get("/api/finance/pnl", headers=tenant))
    assert before.status_code == 200, before.text
    opex_before = float(before.json().get("opex") or before.json().get("operating_expenses") or 0)
    r = await client.post(f"/api/cash/transactions/{txn_id}/correct",
                          json={"delta": -10, "reason": "underbooked"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    after = (await client.get("/api/finance/pnl", headers=tenant)).json()
    opex_after = float(after.get("opex") or after.get("operating_expenses") or 0)
    # -300 booked, corrected by a further -10 -> 310 of expenses (the cashbox shows -310).
    # Observed: only the |-10| delta row counts once the original is flagged "corrected".
    assert opex_before == 300.0, before.json()
    assert opex_after == 310.0, after
