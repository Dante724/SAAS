"""Build 144bd — recommendations (upsell checklist the customer approves).

Covers the staff draft→publish flow, the role gate, the customer approve/decline
(single + multi) turning approved items into order lines, the notif dot, org scoping,
and the public-pages toggle.
"""
import uuid

import pytest


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Rec Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def _order(client, H):
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    return (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "screen"}, headers=H)).json()


async def _add_rec(client, H, oid, title="Brake pads", price=80, kind="service"):
    r = await client.post(f"/api/orders/{oid}/recommendations", json={
        "title": title, "unit_price": price, "qty": 1, "kind": kind}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def test_draft_publish_and_customer_sees_only_published(client):
    H = await _tenant(client)
    o = await _order(client, H)
    r1 = await _add_rec(client, H, o["id"], "Brake pads", 80)
    await _add_rec(client, H, o["id"], "Coolant flush", 45)
    # both are drafts → customer sees none yet
    track = (await client.get(f"/api/public/track/{o['public_token']}")).json()
    assert track["recommendations"] == []
    # publish one → customer sees exactly that one, status sent
    await client.post(f"/api/orders/{o['id']}/recommendations/{r1['id']}/publish", json={}, headers=H)
    track = (await client.get(f"/api/public/track/{o['public_token']}")).json()
    sent = [x for x in track["recommendations"] if x["status"] == "sent"]
    assert len(sent) == 1 and sent[0]["title"] == "Brake pads"


async def test_approve_multi_creates_lines_and_bumps_dot(client):
    H = await _tenant(client)
    o = await _order(client, H)
    a = await _add_rec(client, H, o["id"], "Brake pads", 80)
    b = await _add_rec(client, H, o["id"], "Coolant flush", 45)
    await client.post(f"/api/orders/{o['id']}/recommendations/publish-all", json={}, headers=H)
    # customer approves both
    r = await client.post(f"/api/public/track/{o['public_token']}/recommendations/decide",
                          json={"approve_ids": [a["id"], b["id"]], "decline_ids": []})
    assert r.status_code == 200 and r.json()["approved"] == 2
    # both became order lines
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    descs = [ln["description"] for ln in got["lines"]]
    assert "Brake pads" in descs and "Coolant flush" in descs
    # notif dot bumped, then cleared on open
    assert got["rec_unread"] == 2
    await client.post(f"/api/orders/{o['id']}/recommendations/seen", json={}, headers=H)
    again = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert again["rec_unread"] == 0


async def test_decline_records_no_line(client):
    H = await _tenant(client)
    o = await _order(client, H)
    a = await _add_rec(client, H, o["id"], "Wiper blades", 22)
    await client.post(f"/api/orders/{o['id']}/recommendations/{a['id']}/publish", json={}, headers=H)
    r = await client.post(f"/api/public/track/{o['public_token']}/recommendations/decide",
                          json={"approve_ids": [], "decline_ids": [a["id"]]})
    assert r.json()["declined"] == 1
    items = (await client.get(f"/api/orders/{o['id']}/recommendations", headers=H)).json()["items"]
    assert items[0]["status"] == "declined"
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert all(ln["description"] != "Wiper blades" for ln in got["lines"])


async def test_role_gate_blocks_recommend(client):
    H = await _tenant(client)
    o = await _order(client, H)
    # a role that explicitly forbids recommending
    role = (await client.post("/api/staff/roles", json={
        "name": "Viewer", "permissions": {"can_recommend": False}}, headers=H)).json()
    jr_email = f"jr-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Jr", "email": jr_email,
        "password": "pw-test-12345", "role_id": role["id"]}, headers=H)
    login = (await client.post("/api/auth/token", data={
        "username": jr_email, "password": "pw-test-12345"})).json()
    JH = {"Authorization": f"Bearer {login['access_token']}"}
    r = await client.post(f"/api/orders/{o['id']}/recommendations", json={
        "title": "X", "unit_price": 10}, headers=JH)
    assert r.status_code == 403


async def test_toggle_off_hides_and_blocks(client):
    H = await _tenant(client)
    o = await _order(client, H)
    a = await _add_rec(client, H, o["id"], "Brake pads", 80)
    await client.post(f"/api/orders/{o['id']}/recommendations/{a['id']}/publish", json={}, headers=H)
    # turn the feature off
    await client.put("/api/staff/public-pages", json={"oe_allow_recommend": False}, headers=H)
    track = (await client.get(f"/api/public/track/{o['public_token']}")).json()
    assert track["allow_recommend"] is False
    assert track["recommendations"] == []
    r = await client.post(f"/api/public/track/{o['public_token']}/recommendations/decide",
                          json={"approve_ids": [a["id"]], "decline_ids": []})
    assert r.status_code == 403


async def test_cross_org_cannot_touch_recommendations(client):
    A = await _tenant(client)
    B = await _tenant(client)
    o = await _order(client, A)
    a = await _add_rec(client, A, o["id"], "Brake pads", 80)
    # org B can't list or add on org A's order
    assert (await client.get(f"/api/orders/{o['id']}/recommendations", headers=B)).status_code == 404
    assert (await client.post(f"/api/orders/{o['id']}/recommendations/{a['id']}/publish",
                              json={}, headers=B)).status_code == 404
