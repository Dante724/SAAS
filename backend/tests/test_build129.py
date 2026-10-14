"""Build 129 — invoice a chosen subset of an order's lines (the invoice drawer)."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _client(client, h):
    return (await client.post("/api/clients", json={"name": "Inv", "phone": "+15550000"}, headers=h)).json()["id"]


async def _line(client, h, oid, price, desc):
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "custom", "description": desc,
        "qty": 1, "unit_price": price}, headers=h)
    return r.json().get("line_id")


@pytest.mark.asyncio
async def test_invoice_selected_lines_only(client, tenant):
    cid = await _client(client, tenant)
    oid = (await client.post("/api/orders", json={
        "location_id": await _loc(client, tenant), "complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    l1 = await _line(client, tenant, oid, 100, "Part A")
    await _line(client, tenant, oid, 200, "Part B")

    # bill only the first line
    r = await client.post(f"/api/cash/orders/{oid}/invoices", json={"line_ids": [l1]}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == pytest.approx(100)   # only Part A, not the 300 order total

    # a second invoice can bill the rest
    r2 = await client.post(f"/api/cash/orders/{oid}/invoices", json={"line_ids": []}, headers=tenant)
    assert r2.status_code == 201
    listed = (await client.get(f"/api/cash/orders/{oid}/invoices", headers=tenant)).json()
    assert len(listed["invoices"]) == 2
