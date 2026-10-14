"""144dd — the WO drawer tab dots must reflect real state, not be always-on. The order detail
payload carries the two signals the badges read: client_msg_unread (Activity) and a new
checklists_pending flag (Checklists = a required checklist item still undone)."""
import pytest
from sqlalchemy import select


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_checklists_pending_reflects_required_undone(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                            headers=tenant)).json()
    # no checklist yet → not pending
    d0 = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert d0["checklists_pending"] is False

    # attach a checklist with one required item → pending
    tpl = (await client.post("/api/checklist-templates", json={
        "name": "QC", "steps": [{"title": "S", "sections": [{"title": "Sec", "items": [
            {"label": "Screen OK", "field_kind": "check_result", "required": True}]}]}]}, headers=tenant)).json()
    att = (await client.post(f"/api/orders/{o['id']}/checklists", json={"template_id": tpl["id"]}, headers=tenant)).json()
    items = (await client.get(f"/api/orders/{o['id']}/checklists/{att['id']}", headers=tenant)).json()["items"]
    d1 = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert d1["checklists_pending"] is True

    # complete the required item → not pending
    await client.patch(f"/api/orders/{o['id']}/checklists/{att['id']}/items/{items[0]['id']}",
                       json={"value": "pass"}, headers=tenant)
    d2 = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert d2["checklists_pending"] is False


@pytest.mark.asyncio
async def test_payment_due_only_when_issued_invoice_unpaid(client, tenant, db):
    from backend.models import Invoice
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                            headers=tenant)).json()
    # no invoice → not due
    assert (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["payment_due"] is False

    from backend.models import Order as _O
    org = (await db.execute(select(_O.org_id).where(_O.id == o["id"]))).scalar_one()
    # an issued, unpaid invoice → due
    inv = Invoice(org_id=org, order_id=o["id"], invoice_number="INV-DD-1", status="issued", total=100, amount_paid=0)
    db.add(inv)
    await db.commit()
    assert (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["payment_due"] is True

    # fully paid → not due
    inv.amount_paid = 100
    await db.commit()
    assert (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["payment_due"] is False


@pytest.mark.asyncio
async def test_list_rows_carry_checklists_pending_batched(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    plain = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "a"},
                                headers=tenant)).json()
    withcl = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "b"},
                                 headers=tenant)).json()
    tpl = (await client.post("/api/checklist-templates", json={
        "name": "QC", "steps": [{"title": "S", "sections": [{"title": "Sec", "items": [
            {"label": "X", "field_kind": "check_result", "required": True}]}]}]}, headers=tenant)).json()
    await client.post(f"/api/orders/{withcl['id']}/checklists", json={"template_id": tpl["id"]}, headers=tenant)

    rows = {r["id"]: r for r in (await client.get("/api/orders?limit=50", headers=tenant)).json()["items"]}
    assert rows[withcl["id"]]["checklists_pending"] is True
    assert rows[plain["id"]]["checklists_pending"] is False


@pytest.mark.asyncio
async def test_detail_carries_client_msg_unread(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                            headers=tenant)).json()
    token = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["public_token"]
    assert (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["client_msg_unread"] == 0
    # a customer message bumps it
    await client.post(f"/api/public/track/{token}/message", json={"body": "hi"})
    assert (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()["client_msg_unread"] == 1
