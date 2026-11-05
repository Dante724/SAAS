"""Hardening council M4: after a partial receipt, PATCH /po/{id} rewrote a received line's part,
quantity and price. The receiving record (movements, cost layers, qty_received) then pointed at a
different product, or at more units than the line said were ordered. A received line now keeps
its product and price, and its quantity can't drop below what has arrived; raising the quantity
and editing untouched lines still work.
"""
import uuid

import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h):
    body = {"name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "cost_price": 5, "sell_price": 100}
    r = await client.post("/api/catalog/products", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _supplier(client, h):
    r = await client.post("/api/suppliers", json={"name": f"Sup {uuid.uuid4().hex[:4]}"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _partial_po(client, h, received=4):
    """A PO of 10 × pa @ 5 plus 3 × pb @ 7, with `received` of pa already in. Returns ids."""
    loc = await _loc(client, h)
    pa, pb = await _part(client, h), await _part(client, h)
    sup = await _supplier(client, h)
    po = await client.post("/api/inventory/po", json={"supplier_id": sup, "location_id": loc,
                                                       "lines": [{"part_id": pa, "qty_ordered": 10, "unit_cost": 5},
                                                                 {"part_id": pb, "qty_ordered": 3, "unit_cost": 7}]}, headers=h)
    assert po.status_code == 201, po.text
    po_id = po.json()["id"]
    lines = (await client.get(f"/api/inventory/po/{po_id}", headers=h)).json()["lines"]
    la = next(ln for ln in lines if ln["part_id"] == pa)
    lb = next(ln for ln in lines if ln["part_id"] == pb)
    r = await client.post(f"/api/inventory/po/{po_id}/receive", json={"lines": [{"po_line_id": la["id"], "qty": received}]}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "partial", r.text
    return po_id, pa, pb, la["id"], lb["id"]


async def _line(client, h, po_id, line_id):
    return next(ln for ln in (await client.get(f"/api/inventory/po/{po_id}", headers=h)).json()["lines"] if ln["id"] == line_id)


@pytest.mark.asyncio
async def test_received_line_cannot_change_product(client, tenant):
    po_id, pa, pb, la, lb = await _partial_po(client, tenant)
    r = await client.patch(f"/api/inventory/po/{po_id}", json={"lines": [
        {"id": la, "part_id": pb, "qty_ordered": 10, "unit_cost": 5},
        {"id": lb, "part_id": pb, "qty_ordered": 3, "unit_cost": 7}]}, headers=tenant)
    assert r.status_code == 409, f"part swap on a received line accepted: {r.status_code} {r.text[:300]}"
    line = await _line(client, tenant, po_id, la)
    assert (line["part_id"], line["qty_ordered"], line["qty_received"]) == (pa, 10, 4)


@pytest.mark.asyncio
async def test_received_line_cannot_drop_below_what_arrived(client, tenant):
    po_id, pa, pb, la, lb = await _partial_po(client, tenant)
    r = await client.patch(f"/api/inventory/po/{po_id}", json={"lines": [
        {"id": la, "part_id": pa, "qty_ordered": 2, "unit_cost": 5},
        {"id": lb, "part_id": pb, "qty_ordered": 3, "unit_cost": 7}]}, headers=tenant)
    assert r.status_code == 409, f"qty below received accepted: {r.status_code} {r.text[:300]}"
    assert (await _line(client, tenant, po_id, la))["qty_ordered"] == 10


@pytest.mark.asyncio
async def test_received_line_cannot_change_price(client, tenant):
    po_id, pa, pb, la, lb = await _partial_po(client, tenant)
    r = await client.patch(f"/api/inventory/po/{po_id}", json={"lines": [
        {"id": la, "part_id": pa, "qty_ordered": 10, "unit_cost": 99},
        {"id": lb, "part_id": pb, "qty_ordered": 3, "unit_cost": 7}]}, headers=tenant)
    assert r.status_code == 409, f"price change on a received line accepted: {r.status_code} {r.text[:300]}"
    assert (await _line(client, tenant, po_id, la))["unit_cost"] == 5


@pytest.mark.asyncio
async def test_received_line_can_still_grow_and_other_lines_stay_editable(client, tenant):
    po_id, pa, pb, la, lb = await _partial_po(client, tenant)
    pc = await _part(client, tenant)
    r = await client.patch(f"/api/inventory/po/{po_id}", json={"lines": [
        {"id": la, "part_id": pa, "qty_ordered": 12, "unit_cost": 5},     # more of the same: fine
        {"id": lb, "part_id": pc, "qty_ordered": 1, "unit_cost": 9}]},     # nothing received: fully editable
        headers=tenant)
    assert r.status_code == 200, r.text
    assert (await _line(client, tenant, po_id, la))["qty_ordered"] == 12
    lb_now = await _line(client, tenant, po_id, lb)
    assert (lb_now["part_id"], lb_now["qty_ordered"], lb_now["unit_cost"]) == (pc, 1, 9)
