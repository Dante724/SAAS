"""144gr — a custom field value can block entering a status until it clears."""
import pytest


async def _loc(client, H):
    b = (await client.get("/api/staff/locations", headers=H)).json()
    return (b.get("items") or b)[0]["id"]


@pytest.mark.asyncio
async def test_field_gates_status(client, tenant):
    H = tenant
    loc_id = await _loc(client, H)
    t = (await client.post("/api/forms/types", json={"name": "Repair"}, headers=H)).json()
    # A status to gate, and a field that blocks it when severity = severe.
    st = await client.post("/api/statuses", json={"name": "Ready", "status_group": "ready"}, headers=H)
    skey = st.json()["key"]
    fr = await client.post(f"/api/forms/types/{t['id']}/fields", json={
        "field_label": "Damage severity", "field_type": "text",
        "gate_status_key": skey, "gate_comparator": "is", "gate_value": "severe"}, headers=H)
    assert fr.status_code == 201, fr.text
    key = fr.json()["field_key"]

    # severe → blocked.
    o = (await client.post("/api/orders", json={
        "location_id": loc_id, "type_id": t["id"], "complaint": "x",
        "custom_data": {key: "severe"}}, headers=H)).json()
    blocked = await client.patch(f"/api/orders/{o['id']}/status", json={"status": skey}, headers=H)
    assert blocked.status_code == 409, blocked.text
    assert "blocks" in blocked.text.lower()

    # minor → allowed.
    o2 = (await client.post("/api/orders", json={
        "location_id": loc_id, "type_id": t["id"], "complaint": "y",
        "custom_data": {key: "minor"}}, headers=H)).json()
    ok = await client.patch(f"/api/orders/{o2['id']}/status", json={"status": skey}, headers=H)
    assert ok.status_code == 200, ok.text
