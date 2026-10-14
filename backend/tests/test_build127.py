"""Build 127 — WO Invoices & payments tab + client Loyalty tab wiring.

The UI changes are structural (new tabs, relocated actions, CSS polish); these tests
guard the endpoints those tabs call so the wiring can't silently rot.
"""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _client(client, h, name="Inv Client"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15558888"}, headers=h)).json()["id"]


async def _order_with_line(client, h, cid, price=1000):
    oid = (await client.post("/api/orders", json={
        "location_id": await _loc(client, h), "complaint": "x", "client_id": cid}, headers=h)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "Repair",
        "qty": 1, "unit_price": price}, headers=h)
    return oid


# ── WO Invoices & payments tab: create a full invoice ──────
@pytest.mark.asyncio
async def test_create_invoice_for_whole_order(client, tenant):
    cid = await _client(client, tenant)
    oid = await _order_with_line(client, tenant, cid, price=800)

    r = await client.post(f"/api/cash/orders/{oid}/invoices", json={"line_ids": []}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["invoice_number"] and r.json()["total"] == pytest.approx(800)

    listed = (await client.get(f"/api/cash/orders/{oid}/invoices", headers=tenant)).json()
    assert len(listed["invoices"]) == 1


# ── client Loyalty tab endpoint ────────────────────────────
@pytest.mark.asyncio
async def test_client_loyalty_shape(client, tenant):
    cid = await _client(client, tenant, name="Loyal One")
    d = (await client.get(f"/api/clients/{cid}/loyalty", headers=tenant)).json()
    for k in ("points", "lifetime_points", "tier", "point_value", "earn_rate", "enabled"):
        assert k in d
    assert d["points"] == 0     # fresh client has no points yet
