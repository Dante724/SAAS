"""Build 109 — FEFO batch depletion, expired-stock suggestions, and the public
status lookup that powers the embeddable widget."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import StockItemBatch
from backend.services.dates import now_utc


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _wh(client, H):
    items = (await client.get("/api/settings/warehouses?kind=product", headers=H)).json()["items"]
    return next(w for w in items if w["is_default"])


async def _part(client, H, sku="FEFO-1", cost=2):
    return (await client.post("/api/inventory/parts", json={
        "name": "Perishable", "sku": sku, "cost_price": cost, "sell_price": 5}, headers=H)).json()


async def _stock(client, H, part_id, qty):
    loc = await _loc(client, H)
    await client.post(f"/api/inventory/parts/{part_id}/adjust", json={
        "location_id": loc, "qty": qty, "movement_type": "receive"}, headers=H)


# ══ FEFO batch depletion ═════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_write_off_consumes_earliest_expiry_first(client, tenant, db):
    me = await _me(client, tenant)
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant)
    await _stock(client, tenant, p["id"], 30)

    # three batches, different expiries
    soon = StockItemBatch(org_id=me["org_id"], part_id=p["id"], warehouse_id=w["id"],
                          location_id=loc, unit_cost=2, qty_received=10, qty_remaining=10,
                          expiry_date=now_utc() + timedelta(days=5))
    mid = StockItemBatch(org_id=me["org_id"], part_id=p["id"], warehouse_id=w["id"],
                         location_id=loc, unit_cost=2, qty_received=10, qty_remaining=10,
                         expiry_date=now_utc() + timedelta(days=30))
    late = StockItemBatch(org_id=me["org_id"], part_id=p["id"], warehouse_id=w["id"],
                          location_id=loc, unit_cost=2, qty_received=10, qty_remaining=10,
                          expiry_date=now_utc() + timedelta(days=200))
    db.add_all([soon, mid, late])
    await db.commit()

    # write off 15 → should drain the soonest batch (10) then 5 from the middle one
    r = await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": w["id"], "reason": "expired",
        "lines": [{"part_id": p["id"], "qty": 15}]}, headers=tenant)
    assert r.status_code == 201, r.text

    for b in (soon, mid, late):
        await db.refresh(b)
    assert soon.qty_remaining == 0     # earliest fully consumed
    assert mid.qty_remaining == 5      # partially consumed
    assert late.qty_remaining == 10    # untouched — furthest expiry


@pytest.mark.asyncio
async def test_non_perishable_write_off_still_works(client, tenant):
    """A part with no tracked batches must write off fine — FEFO is a no-op for it."""
    w = await _wh(client, tenant)
    p = await _part(client, tenant, sku="PLAIN-1")
    await _stock(client, tenant, p["id"], 5)
    r = await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": w["id"], "reason": "damaged",
        "lines": [{"part_id": p["id"], "qty": 2}]}, headers=tenant)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_expired_stock_suggestions(client, tenant, db):
    me = await _me(client, tenant)
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="EXP-1", cost=3)
    db.add_all([
        StockItemBatch(org_id=me["org_id"], part_id=p["id"], warehouse_id=w["id"],
                       location_id=loc, unit_cost=3, qty_received=8, qty_remaining=8,
                       expiry_date=now_utc() - timedelta(days=2)),   # expired
        StockItemBatch(org_id=me["org_id"], part_id=p["id"], warehouse_id=w["id"],
                       location_id=loc, unit_cost=3, qty_received=4, qty_remaining=4,
                       expiry_date=now_utc() + timedelta(days=300)),  # fine
    ])
    await db.commit()

    r = (await client.get("/api/stock-docs/write-offs/suggestions", headers=tenant)).json()
    assert len(r["items"]) == 1                 # only the expired batch
    assert r["items"][0]["expired"] is True
    assert r["items"][0]["value_at_risk"] == pytest.approx(24.0)   # 8 × 3
    assert r["total_value"] == pytest.approx(24.0)

    # widen the window to catch the far-future batch too
    wide = (await client.get("/api/stock-docs/write-offs/suggestions?within_days=400",
                             headers=tenant)).json()
    assert len(wide["items"]) == 2


# ══ Public status lookup (widget backend) ════════════════════════════════════
@pytest.mark.asyncio
async def test_public_lookup_requires_matching_phone(client, tenant):
    loc = await _loc(client, tenant)
    c = (await client.post("/api/clients", json={
        "name": "Jo", "phone": "+1 (555) 123-4567"}, headers=tenant)).json()
    o = (await client.post("/api/orders", json={
        "location_id": loc, "client_id": c["id"], "complaint": "screen"}, headers=tenant)).json()

    # right number + right phone (format-agnostic) → token
    ok = await client.post("/api/public/lookup", json={
        "order_number": o["order_number"], "phone": "555-123-4567"})
    assert ok.status_code == 200 and ok.json()["token"]

    # right number, wrong phone → same 404 as unknown (no info leak)
    bad = await client.post("/api/public/lookup", json={
        "order_number": o["order_number"], "phone": "555-000-0000"})
    assert bad.status_code == 404

    # unknown order → 404
    missing = await client.post("/api/public/lookup", json={
        "order_number": "NOPE-999", "phone": "555-123-4567"})
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_lookup_token_renders_via_track(client, tenant):
    loc = await _loc(client, tenant)
    c = (await client.post("/api/clients", json={
        "name": "Kim", "phone": "5559998888"}, headers=tenant)).json()
    o = (await client.post("/api/orders", json={
        "location_id": loc, "client_id": c["id"], "complaint": "battery"}, headers=tenant)).json()

    tok = (await client.post("/api/public/lookup", json={
        "order_number": o["order_number"], "phone": "5559998888"})).json()["token"]
    track = await client.get(f"/api/public/track/{tok}")
    assert track.status_code == 200
    assert track.json()["order_number"] == o["order_number"]


@pytest.mark.asyncio
async def test_widget_js_served(client):
    r = await client.get("/api/public/widget.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert "repairos-status" in r.text and "lookup" in r.text
