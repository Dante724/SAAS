"""Build 144bc — the cash drawer end-to-end: open (denomination count), pay-in/pay-out with
mandatory notes, cash payment on the shift, close (blind-aware) with movements folded into the
expected total and an explained discrepancy."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _pay_invoice_cash(client, h, sid, amount):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": amount}, headers=h)).json()
    await client.post(f"/api/finance/invoices/{inv['id']}/pay",
                      json={"method": "cash", "amount": amount, "shift_id": sid}, headers=h)


@pytest.mark.asyncio
async def test_drawer_full_flow(client, tenant):
    loc = await _loc(client, tenant)
    # open with a denomination count: 1×100 + 5×20 = 200
    r = await client.post("/api/finance/shifts/open",
                          json={"location_id": loc, "denoms": {"100": 1, "20": 5}}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["opening_float"] == 200.0
    sid = r.json()["id"]

    # second open at same location is refused
    r2 = await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 50}, headers=tenant)
    assert r2.status_code == 409, r2.text

    # current shift reflects the open drawer
    cur = (await client.get(f"/api/finance/shifts/current?location_id={loc}", headers=tenant)).json()
    assert cur["open"] is True and cur["id"] == sid

    # a cash sale of 60 on this shift
    await _pay_invoice_cash(client, tenant, sid, 60)
    # pay-in 40 (change top-up), pay-out 30 (bank run) — both need notes
    assert (await client.post(f"/api/finance/shifts/{sid}/movement",
            json={"direction": "in", "amount": 40, "note": "change top-up"}, headers=tenant)).status_code == 201
    assert (await client.post(f"/api/finance/shifts/{sid}/movement",
            json={"direction": "out", "amount": 30, "note": "bank run"}, headers=tenant)).status_code == 201
    # a note is mandatory
    bad = await client.post(f"/api/finance/shifts/{sid}/movement",
                            json={"direction": "out", "amount": 10, "note": ""}, headers=tenant)
    assert bad.status_code == 422, bad.text

    # expected = 200 opening + 60 cash − 0 refunds + 40 pay-in − 30 pay-out = 270
    # close with a denomination count that's $5 short (counted 265)
    r = await client.post(f"/api/finance/shifts/{sid}/close",
                          json={"denoms": {"100": 2, "50": 1, "10": 1, "5": 1}}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counted"] == 265.0
    assert body["expected_cash"] == 270.0
    assert body["discrepancy"] == -5.0
    assert body["breakdown"]["pay_in"] == 40.0 and body["breakdown"]["pay_out"] == 30.0
    # the variance is explained, not just stated
    assert body["explanation"]["direction"] == "short"
    assert body["explanation"]["amount"] == 5.0
    assert len(body["explanation"]["movements"]) == 2

    # closing again is refused
    assert (await client.post(f"/api/finance/shifts/{sid}/close?closing_float=270", headers=tenant)).status_code == 409


@pytest.mark.asyncio
async def test_shift_history_shows_balanced_flag(client, tenant):
    loc = await _loc(client, tenant)
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100}, headers=tenant)).json()["id"]
    await client.post(f"/api/finance/shifts/{sid}/close?closing_float=100", headers=tenant)
    hist = (await client.get("/api/finance/shifts", headers=tenant)).json()["items"]
    me = [s for s in hist if s["id"] == sid][0]
    assert me["open"] is False and me["discrepancy"] == 0.0


@pytest.mark.asyncio
async def test_legacy_query_close_still_works(client, tenant):
    # back-compat: the old ?closing_float=N form (no body) must still close a drawer.
    loc = await _loc(client, tenant)
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=150", headers=tenant)
    assert r.status_code == 200 and r.json()["expected_cash"] == 100.0 and r.json()["discrepancy"] == 50.0
