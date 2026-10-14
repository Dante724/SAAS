"""144ar — freight allocation basis (value/qty/weight), per-shipment freight, and auto-advancing
the manual workflow stage to Received on a full receipt. Each is something Orderry/Jobber/
ShopMonkey don't do; the tests pin the money math and the stage behaviour."""
import pytest


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def _sup(client, tenant, name="Acme"):
    return (await client.post("/api/suppliers", json={"name": name}, headers=tenant)).json()["id"]


async def _part(client, tenant, name, sku, weight=None):
    body = {"name": name, "sku": sku}
    if weight is not None:
        body["weight"] = weight
    return (await client.post("/api/inventory/parts", json=body, headers=tenant)).json()["id"]


async def _avg(client, tenant, pid):
    return (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json().get("avg_cost")


async def _po(client, tenant, sid, loc, lines):
    po = (await client.post("/api/inventory/po", headers=tenant,
          json={"supplier_id": sid, "location_id": loc, "lines": lines})).json()["po_id"]
    detail = (await client.get(f"/api/inventory/po/{po}", headers=tenant)).json()
    return po, detail


@pytest.mark.asyncio
async def test_autoadvance_workflow_on_full_receipt(client, tenant):
    sid, loc = await _sup(client, tenant), await _loc(client, tenant)
    pid = await _part(client, tenant, "Belt", "AR-1")
    po, detail = await _po(client, tenant, sid, loc, [{"part_id": pid, "qty_ordered": 4, "unit_cost": 5}])
    assert detail["workflow_status"] == "po_ordered"
    line = detail["lines"][0]["id"]
    # partial receipt does NOT auto-advance to received
    await client.post(f"/api/inventory/po/{po}/receive", headers=tenant, json={"lines": [{"po_line_id": line, "qty": 2}]})
    assert (await client.get(f"/api/inventory/po/{po}", headers=tenant)).json()["workflow_status"] == "po_ordered"
    # full receipt auto-advances the manual stage to Received
    r = await client.post(f"/api/inventory/po/{po}/receive", headers=tenant, json={"lines": [{"po_line_id": line, "qty": 2}]})
    assert r.json()["workflow_status"] == "po_received"
    assert (await client.get(f"/api/inventory/po/{po}", headers=tenant)).json()["workflow_status"] == "po_received"


@pytest.mark.asyncio
async def test_autoadvance_respects_manual_terminal_stage(client, tenant):
    sid, loc = await _sup(client, tenant), await _loc(client, tenant)
    pid = await _part(client, tenant, "Hose", "AR-2")
    po, detail = await _po(client, tenant, sid, loc, [{"part_id": pid, "qty_ordered": 3, "unit_cost": 4}])
    line = detail["lines"][0]["id"]
    # buyer parks it at a terminal stage BEFORE fully receiving
    await client.post(f"/api/inventory/po/{po}/workflow", headers=tenant, json={"workflow_status": "po_closed"})
    await client.post(f"/api/inventory/po/{po}/receive", headers=tenant, json={"lines": [{"po_line_id": line, "qty": 3}]})
    # auto-advance must NOT stomp the deliberate manual choice
    assert (await client.get(f"/api/inventory/po/{po}", headers=tenant)).json()["workflow_status"] == "po_closed"


@pytest.mark.asyncio
async def test_shipment_freight_capitalised_into_this_delivery(client, tenant):
    sid, loc = await _sup(client, tenant), await _loc(client, tenant)
    pid = await _part(client, tenant, "Pump", "AR-3")
    po, detail = await _po(client, tenant, sid, loc, [{"part_id": pid, "qty_ordered": 10, "unit_cost": 2}])
    line = detail["lines"][0]["id"]
    # receive 5 with ₹10 shipment freight → +2/unit on those 5 → avg 4.0
    r = await client.post(f"/api/inventory/po/{po}/receive", headers=tenant,
                          json={"lines": [{"po_line_id": line, "qty": 5}], "shipment_freight": 10})
    assert r.status_code == 200
    assert (await _avg(client, tenant, pid)) == pytest.approx(4.0, abs=0.01)
    assert (await client.get(f"/api/inventory/po/{po}", headers=tenant)).json()["shipment_freight"] == pytest.approx(10.0, abs=0.01)


@pytest.mark.asyncio
async def test_alloc_basis_qty_splits_evenly(client, tenant):
    """Two lines, very different cost, equal qty: qty-basis splits freight 50/50, not by value."""
    sid, loc = await _sup(client, tenant), await _loc(client, tenant)
    cheap = await _part(client, tenant, "Cheap", "AR-4a")
    dear = await _part(client, tenant, "Dear", "AR-4b")
    po, detail = await _po(client, tenant, sid, loc, [
        {"part_id": cheap, "qty_ordered": 10, "unit_cost": 1},
        {"part_id": dear, "qty_ordered": 10, "unit_cost": 99},
    ])
    # set charges + qty basis, capitalise
    await client.post(f"/api/inventory/po/{po}/recost", headers=tenant,
                      json={"charges": [{"label": "Freight", "amount": 20}], "charges_to_cost": True, "alloc_basis": "qty"})
    lines = {ln["part"]: ln["id"] for ln in detail["lines"]}
    for lid in lines.values():
        await client.post(f"/api/inventory/po/{po}/receive", headers=tenant, json={"lines": [{"po_line_id": lid, "qty": 10}]})
    # qty basis → ₹20 over 20 units = ₹1/unit on BOTH parts
    assert (await _avg(client, tenant, cheap)) == pytest.approx(2.0, abs=0.02)   # 1 + 1
    assert (await _avg(client, tenant, dear)) == pytest.approx(100.0, abs=0.02)  # 99 + 1


@pytest.mark.asyncio
async def test_alloc_basis_weight(client, tenant):
    """Weight basis splits freight by total kg per line, not value or count."""
    sid, loc = await _sup(client, tenant), await _loc(client, tenant)
    light = await _part(client, tenant, "Light", "AR-5a", weight=1)
    heavy = await _part(client, tenant, "Heavy", "AR-5b", weight=3)
    po, detail = await _po(client, tenant, sid, loc, [
        {"part_id": light, "qty_ordered": 10, "unit_cost": 5},
        {"part_id": heavy, "qty_ordered": 10, "unit_cost": 5},
    ])
    await client.post(f"/api/inventory/po/{po}/recost", headers=tenant,
                      json={"charges": [{"label": "Freight", "amount": 40}], "charges_to_cost": True, "alloc_basis": "weight"})
    lines = {ln["part"]: ln["id"] for ln in detail["lines"]}
    for lid in lines.values():
        await client.post(f"/api/inventory/po/{po}/receive", headers=tenant, json={"lines": [{"po_line_id": lid, "qty": 10}]})
    # total weight = 10*1 + 10*3 = 40kg. ₹40 / 40kg = ₹1/kg.
    #   light: 1kg/unit → +1/unit → 6.0 ; heavy: 3kg/unit → +3/unit → 8.0
    assert (await _avg(client, tenant, light)) == pytest.approx(6.0, abs=0.03)
    assert (await _avg(client, tenant, heavy)) == pytest.approx(8.0, abs=0.03)
