"""Hardening council M6: `serial_intake.IN_STOCK` didn't count `in_transit`, so a unit on a
transfer could be received again under the same serial. The transfer then found two live rows
for one serial and wedged with 409 (or an IntegrityError) on completion.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _loc2(client, h):
    r = await client.post("/api/staff/locations", json={"name": "Branch B"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _part(client, h):
    body = {"name": f"P-{uuid.uuid4().hex[:6]}", "sku": f"SKU-{uuid.uuid4().hex[:6]}",
            "cost_price": 10, "sell_price": 100, "serialized": True}
    r = await client.post("/api/catalog/products", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _receive(client, h, loc, pid, serials):
    return await client.post(f"/api/inventory/parts/{pid}/adjust", json={
        "location_id": loc, "qty": len(serials), "movement_type": "receive", "serials": serials}, headers=h)


async def _ship(client, h, a, b, pid, serial):
    t = await client.post("/api/transfers", json={"from_location_id": a, "to_location_id": b,
                                                   "lines": [{"part_id": pid, "qty": 1, "serials": [serial]}]}, headers=h)
    assert t.status_code == 201, t.text
    tid = t.json()["id"]
    for st in ("ready", "in_transit"):
        r = await client.patch(f"/api/transfers/{tid}/status", json={"status": st}, headers=h)
        assert r.status_code == 200, r.text
    return tid


@pytest.mark.asyncio
async def test_serial_on_a_transfer_cannot_be_received_again(client, tenant):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, a, pid, ["SN-E1"])).status_code == 200
    await _ship(client, tenant, a, b, pid, "SN-E1")
    r = await _receive(client, tenant, a, pid, ["SN-E1"])
    assert r.status_code == 409, f"expected 409 for a serial that is in transit, got {r.status_code} {r.text}"
    assert "SN-E1" in r.text


@pytest.mark.asyncio
async def test_scan_box_blocks_a_serial_that_is_in_transit(client, tenant):
    a, b = await _loc(client, tenant), await _loc2(client, tenant)
    pid = await _part(client, tenant)
    assert (await _receive(client, tenant, a, pid, ["SN-E3"])).status_code == 200
    await _ship(client, tenant, a, b, pid, "SN-E3")
    r = await client.post("/api/inventory/serials/check", json={"serials": ["SN-E3"]}, headers=tenant)
    assert r.status_code == 200, r.text
    row = r.json()["rows"][0]
    assert row["status"] == "block", row


@pytest.mark.asyncio
async def test_transfer_completes_after_a_refused_double_receipt(tenant):
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        a, b = await _loc(client, tenant), await _loc2(client, tenant)
        pid = await _part(client, tenant)
        assert (await _receive(client, tenant, a, pid, ["SN-E1B"])).status_code == 200
        tid = await _ship(client, tenant, a, b, pid, "SN-E1B")
        assert (await _receive(client, tenant, a, pid, ["SN-E1B"])).status_code == 409
        for st in ("received", "completed"):
            r = await client.patch(f"/api/transfers/{tid}/status", json={"status": st}, headers=tenant)
            assert r.status_code == 200, f"transfer {st} -> {r.status_code} {r.text[:300]}"
