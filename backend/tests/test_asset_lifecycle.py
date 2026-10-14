"""Build 120 — asset transfer / write-off / movement log, and posting supplier."""
import pytest
from sqlalchemy import select

from backend.models import Client, Device


async def _client(client, tenant, name="Owner A"):
    return (await client.post("/api/clients", json={"name": name, "phone": "+15551"}, headers=tenant)).json()["id"]


async def _asset(client, tenant, owner="company", client_id=None):
    body = {"asset_type": "device", "brand": "Apple", "model": "iPad", "serial": "AL-1",
            "owner_type": owner}
    if client_id:
        body["client_id"] = client_id
    r = await client.post("/api/assets", json=body, headers=tenant)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_asset_creation_is_logged(client, tenant):
    aid = await _asset(client, tenant)
    log = (await client.get(f"/api/assets/{aid}/movements", headers=tenant)).json()
    assert any(m["event_type"] == "create" for m in log["items"])


@pytest.mark.asyncio
async def test_transfer_changes_owner_and_logs(client, tenant, db):
    aid = await _asset(client, tenant, owner="company")
    cid = await _client(client, tenant, "New Owner")
    r = await client.post(f"/api/assets/{aid}/transfer", json={
        "to_client_id": cid, "note": "Handed to customer"}, headers=tenant)
    assert r.status_code == 201, r.text
    d = (await db.execute(select(Device).where(Device.id == aid))).scalar_one()
    assert d.owner_type == "client" and d.client_id == cid
    log = (await client.get(f"/api/assets/{aid}/movements", headers=tenant)).json()
    assert any(m["event_type"] == "transfer" for m in log["items"])


@pytest.mark.asyncio
async def test_write_off_sets_status_and_logs(client, tenant, db):
    aid = await _asset(client, tenant)
    r = await client.post(f"/api/assets/{aid}/write-off", json={"note": "Water damage"}, headers=tenant)
    assert r.status_code == 201, r.text
    d = (await db.execute(select(Device).where(Device.id == aid))).scalar_one()
    assert d.asset_status == "written_off"
    log = (await client.get(f"/api/assets/{aid}/movements", headers=tenant)).json()
    assert any(m["event_type"] == "writeoff" for m in log["items"])


@pytest.mark.asyncio
async def test_double_write_off_blocked(client, tenant):
    aid = await _asset(client, tenant)
    await client.post(f"/api/assets/{aid}/write-off", json={}, headers=tenant)
    again = await client.post(f"/api/assets/{aid}/write-off", json={}, headers=tenant)
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_posting_with_supplier_adds_to_balance(client, tenant, db):
    # create a supplier + part
    from backend.models import Supplier
    sup = (await client.post("/api/suppliers", json={"name": "Vend Co", "phone": "+9"}, headers=tenant)).json()
    part = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": "SC-1", "cost_price": 0, "sell_price": 0}, headers=tenant)).json()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    r = await client.post("/api/inventory/postings", json={
        "location_id": loc, "note": "INV-1", "supplier_id": sup["id"],
        "lines": [{"part_id": part["id"], "qty": 5, "purchase_price": 100, "markup_pct": 50}]}, headers=tenant)
    assert r.status_code == 201, r.text
    s = (await db.execute(select(Supplier).where(Supplier.id == sup["id"]))).scalar_one()
    assert s.balance == 500.0    # 5 × 100 added to what we owe the supplier
