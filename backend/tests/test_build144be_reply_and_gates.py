"""Build 144be — approved-line tax math, publish permission split, staff reply.

- an approved recommendation line carries the org's default tax (matches estimate math)
- publishing is gated by can_publish_recommend (separate from can_recommend)
- staff reply posts a client-visible message, gated by can_reply_customer + the toggle
"""
import uuid

import pytest


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Be Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def _order(client, H):
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={"name": "Neha"}, headers=H)).json()["id"]
    return (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "battery"}, headers=H)).json()


async def _emp_with_role(client, H, perms):
    role = (await client.post("/api/staff/roles", json={"name": "R", "permissions": perms}, headers=H)).json()
    email = f"e-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Emp", "email": email, "password": "pw-test-12345", "role_id": role["id"]}, headers=H)
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def test_approved_line_uses_same_math_as_manual_line(client):
    """#3: an approved recommendation must go through the SAME tax/line chain as a
    hand-added line, never a bare qty*price. Prove it by adding an identical manual
    service line and asserting the two lines get identical tax treatment + total."""
    H = await _tenant(client)
    # a default 10% sales tax so the auto-apply chain has something to stamp
    await client.post("/api/catalog/taxes", json={
        "name": "GST", "rate": 10, "mode": "imposed", "is_default": True}, headers=H)
    o = await _order(client, H)
    # hand-added service line, no explicit tax → auto-apply runs
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Manual diag", "unit_price": 100}, headers=H)
    # recommended service, same price, approved by customer
    rec = (await client.post(f"/api/orders/{o['id']}/recommendations", json={
        "title": "Rec diag", "unit_price": 100, "qty": 1, "kind": "service"}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/publish", json={}, headers=H)
    await client.post(f"/api/public/track/{o['public_token']}/recommendations/decide",
                      json={"approve_ids": [rec["id"]], "decline_ids": []})
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    manual = [ln for ln in got["lines"] if ln["description"] == "Manual diag"][0]
    approved = [ln for ln in got["lines"] if ln["description"] == "Rec diag"][0]
    # identical tax treatment + total → the recommendation reused the estimate math
    assert approved["tax_rate"] == manual["tax_rate"]
    assert approved["tax_mode"] == manual["tax_mode"]
    assert round(approved["total"], 2) == round(manual["total"], 2)


async def test_publish_requires_publish_permission(client):
    H = await _tenant(client)
    o = await _order(client, H)
    rec = (await client.post(f"/api/orders/{o['id']}/recommendations", json={
        "title": "Wipers", "unit_price": 20}, headers=H)).json()
    # a role that may recommend (draft) but NOT publish
    drafter = await _emp_with_role(client, H, {"can_recommend": True, "can_publish_recommend": False})
    # can add a draft
    add = await client.post(f"/api/orders/{o['id']}/recommendations", json={
        "title": "Coolant", "unit_price": 30}, headers=drafter)
    assert add.status_code == 201
    # cannot publish
    pub = await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/publish", json={}, headers=drafter)
    assert pub.status_code == 403


async def test_staff_reply_toggle_and_permission(client):
    H = await _tenant(client)
    o = await _order(client, H)
    # owner can reply → shows on the public track page
    r = await client.post(f"/api/orders/{o['id']}/customer-reply", json={"body": "On it, ready tomorrow."}, headers=H)
    assert r.status_code == 201
    track = (await client.get(f"/api/public/track/{o['public_token']}")).json()
    assert any((u.get("body") == "On it, ready tomorrow.") for u in track["updates"])
    # role without can_reply_customer is blocked
    noreply = await _emp_with_role(client, H, {"can_reply_customer": False})
    blocked = await client.post(f"/api/orders/{o['id']}/customer-reply", json={"body": "hi"}, headers=noreply)
    assert blocked.status_code == 403
    # turning the org toggle off blocks even the owner
    await client.put("/api/staff/public-pages", json={"oe_allow_staff_reply": False}, headers=H)
    off = await client.post(f"/api/orders/{o['id']}/customer-reply", json={"body": "x"}, headers=H)
    assert off.status_code == 403
    # order detail exposes the flag for the UI
    got = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert got["allow_staff_reply"] is False
