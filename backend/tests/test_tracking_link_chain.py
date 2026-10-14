async def test_public_tracking_link_end_to_end(client, tenant):
    # 1) create a work order
    oid = (await client.post("/api/orders", json={"complaint": "screen"}, headers=tenant)).json()["id"]
    # 2) staff mints/returns the public link (idempotent)
    r1 = await client.post(f"/api/orders/{oid}/public-link", headers=tenant)
    assert r1.status_code == 200, r1.text
    tok = r1.json()["token"]
    assert tok and r1.json()["path"] == f"/track/{tok}"
    # idempotent — same token second time
    r2 = await client.post(f"/api/orders/{oid}/public-link", headers=tenant)
    assert r2.json()["token"] == tok
    # 3) the public (unauthenticated) tracking payload resolves by token
    pub = await client.get(f"/api/public/track/{tok}")
    assert pub.status_code == 200, pub.text
    body = pub.json()
    assert body.get("status") is not None
    # 4) order detail exposes the token so the drawer can build the link
    det = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert det["public_token"] == tok
