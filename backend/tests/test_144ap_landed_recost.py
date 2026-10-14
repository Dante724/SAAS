"""144ap — late-freight landed-cost recost. The freight bill arrives after the goods; adding it
must revalue on-hand stock (moving average) — the differentiator none of Orderry/Jobber/
ShopMonkey have.
"""
import pytest


async def _setup(client, tenant, qty=10, cost=2.0):
    sid = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "RC-1"}, headers=tenant)).json()["id"]
    po_id = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "lines": [{"part_id": pid, "qty_ordered": qty, "unit_cost": cost}]})).json()["po_id"]
    line = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()["lines"][0]["id"]
    return po_id, pid, line


async def _avg(client, tenant, pid):
    p = (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()
    return p.get("avg_cost")


@pytest.mark.asyncio
async def test_late_freight_recosts_onhand(client, tenant):
    po_id, pid, line = await _setup(client, tenant, qty=10, cost=2.0)
    # receive all 10 with NO freight known yet → avg cost = 2.0
    await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant,
                      json={"lines": [{"po_line_id": line, "qty": 10}]})
    assert abs((await _avg(client, tenant, pid)) - 2.0) < 0.001

    # freight bill lands late: ₹20 shipping over 10 units on hand → +2.0/unit → avg 4.0
    r = await client.post(f"/api/inventory/po/{po_id}/recost", headers=tenant,
                          json={"charges": [{"label": "Freight", "amount": 20}], "charges_to_cost": True})
    assert r.status_code == 200 and r.json()["applied"] == pytest.approx(20.0, abs=0.01)
    assert (await _avg(client, tenant, pid)) == pytest.approx(4.0, abs=0.01)

    # idempotent — recosting again with the same charge applies nothing
    r2 = await client.post(f"/api/inventory/po/{po_id}/recost", headers=tenant,
                           json={"charges": [{"label": "Freight", "amount": 20}], "charges_to_cost": True})
    assert r2.json()["applied"] == pytest.approx(0.0, abs=0.01)
    assert (await _avg(client, tenant, pid)) == pytest.approx(4.0, abs=0.01)


@pytest.mark.asyncio
async def test_recost_noop_without_capitalise_flag(client, tenant):
    po_id, pid, line = await _setup(client, tenant, qty=5, cost=3.0)
    await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant,
                      json={"lines": [{"po_line_id": line, "qty": 5}]})
    r = await client.post(f"/api/inventory/po/{po_id}/recost", headers=tenant,
                          json={"charges": [{"label": "Freight", "amount": 15}], "charges_to_cost": False})
    assert r.status_code == 200 and r.json()["applied"] == 0.0
    assert (await _avg(client, tenant, pid)) == pytest.approx(3.0, abs=0.01)
