"""144aq — Purchasing Cockpit: exception-driven action worklist. Read-only aggregation; assert the
right POs/parts land in the right buckets."""
import pytest


@pytest.mark.asyncio
async def test_cockpit_buckets(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    sid = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "CK-1"}, headers=tenant)).json()["id"]

    # 1) Overdue: an open PO whose expected date is in the past, nothing received.
    await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "expected_at": "2020-01-01",
        "lines": [{"part_id": pid, "qty_ordered": 3, "unit_cost": 5}]})

    # 2) Awaiting invoice: received fully, no supplier invoice total yet.
    po2 = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc,
        "lines": [{"part_id": pid, "qty_ordered": 2, "unit_cost": 5}]})).json()["po_id"]
    line2 = (await client.get(f"/api/inventory/po/{po2}", headers=tenant)).json()["lines"][0]["id"]
    await client.post(f"/api/inventory/po/{po2}/receive", headers=tenant, json={"lines": [{"po_line_id": line2, "qty": 2}]})

    # 3) Below minimum: a part with a min-stock and no stock, not on an open PO.
    await client.post("/api/inventory/parts", json={"name": "Fuse", "sku": "CK-2", "min_stock": 10}, headers=tenant)

    c = (await client.get("/api/inventory/purchasing/cockpit", headers=tenant)).json()
    counts = c["counts"]
    assert counts["overdue"] >= 1
    assert counts["awaiting_invoice"] >= 1
    assert counts["below_min"] >= 1
    # overdue row carries days_late and supplier
    assert c["buckets"]["overdue"][0]["days_late"] > 0
    assert c["buckets"]["overdue"][0]["supplier"] == "Acme"
