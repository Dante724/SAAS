"""144ih — accept/decline gating per doc type + status (Orderry parity)."""


async def _mk(client, tenant, is_estimate):
    oid = (await client.post("/api/orders", json={
        "complaint": "x", "is_estimate": is_estimate}, headers=tenant)).json()["id"]
    tok = (await client.post(f"/api/orders/{oid}/public-link", headers=tenant)).json()["token"]
    return oid, tok


async def test_work_order_approval_off_by_default(client, tenant):
    _, tok = await _mk(client, tenant, is_estimate=False)
    # default: work orders are NOT signable
    t = (await client.get(f"/api/public/track/{tok}")).json()
    assert t["awaiting_approval"] is False
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept"})
    assert r.status_code == 403


async def test_work_order_approval_enabled(client, tenant):
    _, tok = await _mk(client, tenant, is_estimate=False)
    await client.put("/api/staff/public-pages", json={"oe_wo_allow_approval": True}, headers=tenant)
    t = (await client.get(f"/api/public/track/{tok}")).json()
    assert t["awaiting_approval"] is True
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "name": "Cap"})
    assert r.status_code == 200, r.text


async def test_accept_moves_to_configured_status(client, tenant):
    oid, tok = await _mk(client, tenant, is_estimate=False)
    cur = (await client.get(f"/api/public/track/{tok}")).json()["status"]
    sts = (await client.get("/api/statuses?is_estimate=false", headers=tenant)).json()["items"]
    target = next(s for s in sts if s["key"] != cur)
    await client.put("/api/staff/public-pages", json={
        "oe_wo_allow_approval": True, "oe_wo_accept_status": target["key"]}, headers=tenant)
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "name": "C"})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/public/track/{tok}")).json()["status"] == target["key"]


async def test_require_signature_blocks_accept_without_sig(client, tenant):
    _, tok = await _mk(client, tenant, is_estimate=False)
    await client.put("/api/staff/public-pages", json={
        "oe_wo_allow_approval": True, "oe_wo_require_signature": True}, headers=tenant)
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept"})
    assert r.status_code == 422
    ok = await client.post(f"/api/public/track/{tok}/respond", json={
        "decision": "accept", "signature": "data:image/png;base64,iVBORw0KGgo="})
    assert ok.status_code == 200, ok.text


async def test_estimate_status_allowlist(client, tenant):
    oid, tok = await _mk(client, tenant, is_estimate=True)
    # restrict estimate approval to a status this order is NOT in
    await client.put("/api/staff/public-pages", json={"oe_approval_statuses": "sent,approval"}, headers=tenant)
    t = (await client.get(f"/api/public/track/{tok}")).json()
    assert t["awaiting_approval"] is False
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept"})
    assert r.status_code == 403
    # blank list = any status → allowed again
    await client.put("/api/staff/public-pages", json={"oe_approval_statuses": ""}, headers=tenant)
    t2 = (await client.get(f"/api/public/track/{tok}")).json()
    assert t2["awaiting_approval"] is True
