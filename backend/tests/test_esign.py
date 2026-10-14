"""144ig — tamper-evident client e-signature on estimate + work order, certificate PDF."""

_PNG = ("data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


async def _sign(client, tenant, is_estimate):
    oid = (await client.post("/api/orders", json={
        "complaint": "Hull crack", "is_estimate": is_estimate}, headers=tenant)).json()["id"]
    if not is_estimate:
        # 144ih — work-order approval is opt-in; enable it so the customer can sign.
        await client.put("/api/staff/public-pages", json={"oe_wo_allow_approval": True}, headers=tenant)
    token = (await client.post(f"/api/orders/{oid}/public-link", headers=tenant)).json()["token"]
    r = await client.post(f"/api/public/track/{token}/respond", json={
        "decision": "accept", "signature": _PNG, "name": "Jane Boat"})
    assert r.status_code == 200, r.text
    return oid, token


async def test_estimate_signature_recorded(client, tenant):
    oid, token = await _sign(client, tenant, is_estimate=True)
    rows = (await client.get(f"/api/orders/{oid}/signatures", headers=tenant)).json()["items"]
    assert len(rows) == 1
    s = rows[0]
    assert s["doc_type"] == "estimate" and s["decision"] == "accept"
    assert s["signer_name"] == "Jane Boat"
    assert s["has_drawn"] is True
    assert s["doc_hash"] and len(s["doc_hash"]) == 64


async def test_work_order_signature_recorded(client, tenant):
    oid, _ = await _sign(client, tenant, is_estimate=False)
    rows = (await client.get(f"/api/orders/{oid}/signatures", headers=tenant)).json()["items"]
    assert len(rows) == 1 and rows[0]["doc_type"] == "work_order"


async def test_certificate_pdf_public_and_staff(client, tenant):
    oid, token = await _sign(client, tenant, is_estimate=True)
    # public token-gated certificate
    pub = await client.get(f"/api/public/track/{token}/certificate.pdf")
    assert pub.status_code == 200, pub.text
    assert pub.content[:4] == b"%PDF"
    # staff certificate
    staff = await client.get(f"/api/orders/{oid}/certificate.pdf", headers=tenant)
    assert staff.status_code == 200 and staff.content[:4] == b"%PDF"


async def test_track_exposes_signature_flag(client, tenant):
    _, token = await _sign(client, tenant, is_estimate=True)
    t = (await client.get(f"/api/public/track/{token}")).json()
    assert t["has_signature"] is True


async def test_certificate_404_when_unsigned(client, tenant):
    oid = (await client.post("/api/orders", json={"complaint": "x"}, headers=tenant)).json()["id"]
    r = await client.get(f"/api/orders/{oid}/certificate.pdf", headers=tenant)
    assert r.status_code == 404
