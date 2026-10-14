"""Build 115 — group invoice: one invoice combining several orders per client."""
import pytest
from sqlalchemy import func, select

from backend.models import Invoice


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_group_invoice_combines_orders_per_client(client, tenant, db):
    loc = await _loc(client, tenant)
    c = (await client.post("/api/clients", json={"name": "Grouped", "phone": "+15559100"}, headers=tenant)).json()
    ids = []
    for _ in range(3):
        o = (await client.post("/api/orders", json={
            "location_id": loc, "client_id": c["id"], "complaint": "job"}, headers=tenant)).json()
        ids.append(o["id"])

    r = await client.post("/api/orders/bulk", json={
        "order_ids": ids, "action": "group_invoice", "value": ""}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1          # one invoice for the one client

    invs = (await db.execute(select(func.count()).select_from(Invoice).where(
        Invoice.client_id == c["id"]))).scalar()
    assert invs == 1


@pytest.mark.asyncio
async def test_group_invoice_separates_by_client(client, tenant, db):
    loc = await _loc(client, tenant)
    c1 = (await client.post("/api/clients", json={"name": "C1", "phone": "+15559200"}, headers=tenant)).json()
    c2 = (await client.post("/api/clients", json={"name": "C2", "phone": "+15559201"}, headers=tenant)).json()
    ids = []
    for cid in (c1["id"], c1["id"], c2["id"]):
        o = (await client.post("/api/orders", json={
            "location_id": loc, "client_id": cid, "complaint": "job"}, headers=tenant)).json()
        ids.append(o["id"])

    r = await client.post("/api/orders/bulk", json={
        "order_ids": ids, "action": "group_invoice", "value": ""}, headers=tenant)
    assert r.json()["updated"] == 2          # one invoice each for two clients
