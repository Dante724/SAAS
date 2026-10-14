"""144gp Phase B — a custom-field value auto-drafts an upsell through the shared
rule surface (same engine as checklist items)."""
import pytest


async def _loc(client, H):
    r = await client.get("/api/staff/locations", headers=H)
    b = r.json()
    return (b.get("items") or b)[0]["id"]


@pytest.mark.asyncio
async def test_field_below_threshold_drafts_upsell(client, tenant):
    H = tenant
    loc_id = await _loc(client, H)
    # A work-order type with a "battery health" field that upsells under 80.
    t = (await client.post("/api/forms/types", json={"name": "Phone repair"}, headers=H)).json()
    fr = await client.post(f"/api/forms/types/{t['id']}/fields", json={
        "field_label": "Battery health", "field_type": "number",
        "upsell_kind": "custom", "upsell_price": 59.0,
        "upsell_comparator": "lt", "upsell_threshold": 80}, headers=H)
    assert fr.status_code == 201, fr.text
    key = fr.json()["field_key"]

    # Order with battery 71 → drafts a field-sourced upsell.
    o = (await client.post("/api/orders", json={
        "location_id": loc_id, "type_id": t["id"], "complaint": "slow",
        "custom_data": {key: "71"}}, headers=H)).json()
    recs = (await client.get(f"/api/orders/{o['id']}/recommendations", headers=H)).json()
    items = recs.get("items", recs) if isinstance(recs, dict) else recs
    field_recs = [r for r in items if r.get("source") == "field"]
    assert len(field_recs) == 1, items
    assert field_recs[0]["unit_price"] == 59.0
    assert field_recs[0]["status"] == "draft"

    # Order with battery 95 → nothing drafted.
    o2 = (await client.post("/api/orders", json={
        "location_id": loc_id, "type_id": t["id"], "complaint": "fine",
        "custom_data": {key: "95"}}, headers=H)).json()
    recs2 = (await client.get(f"/api/orders/{o2['id']}/recommendations", headers=H)).json()
    items2 = recs2.get("items", recs2) if isinstance(recs2, dict) else recs2
    assert [r for r in items2 if r.get("source") == "field"] == []
