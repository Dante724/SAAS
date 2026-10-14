"""144gj — status requirement gates: block entering a status until satisfied."""
import pytest


@pytest.mark.asyncio
async def test_require_comment_gate(client, tenant):
    H = tenant
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc.get("items") or loc)[0]["id"]

    # A status that requires a comment before it can be entered.
    st = await client.post("/api/statuses", json={
        "name": "Needs note", "status_group": "in_progress", "require_comment": True}, headers=H)
    assert st.status_code == 201, st.text
    key = st.json()["key"]

    o = (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)).json()

    # No comment yet → blocked.
    blocked = await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=H)
    assert blocked.status_code == 409, blocked.text
    assert "comment" in blocked.text.lower()

    # Add a comment → now allowed.
    c = await client.post(f"/api/orders/{o['id']}/comments", json={"body": "diagnosed"}, headers=H)
    assert c.status_code == 201, c.text
    ok = await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=H)
    assert ok.status_code == 200, ok.text
