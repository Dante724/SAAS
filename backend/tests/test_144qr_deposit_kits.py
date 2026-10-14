"""144q/144r — estimate deposits + job kits.

Deposit: set by percent-of-total or absolute amount, exposed on the order.
Job kit: create a reusable parts+labour bundle, then one-click apply it to an
order and see the lines land and totals recompute.
"""


async def _client(client, tenant, name="Kit Co"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15550777"}, headers=tenant)).json()["id"]


async def _order(client, tenant, cid, unit=100.0):
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "complaint": "job",
        "lines": [{"line_type": "labour", "description": "Repair", "qty": 1, "unit_price": unit}]})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_deposit_percent_and_absolute(client, tenant):
    cid = await _client(client, tenant)
    oid = await _order(client, tenant, cid, unit=200.0)  # total 200

    # percent of total
    r = await client.patch(f"/api/orders/{oid}/deposit", json={"percent": 25}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["deposit_amount"] == 50.0     # 25% of 200

    # absolute overrides
    r = await client.patch(f"/api/orders/{oid}/deposit", json={"amount": 75}, headers=tenant)
    assert r.json()["deposit_amount"] == 75.0

    # exposed on the order
    got = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert got["deposit_amount"] == 75.0

    # guards
    assert (await client.patch(f"/api/orders/{oid}/deposit", json={"percent": 150}, headers=tenant)).status_code == 400
    assert (await client.patch(f"/api/orders/{oid}/deposit", json={}, headers=tenant)).status_code == 400
