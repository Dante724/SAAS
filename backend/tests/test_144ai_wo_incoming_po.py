"""144ai — 'Waiting on PO' on a work order: parts on order for this WO show up, and
clear once the PO is fully received."""
import pytest
from sqlalchemy import select

from backend.models import Order, PurchaseOrder, Supplier


@pytest.mark.asyncio
async def test_wo_incoming_po_shows_then_clears(client, tenant, db):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15550999"}, headers=tenant)).json()["id"]
    # WO with a custom part line so create-po turns it into a real orderable part
    oid = (await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": loc, "complaint": "needs part",
        "lines": [{"line_type": "part", "kind": "product", "description": "Rare board", "qty": 1, "unit_price": 90, "cost_price": 60}],
    })).json()["id"]
    sid = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]

    # raise + send a PO for this WO (auto_send marks it ordered so it can be received;
    # receiving a still-draft PO is correctly refused). Sets PurchaseOrder.source_order_id.
    po = (await client.post(f"/api/orders/{oid}/create-po", headers=tenant,
                            json={"supplier_id": sid, "auto_send": True})).json()
    assert po["ok"], po

    inc = (await client.get(f"/api/orders/{oid}/incoming-po", headers=tenant)).json()["items"]
    assert len(inc) == 1 and inc[0]["po_number"] == po["po_number"]
    assert inc[0]["parts"][0]["outstanding"] >= 1

    # receive the PO fully → WO no longer waiting
    await client.post(f"/api/inventory/po/{po['po_id']}/receive", headers=tenant, json={})
    inc2 = (await client.get(f"/api/orders/{oid}/incoming-po", headers=tenant)).json()["items"]
    assert inc2 == []
