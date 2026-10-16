"""ADR-144po — parts on a PO raised from a job are written off to that job when they arrive,
the job lists all its POs, and the job's people hear about PO changes."""
import uuid

import pytest
from sqlalchemy import select

from backend.models import OrderLine, StaffNotification, StockLevel


async def _setup(client, H, *, estimate=False, assignee=None):
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15550123"}, headers=H)).json()["id"]
    body = {"client_id": cid, "location_id": loc, "complaint": "needs part",
            "lines": [{"line_type": "part", "kind": "product", "description": "Rare board", "qty": 2,
                       "unit_price": 90, "cost_price": 60}]}
    if estimate:
        body["is_estimate"] = True
    if assignee:
        body["assigned_to_id"] = assignee
    oid = (await client.post("/api/orders", headers=H, json=body)).json()["id"]
    sid = (await client.post("/api/suppliers", json={"name": f"Acme {uuid.uuid4().hex[:4]}"}, headers=H)).json()["id"]
    po = (await client.post(f"/api/orders/{oid}/create-po", headers=H, json={"supplier_id": sid, "auto_send": True})).json()
    assert po["ok"], po
    return oid, po, loc


@pytest.mark.asyncio
async def test_arrival_is_written_off_to_the_job(client, tenant, db):
    oid, po, loc = await _setup(client, tenant)
    line = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalar_one()
    assert line.pending_stock == 2
    r = await client.post(f"/api/inventory/po/{po['po_id']}/receive", headers=tenant, json={})
    assert r.status_code == 200, r.text
    await db.refresh(line)
    assert line.pending_stock == 0
    sl = (await db.execute(select(StockLevel).where(StockLevel.part_id == line.ref_id,
                                                    StockLevel.location_id == loc))).scalar_one()
    assert sl.qty_available == 0                     # 2 in, 2 used by the job
    pos = (await client.get(f"/api/orders/{oid}/purchase-orders", headers=tenant)).json()["items"]
    assert len(pos) == 1 and pos[0]["status"] == "received" and pos[0]["raised_here"]
    assert pos[0]["lines"][0] == {"part": "Rare board", "ordered": 2, "received": 2, "outstanding": 0}
    feed = (await client.get(f"/api/feed/order/{oid}", headers=tenant)).text
    assert "written off to this job" in feed


@pytest.mark.asyncio
async def test_estimate_is_never_written_off(client, tenant, db):
    oid, po, loc = await _setup(client, tenant, estimate=True)
    await client.post(f"/api/inventory/po/{po['po_id']}/receive", headers=tenant, json={})
    line = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalar_one()
    sl = (await db.execute(select(StockLevel).where(StockLevel.part_id == line.ref_id,
                                                    StockLevel.location_id == loc))).scalar_one()
    assert sl.qty_available == 2                     # landed as free stock


@pytest.mark.asyncio
async def test_assignee_hears_about_po_changes(client, tenant, db):
    email = f"tech-{uuid.uuid4().hex[:6]}@test.dev"
    tech = (await client.post("/api/staff/employees", json={"name": "Tech", "email": email, "password": "pw-test-12345"},
                              headers=tenant)).json()
    oid, po, _ = await _setup(client, tenant, assignee=tech["id"])
    await client.post(f"/api/inventory/po/{po['po_id']}/cancel", headers=tenant)
    notes = (await db.execute(select(StaffNotification).where(StaffNotification.employee_id == tech["id"]))).scalars().all()
    assert any(po["po_number"] in n.title and "cancelled" in n.title for n in notes)
