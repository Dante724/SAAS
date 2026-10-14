"""144an — PO manual, configurable workflow stage (Orderry parity), decoupled from receipt.

The workflow stage is a human-driven, per-org configurable pipeline (record_type=purchase_order).
It is independent of the receipt state (PurchaseOrder.status): receiving moves stock and the
receipt state, never the workflow label, and a workflow label can never fake what was received.
"""
import pytest


async def _mk_po(client, tenant, draft=False, qty=6):
    sid = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "B-9"}, headers=tenant)).json()["id"]
    po_id = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "draft": draft,
        "lines": [{"part_id": pid, "qty_ordered": qty, "unit_cost": 2}]})).json()["po_id"]
    return po_id


@pytest.mark.asyncio
async def test_purchase_order_pipeline_seeded(client, tenant):
    r = await client.get("/api/statuses?record_type=purchase_order", headers=tenant)
    assert r.status_code == 200
    keys = {s["key"] for s in r.json()["items"]}
    assert {"po_draft", "po_ordered", "po_received", "po_cancelled"} <= keys


@pytest.mark.asyncio
async def test_manual_workflow_set_and_default(client, tenant):
    po_id = await _mk_po(client, tenant)                      # created non-draft → po_ordered
    d = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()
    assert d["workflow_status"] == "po_ordered"

    # move it through any manual stage
    r = await client.post(f"/api/inventory/po/{po_id}/workflow", json={"workflow_status": "po_in_transit"}, headers=tenant)
    assert r.status_code == 200
    d = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()
    assert d["workflow_status"] == "po_in_transit"

    # unknown stage is rejected
    bad = await client.post(f"/api/inventory/po/{po_id}/workflow", json={"workflow_status": "nope"}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_partial_receipt_leaves_manual_stage(client, tenant):
    """A partial receive moves the receipt state but never the manual workflow label."""
    po_id = await _mk_po(client, tenant, qty=4)
    await client.post(f"/api/inventory/po/{po_id}/workflow", json={"workflow_status": "po_delayed"}, headers=tenant)
    line_id = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()["lines"][0]["id"]
    r = await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant,
                          json={"lines": [{"po_line_id": line_id, "qty": 2}]})
    assert r.status_code == 200 and r.json()["status"] == "partial"
    d = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()
    assert d["status"] == "partial"               # receipt truth
    assert d["workflow_status"] == "po_delayed"   # manual label untouched by a partial receive


@pytest.mark.asyncio
async def test_full_receipt_auto_advances_workflow(client, tenant):
    """144ar — a full receive auto-advances a non-terminal manual stage to Received (convenience),
    while the receipt state remains the source of truth."""
    po_id = await _mk_po(client, tenant, qty=4)
    await client.post(f"/api/inventory/po/{po_id}/workflow", json={"workflow_status": "po_delayed"}, headers=tenant)
    line_id = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()["lines"][0]["id"]
    r = await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant,
                          json={"lines": [{"po_line_id": line_id, "qty": 4}]})
    assert r.status_code == 200 and r.json()["status"] == "received"
    d = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()
    assert d["status"] == "received" and d["workflow_status"] == "po_received"
