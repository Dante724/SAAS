"""B2 — audit trail + GDPR export/erasure."""
import pytest
from sqlalchemy import select


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_audit_records_mutations(client, tenant):
    loc = await _loc(client, tenant)
    # create a client + order + status change -> three audited actions
    cid = (await client.post("/api/clients", json={"name": "Audited Co"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"location_id": loc, "client_id": cid, "complaint": "x"}, headers=tenant)).json()
    await client.post("/api/statuses", json={"name": "Done AU", "key": "done-au",
                                             "status_group": "closed", "is_terminal": True}, headers=tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done-au"}, headers=tenant)

    audit = (await client.get("/api/compliance/audit", headers=tenant)).json()["items"]
    actions = {a["action"] for a in audit}
    assert {"client.create", "order.create", "order.status_change"} <= actions
    # status change diff captured
    sc = next(a for a in audit if a["action"] == "order.status_change")
    assert sc["diff"]["status"][1] == "done-au"
    # filter by entity
    only_order = (await client.get(f"/api/compliance/audit?entity_type=order&entity_id={o['id']}", headers=tenant)).json()["items"]
    assert all(a["entity_type"] == "order" for a in only_order) and len(only_order) >= 2


@pytest.mark.asyncio
async def test_gdpr_export(client, tenant, db):
    from backend.models import Device
    cid = (await client.post("/api/clients",
                             json={"name": "Jane Subject", "phone": "555-9", "email": "jane@x.dev"}, headers=tenant)).json()["id"]
    dev = Device(client_id=cid, owner_type="client", asset_type="Phone", brand="Apple", model="15", imei="359...")
    db.add(dev)
    await db.commit()
    loc = await _loc(client, tenant)
    await client.post("/api/orders", json={"location_id": loc, "client_id": cid, "complaint": "x"}, headers=tenant)

    exp = (await client.get(f"/api/compliance/gdpr/client/{cid}/export", headers=tenant)).json()
    assert exp["client"]["name"] == "Jane Subject" and exp["client"]["email"] == "jane@x.dev"
    assert len(exp["assets"]) == 1 and exp["assets"][0]["imei"] == "359..."
    assert len(exp["orders"]) == 1
    # the export itself is audited
    audit = (await client.get("/api/compliance/audit?action=gdpr.export", headers=tenant)).json()["items"]
    assert any(a["entity_id"] == cid for a in audit)


@pytest.mark.asyncio
async def test_gdpr_erase_anonymizes_but_keeps_financials(client, tenant, db):
    from backend.models import Client as ClientM
    from backend.models import Device, Order
    cid = (await client.post("/api/clients",
                             json={"name": "Erase Me", "phone": "555-0", "email": "e@x.dev",
                                   "address": "1 St"}, headers=tenant)).json()["id"]
    dev = Device(client_id=cid, owner_type="client", asset_type="Phone", serial="SER-123", imei="IMEI-9")
    db.add(dev)
    await db.commit()
    loc = await _loc(client, tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "client_id": cid, "complaint": "x"}, headers=tenant)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 200}, headers=tenant)

    r = await client.post(f"/api/compliance/gdpr/client/{cid}/erase", headers=tenant)
    assert r.status_code == 200 and r.json()["asset_fields_scrubbed"] >= 2

    db.expire_all()
    cl = (await db.execute(select(ClientM).where(ClientM.id == cid))).scalar_one()
    assert cl.name.startswith("[erased") and cl.phone is None and cl.email is None and cl.address is None
    d = (await db.execute(select(Device).where(Device.client_id == cid))).scalar_one()
    assert d.serial is None and d.imei is None
    # financial record (order total) is retained
    ordr = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    assert ordr.total == pytest.approx(200.0)
    # erasure is audited
    audit = (await client.get("/api/compliance/audit?action=gdpr.erase", headers=tenant)).json()["items"]
    assert any(a["entity_id"] == cid for a in audit)
