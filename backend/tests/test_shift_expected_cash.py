"""Build 144au (H1) — closing a cash shift must compute expected_cash from the opening float plus
the cash payments taken on that shift, so the discrepancy is real instead of always zero."""
import pytest


async def _invoice(client, h, subtotal=100):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15559"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": subtotal}, headers=h)).json()
    return inv["id"]


@pytest.mark.asyncio
async def test_expected_cash_counts_shift_cash_payments(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    shift = (await client.post("/api/finance/shifts/open",
                               json={"location_id": loc, "opening_float": 200}, headers=tenant)).json()
    sid = shift["id"]

    inv = await _invoice(client, tenant, 100)
    r = await client.post(f"/api/finance/invoices/{inv}/pay",
                          json={"method": "cash", "amount": 100, "shift_id": sid}, headers=tenant)
    assert r.status_code == 200, r.text

    # A card payment on the same shift must NOT count toward the till.
    inv2 = await _invoice(client, tenant, 50)
    await client.post(f"/api/finance/invoices/{inv2}/pay",
                      json={"method": "card", "amount": 50, "shift_id": sid}, headers=tenant)

    # Operator counts only 250 in the drawer, but expected = opening 200 + 100 cash = 300
    # (the 50 card payment does NOT touch the till). Discrepancy must be -50, not 0.
    r = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=250", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["discrepancy"] == -50.0, r.json()
