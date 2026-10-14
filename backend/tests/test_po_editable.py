"""Build 144al — editable purchase orders: detail, edit (header/lines/charges),
customizable charge types, and charges flowing into supplier finance + landed cost."""
import pytest

from backend.models import Supplier


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _supplier(db, client, tenant):
    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    s = Supplier(org_id=org_id, name="Acme Parts", email="ap@acme.com", balance=0.0)
    db.add(s)
    await db.commit()
    return s.id


async def _part(client, tenant, name, cost):
    return (await client.post("/api/inventory/parts", json={"name": name, "cost_price": cost},
                              headers=tenant)).json()["id"]


@pytest.mark.asyncio
async def test_po_detail_edit_and_charges(db, client, tenant):
    loc = await _loc(client, tenant)
    sup = await _supplier(db, client, tenant)
    p1 = await _part(client, tenant, "Screen", 10)

    po = (await client.post("/api/inventory/po", json={
        "supplier_id": sup, "location_id": loc, "on_credit": True,
        "lines": [{"part_id": p1, "qty_ordered": 2, "unit_cost": 10}]}, headers=tenant)).json()
    pid = po["po_id"]

    # detail is fetchable with supplier block + editable flag
    d = (await client.get(f"/api/inventory/po/{pid}", headers=tenant)).json()
    assert d["supplier"]["name"] == "Acme Parts"
    assert d["editable"] is True
    assert d["subtotal"] == 20 and d["total"] == 20

    # edit: add a second product, set supplier invoice, add a shipping charge
    p2 = await _part(client, tenant, "Cable", 3)
    d2 = (await client.patch(f"/api/inventory/po/{pid}", json={
        "supplier_invoice_no": "INV-778", "supplier_invoice_date": "2026-08-05",
        "charges": [{"label": "Shipping", "amount": 15}],
        "lines": [
            {"id": d["lines"][0]["id"], "part_id": p1, "qty_ordered": 3, "unit_cost": 10},
            {"part_id": p2, "qty_ordered": 5, "unit_cost": 3},
        ]}, headers=tenant)).json()
    assert d2["supplier_invoice_no"] == "INV-778"
    assert d2["charges_total"] == 15
    # subtotal = 3*10 + 5*3 = 45; total = 45 + 15
    assert d2["subtotal"] == 45 and d2["total"] == 60
    assert len(d2["lines"]) == 2


@pytest.mark.asyncio
async def test_charges_hit_supplier_balance_on_receive(db, client, tenant):
    loc = await _loc(client, tenant)
    sup = await _supplier(db, client, tenant)
    p1 = await _part(client, tenant, "Board", 40)
    po = (await client.post("/api/inventory/po", json={
        "supplier_id": sup, "location_id": loc, "on_credit": True,
        "lines": [{"part_id": p1, "qty_ordered": 1, "unit_cost": 40}]}, headers=tenant)).json()
    pid = po["po_id"]
    # add a flat shipping charge (not capitalised into cost)
    await client.patch(f"/api/inventory/po/{pid}",
                       json={"charges": [{"label": "Shipping", "amount": 12}], "charges_to_cost": False},
                       headers=tenant)
    # receive it fully
    r = await client.post(f"/api/inventory/po/{pid}/receive", json={"lines": []}, headers=tenant)
    assert r.status_code == 200, r.text

    from sqlalchemy import select
    s = (await db.execute(select(Supplier).where(Supplier.id == sup))).scalar_one()
    # we owe the goods (40) + the shipping charge (12)
    assert round(s.balance, 2) == 52.0


@pytest.mark.asyncio
async def test_landed_cost_allocates_freight_into_unit_cost(db, client, tenant):
    loc = await _loc(client, tenant)
    sup = await _supplier(db, client, tenant)
    p1 = await _part(client, tenant, "Widget", 10)
    po = (await client.post("/api/inventory/po", json={
        "supplier_id": sup, "location_id": loc, "on_credit": False,
        "lines": [{"part_id": p1, "qty_ordered": 10, "unit_cost": 10}]}, headers=tenant)).json()
    pid = po["po_id"]
    # 20 freight over 10 units of a single line → +2/unit landed; toggle allocation on
    await client.patch(f"/api/inventory/po/{pid}",
                       json={"charges": [{"label": "Freight", "amount": 20}], "charges_to_cost": True},
                       headers=tenant)
    await client.post(f"/api/inventory/po/{pid}/receive", json={"lines": []}, headers=tenant)

    from sqlalchemy import select

    from backend.models import Part
    part = (await db.execute(select(Part).where(Part.id == p1))).scalar_one()
    # moving-average cost now reflects 10 + 2 freight = 12
    assert round(part.avg_cost, 2) == 12.0


@pytest.mark.asyncio
async def test_charge_types_customizable_and_received_lock(db, client, tenant):
    # default charge types then customise
    d = (await client.get("/api/inventory/po-charge-types", headers=tenant)).json()
    assert "Shipping" in d["types"]
    up = (await client.put("/api/inventory/po-charge-types",
                           json={"types": ["Shipping", "Customs", "Handling"]}, headers=tenant)).json()
    assert up["types"] == ["Shipping", "Customs", "Handling"]

    # a fully-received PO can't be edited
    loc = await _loc(client, tenant)
    sup = await _supplier(db, client, tenant)
    p1 = await _part(client, tenant, "Thing", 5)
    pid = (await client.post("/api/inventory/po", json={
        "supplier_id": sup, "location_id": loc,
        "lines": [{"part_id": p1, "qty_ordered": 1, "unit_cost": 5}]}, headers=tenant)).json()["po_id"]
    await client.post(f"/api/inventory/po/{pid}/receive", json={"lines": []}, headers=tenant)
    locked = await client.patch(f"/api/inventory/po/{pid}", json={"notes": "x"}, headers=tenant)
    assert locked.status_code == 409
