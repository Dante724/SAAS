"""Hardening council M9 — a cash POS return comes out of the drawer. The sale's cash leg feeds the
open shift (Transaction.shift_id), but the refund leg did not, so every close after a cash return
read short by the refunded amount (float 100, sale 10, return it, count 100 → expected 110)."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _box(client, h, name, option):
    r = await client.post("/api/cash/accounts", json={"name": name, "payment_option": option}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _balance(client, h, acc):
    items = (await client.get("/api/cash/accounts", headers=h)).json()["items"]
    return next(a for a in items if a["id"] == acc)["balance"]


async def _cash_sale_then_return(client, h, sale_acc, refund_acc, price=10):
    s = (await client.post("/api/sales", json={"account_id": sale_acc, "cash_tendered": price, "lines": [
        {"kind": "service", "description": "Svc", "qty": 1, "unit_price": price}]}, headers=h)).json()
    line = (await client.get(f"/api/sales/{s['id']}", headers=h)).json()["lines"][0]["id"]
    r = await client.post(f"/api/sales/{s['id']}/returns",
                          json={"lines": [{"line_id": line, "qty": 1}], "account_id": refund_acc}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_pos_cash_return_lowers_drawer_expected_cash(client, tenant):
    loc = await _loc(client, tenant)
    acc = await _box(client, tenant, "Till M9", "cash")
    sh = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100},
                            headers=tenant)).json()["id"]
    await _cash_sale_then_return(client, tenant, acc, acc)
    assert await _balance(client, tenant, acc) == 0            # 10 in, 10 out
    rc = await client.post(f"/api/finance/shifts/{sh}/close", json={"closing_float": 100}, headers=tenant)
    assert rc.status_code == 200, rc.text
    body = rc.json()
    assert body["expected_cash"] == pytest.approx(100), body
    assert body["discrepancy"] == pytest.approx(0), body


@pytest.mark.asyncio
async def test_pos_return_onto_a_card_box_leaves_the_drawer_alone(client, tenant):
    """Only a cash till's refund is drawer money: a return paid back through a card box must not
    lower expected cash, which stays at float + the cash sale."""
    loc = await _loc(client, tenant)
    cash = await _box(client, tenant, "Till M9", "cash")
    card = await _box(client, tenant, "Card M9", "card")
    sh = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100},
                            headers=tenant)).json()["id"]
    await _cash_sale_then_return(client, tenant, cash, card)
    rc = await client.post(f"/api/finance/shifts/{sh}/close", json={"closing_float": 110}, headers=tenant)
    assert rc.status_code == 200, rc.text
    assert rc.json()["expected_cash"] == pytest.approx(110), rc.json()
