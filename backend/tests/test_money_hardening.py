"""Full-app audit money fixes (144an): refund can't exceed what was paid and posts to the client
ledger; pay_order rejects a double-pay / non-positive amount; archiving an on-account (balance)
order doesn't hand back cash that never entered the till."""
import pytest


async def _order_with_total(client, h, total=100):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15559"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=h)
    return cid, oid


async def _account(client, h):
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    return (await client.get("/api/cash/accounts", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_pay_order_rejects_double_pay_and_negative(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    assert (await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": aid, "amount": 100}, headers=tenant)).status_code in (200, 201)
    # Second "pay the balance" tap: nothing left → 400, not a silent double-charge.
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": aid}, headers=tenant)
    assert r.status_code == 400, r.text
    # Negative amount rejected.
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": aid, "amount": -20}, headers=tenant)
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_refund_cannot_exceed_paid_and_posts_ledger(client, tenant):
    cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": aid, "amount": 100}, headers=tenant)

    # Over-refund is refused.
    r = await client.post("/api/returns/refunds", json={"account_id": aid, "amount": 150, "order_id": oid}, headers=tenant)
    assert r.status_code == 400, r.text

    before = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]
    r = await client.post("/api/returns/refunds", json={"account_id": aid, "amount": 40, "order_id": oid, "reason": "goodwill"}, headers=tenant)
    assert r.status_code == 201, r.text
    after = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]
    # A 40 refund moves the client balance by 40 (they now owe 40 more / 40 less credit).
    assert round(abs(after - before), 2) == 40.0, (before, after)


@pytest.mark.asyncio
async def test_archive_reverses_the_order_charge(client, tenant):
    # Closing an order posts a 'charge' to the client ledger (they owe the total). Archiving it
    # must zero that charge so no phantom receivable is left behind.
    cid, oid = await _order_with_total(client, tenant, 100)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    owed = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]
    assert owed == 100.0, owed   # charge posted on close
    r = await client.delete(f"/api/orders/{oid}?refund=false", headers=tenant)
    assert r.status_code == 200, r.text
    after = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["balance"]
    assert after == 0.0, after   # charge reversed on archive


@pytest.mark.asyncio
async def test_archive_restocks_consumed_parts(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "ArchPart", "sku": "AP-1"}, headers=tenant)).json()["id"]
    await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                      json={"location_id": loc, "qty": 10, "movement_type": "receive"})
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc,
        "lines": [{"line_type": "part", "kind": "product", "ref_id": pid, "description": "Part", "qty": 3, "unit_price": 20}]}, headers=tenant)).json()["id"]
    after_use = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["qty_available"]
    assert after_use == 7, after_use   # 3 deducted on create

    assert (await client.delete(f"/api/orders/{oid}?refund=false", headers=tenant)).status_code == 200
    restored = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["qty_available"]
    assert restored == 10, restored   # the 3 consumed parts returned to stock on archive


@pytest.mark.asyncio
async def test_archive_does_not_cash_refund_on_account_payment(client, tenant):
    _cid, oid = await _order_with_total(client, tenant, 100)
    # Settle on the client's balance — no cash ever entered the till.
    assert (await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "balance", "amount": 100}, headers=tenant)).status_code in (200, 201)
    # Archiving with refund must NOT pay out cash for a balance-settled order.
    r = await client.delete(f"/api/orders/{oid}?refund=true", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["refunded"] == 0, r.json()
