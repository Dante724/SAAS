"""Build 144bf — the third reconciliation leg: card payments (net of fees) must equal what the
processor actually deposited. Recording a payout that doesn't match the card take flags a gap;
a matching payout leaves the books clean. (Connector fills payouts later; manual entry for now.)"""
import pytest


async def _card_sale(client, h, total=100, fee_pct=2.9):
    aid = (await client.post("/api/cash/accounts", json={"name": "Card", "payment_option": "card",
                                                         "fee_pct": fee_pct}, headers=h)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=h)
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "card", "account_id": aid, "amount": total}, headers=h)
    assert r.status_code in (200, 201), r.text
    # 100 card take, 2.90 fee → expected bank deposit 97.10


@pytest.mark.asyncio
async def test_payout_gap_is_flagged(client, tenant):
    await _card_sale(client, tenant, 100, 2.9)
    # processor only deposited 50 — a real gap of ~47.10
    r = await client.post("/api/finance/payouts", json={"gross_amount": 100, "fee_amount": 2.90, "net_amount": 50}, headers=tenant)
    assert r.status_code == 201, r.text

    rec = (await client.get("/api/finance/reconciliation", headers=tenant)).json()
    gap = [it for it in rec["items"] if it["type"] == "payout_gap"]
    assert gap, rec
    assert gap[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_matching_payout_is_clean(client, tenant):
    await _card_sale(client, tenant, 100, 2.9)
    # processor deposited exactly the expected net
    r = await client.post("/api/finance/payouts", json={"gross_amount": 100, "fee_amount": 2.90}, headers=tenant)
    assert r.status_code == 201 and r.json()["net_amount"] == 97.10, r.text   # net defaults to gross − fee

    rec = (await client.get("/api/finance/reconciliation", headers=tenant)).json()
    assert not [it for it in rec["items"] if it["type"] == "payout_gap"], rec


@pytest.mark.asyncio
async def test_payouts_list(client, tenant):
    await client.post("/api/finance/payouts", json={"provider": "stripe", "gross_amount": 200, "fee_amount": 6, "reference": "po_123"}, headers=tenant)
    items = (await client.get("/api/finance/payouts", headers=tenant)).json()["items"]
    assert items and items[0]["provider"] == "stripe" and items[0]["net_amount"] == 194.0
