"""Build 139 — location scope reaches Cash accounts and Finance invoices."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_accounts_scope_keeps_company_wide(client, tenant):
    loc = await _loc(client, tenant)
    loc2 = (await client.post("/api/staff/locations", json={"name": "Branch B"}, headers=tenant)).json()["id"]

    # a company-wide account (no location) and one tied to loc2
    comp = await client.post("/api/cash/accounts", json={"name": "Company wallet"}, headers=tenant)
    assert comp.status_code == 201, comp.text
    await client.post("/api/cash/accounts", json={"name": "Branch B till", "location_id": loc2}, headers=tenant)

    # scoping to loc: company-wide shows, branch-B account hidden
    at_loc = (await client.get(f"/api/cash/accounts?location_id={loc}", headers=tenant)).json()["items"]
    names = {a["name"] for a in at_loc}
    assert "Company wallet" in names
    assert "Branch B till" not in names

    # scoping to loc2: company-wide + branch-B account
    at_b = {a["name"] for a in (await client.get(f"/api/cash/accounts?location_id={loc2}", headers=tenant)).json()["items"]}
    assert "Company wallet" in at_b and "Branch B till" in at_b


@pytest.mark.asyncio
async def test_invoices_scope_by_order_location(client, tenant):
    loc = await _loc(client, tenant)
    loc2 = (await client.post("/api/staff/locations", json={"name": "Branch C"}, headers=tenant)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "I", "phone": "+1"}, headers=tenant)).json()["id"]

    # an order at loc with a line, then an invoice for the whole order
    oid = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "Fix", "qty": 1, "unit_price": 100}, headers=tenant)
    inv = await client.post(f"/api/cash/orders/{oid}/invoices", json={"line_ids": []}, headers=tenant)
    assert inv.status_code == 201, inv.text

    at_loc = (await client.get(f"/api/finance/invoices?location_id={loc}", headers=tenant)).json()
    at_other = (await client.get(f"/api/finance/invoices?location_id={loc2}", headers=tenant)).json()
    assert at_loc["total"] >= 1 and len(at_loc["items"]) >= 1
    assert at_other["total"] == 0 and at_other["items"] == []
