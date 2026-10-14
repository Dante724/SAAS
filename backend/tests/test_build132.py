"""Build 132 — Purchase Orders: group, send (email/sms/whatsapp), print."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _supplier(client, h, name="Acme"):
    return (await client.post("/api/suppliers", json={"name": name, "phone": "+15551234"}, headers=h)).json()["id"]


async def _part(client, h, name, sku):
    return (await client.post("/api/inventory/parts", json={"name": name, "sku": sku}, headers=h)).json()["id"]


async def _po(client, h, sid, loc, part_id, qty, cost):
    r = await client.post("/api/inventory/po", json={
        "supplier_id": sid, "location_id": loc,
        "lines": [{"part_id": part_id, "qty_ordered": qty, "unit_cost": cost}]}, headers=h)
    return r.json()["po_id"]


@pytest.mark.asyncio
async def test_group_pos_merges_lines(client, tenant):
    loc = await _loc(client, tenant)
    sid = await _supplier(client, tenant)
    p1 = await _part(client, tenant, "Screen", "SC-1")
    a = await _po(client, tenant, sid, loc, p1, 2, 100)
    b = await _po(client, tenant, sid, loc, p1, 3, 100)

    r = await client.post("/api/inventory/po/group", json={"po_ids": [a, b]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["grouped"] == 2

    pos = (await client.get("/api/inventory/po", headers=tenant)).json()["items"]
    new = next(p for p in pos if p["id"] == r.json()["po_id"])
    assert len(new["lines"]) == 1 and new["lines"][0]["qty_ordered"] == 5     # 2 + 3 merged
    # sources are marked grouped
    assert {p["status"] for p in pos if p["id"] in (a, b)} == {"grouped"}


@pytest.mark.asyncio
async def test_group_rejects_mixed_suppliers(client, tenant):
    loc = await _loc(client, tenant)
    s1, s2 = await _supplier(client, tenant, "S1"), await _supplier(client, tenant, "S2")
    p = await _part(client, tenant, "Batt", "B-1")
    a = await _po(client, tenant, s1, loc, p, 1, 10)
    b = await _po(client, tenant, s2, loc, p, 1, 10)
    r = await client.post("/api/inventory/po/group", json={"po_ids": [a, b]}, headers=tenant)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_send_po_logs_message(client, tenant):
    loc = await _loc(client, tenant)
    sid = await _supplier(client, tenant)
    p = await _part(client, tenant, "Cable", "CB-1")
    po = await _po(client, tenant, sid, loc, p, 1, 5)
    r = await client.post(f"/api/inventory/po/{po}/send", json={"channel": "whatsapp"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["channel"] == "whatsapp" and r.json()["logged"] is True

    bad = await client.post(f"/api/inventory/po/{po}/send", json={"channel": "carrier-pigeon"}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_print_po_returns_pdf(client, tenant):
    loc = await _loc(client, tenant)
    sid = await _supplier(client, tenant)
    p = await _part(client, tenant, "Fan", "F-1")
    po = await _po(client, tenant, sid, loc, p, 1, 5)
    r = await client.get(f"/api/print/purchase-order/{po}", headers=tenant)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
