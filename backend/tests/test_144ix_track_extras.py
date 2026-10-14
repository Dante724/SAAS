"""144ix — public track page extras: branding + pickup keys, review capture, lookup."""


async def _order_with_token(client, tenant):
    oid = (await client.post("/api/orders", json={"complaint": "screen"}, headers=tenant)).json()["id"]
    tok = (await client.post(f"/api/orders/{oid}/public-link", headers=tenant)).json()["token"]
    return oid, tok


async def test_track_payload_has_branding_and_pickup_keys(client, tenant):
    _, tok = await _order_with_token(client, tenant)
    body = (await client.get(f"/api/public/track/{tok}")).json()
    # branding + pickup + feedback keys are always present (values may be null)
    for k in ("logo_data", "brand_color", "website", "shop_address", "shop_hours",
              "location_name", "shop_phone", "feedback"):
        assert k in body, f"missing {k}"


async def test_review_capture_end_to_end(client, tenant):
    _, tok = await _order_with_token(client, tenant)
    # feedback off → track exposes feedback:null and submit is refused
    assert (await client.get(f"/api/public/track/{tok}")).json()["feedback"] is None
    r = await client.post(f"/api/public/track/{tok}/feedback", json={"rating": 5})
    assert r.status_code == 403

    # turn feedback on with a public review link
    put = await client.put("/api/feedback/settings", json={
        "system": "five_point", "active": True,
        "google_review_url": "https://g.page/r/demo/review",
        "bands": {"high": "Thank you! We'd love a public review."},
    }, headers=tenant)
    assert put.status_code == 200, put.text

    # now the track payload advertises the active feedback block
    fb = (await client.get(f"/api/public/track/{tok}")).json()["feedback"]
    assert fb and fb["system"] == "five_point"
    assert fb["google_review_url"] == "https://g.page/r/demo/review"
    assert fb["submitted"] is False

    # submit a 5 → message + review link; row is created on the fly
    sub = await client.post(f"/api/public/track/{tok}/feedback", json={"rating": 5, "comment": "great"})
    assert sub.status_code == 200, sub.text
    assert sub.json()["google_review_url"] == "https://g.page/r/demo/review"
    assert "review" in sub.json()["message"].lower()

    # idempotent: a second submit is refused, and the payload now shows submitted
    assert (await client.post(f"/api/public/track/{tok}/feedback", json={"rating": 4})).status_code == 409
    fb2 = (await client.get(f"/api/public/track/{tok}")).json()["feedback"]
    assert fb2["submitted"] is True and fb2["rating"] == 5


async def test_lookup_requires_matching_phone(client, tenant):
    # wrong number/phone → 404 (never confirms existence)
    r = await client.post("/api/public/lookup", json={"order_number": "WO-99999", "phone": "0000000000"})
    assert r.status_code == 404


async def test_status_widget_served(client):
    r = await client.get("/api/public/widget.js")
    assert r.status_code == 200
    assert "check your repair status".lower() in r.text.lower()
