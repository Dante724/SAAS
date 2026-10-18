"""Phase 2 — universal scan resolve routes a scanned code to the right record."""


async def test_resolve_order_by_number_and_barcode(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Scan Co", "phone": "+15550888"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "scan me"})).json()

    # by order number
    r = await client.post("/api/scan/resolve", json={"code": o["order_number"]}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] and body["type"] == "order" and body["id"] == o["id"] and body["route"] == "/orders"

    # by auto-assigned barcode (case-insensitive)
    if o.get("barcode"):
        r2 = await client.post("/api/scan/resolve", json={"code": o["barcode"].lower()}, headers=tenant)
        assert r2.json()["type"] == "order" and r2.json()["id"] == o["id"]


async def test_resolve_product_by_sku(client, tenant):
    p = (await client.post("/api/inventory/parts", headers=tenant,
                           json={"name": "Brake pad", "sku": "BP-9001", "barcode": "550100"})).json()
    pid = p.get("id") or p.get("part", {}).get("id")
    r = await client.post("/api/scan/resolve", json={"code": "BP-9001"}, headers=tenant)
    assert r.status_code == 200 and r.json()["type"] == "product", r.text
    if pid:
        assert r.json()["id"] == pid
    # by barcode too
    assert (await client.post("/api/scan/resolve", json={"code": "550100"}, headers=tenant)).json()["type"] == "product"


async def test_unmatched_code(client, tenant):
    r = await client.post("/api/scan/resolve", json={"code": "NOPE-ZZZ-404"}, headers=tenant)
    assert r.status_code == 200 and r.json()["matched"] is False


async def test_resolve_tracking_link_qr(client, tenant, db):
    """The customer ticket's 'public page' QR encodes the tracking URL; scanning it opens the job."""
    from sqlalchemy import select

    from backend.models import Order
    o = (await client.post("/api/orders", headers=tenant, json={"complaint": "ticket qr"})).json()
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    if not row.public_token:
        row.public_token = "tok_scan_test_123456"
        await db.commit()
    url = f"https://shop.example.com/track/{row.public_token}"
    r = await client.post("/api/scan/resolve", json={"code": url}, headers=tenant)
    assert r.status_code == 200 and r.json()["type"] == "order" and r.json()["id"] == o["id"], r.text
    # an unknown token falls through to the normal lookups and stays unmatched
    r2 = await client.post("/api/scan/resolve", json={"code": "https://x/track/doesnotexist99"}, headers=tenant)
    assert r2.json()["matched"] is False
