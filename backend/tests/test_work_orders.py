"""Work Order Management — end-to-end coverage.

Exercises the full domain against the real app: lifecycle, status pipeline,
automation triggers, estimates + approval, signatures, scheduling/calendar,
attachments, activity timeline, warranty, and the customer portal.
"""
import pytest

from backend.services.dates import now_utc


# ── helpers ────────────────────────────────────────────────
async def _loc(client, h):
    r = await client.get("/api/staff/locations", headers=h)
    return r.json()["items"][0]["id"]


async def _order(client, h, **kw):
    body = {"location_id": await _loc(client, h), "complaint": "cracked screen", "duration_min": 30}
    body.update(kw)
    r = await client.post("/api/orders", json=body, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _line(client, h, oid, **kw):
    body = {"line_type": "labour", "kind": "custom", "description": "Repair", "qty": 1, "unit_price": 1000}
    body.update(kw)
    r = await client.post(f"/api/orders/{oid}/lines", json=body, headers=h)
    return r


async def _client_record(client, h, name="Jane Doe"):
    r = await client.post("/api/clients", json={"name": name, "phone": "+1999"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── lifecycle ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_lifecycle_create_line_status_close_reopen(client, tenant):
    o = await _order(client, tenant)
    assert o["order_number"].startswith(("WO", "E"))
    assert (await _line(client, tenant, o["id"])).status_code in (200, 201)

    # advance through the pipeline
    for st in ("in_repair", "done"):
        r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": st}, headers=tenant)
        assert r.status_code == 200, r.text

    got = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert got["status"] == "done"
    assert got["closed_at"] is not None      # terminal status set closed_at

    # reopen clears the closed state
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "in_repair"}, headers=tenant)
    assert r.status_code == 200


# ── status pipeline ────────────────────────────────────────
@pytest.mark.asyncio
async def test_status_pipeline_seeded(client, tenant):
    statuses = (await client.get("/api/statuses?is_estimate=false", headers=tenant)).json()
    rows = statuses if isinstance(statuses, list) else statuses.get("items", [])
    assert len(rows) >= 3
    assert any(s.get("is_default") for s in rows)
    assert any(s.get("status_group") == "closed" for s in rows)

    est = (await client.get("/api/statuses?is_estimate=true", headers=tenant)).json()
    est_rows = est if isinstance(est, list) else est.get("items", [])
    assert any(s.get("converts_to_order") for s in est_rows)   # an "order created" estimate status exists


# ── automation triggers ────────────────────────────────────
@pytest.mark.asyncio
async def test_automation_invoice_and_edit_lock(client, tenant):
    # a status that auto-creates an invoice and locks edits
    made = await client.post("/api/statuses", json={
        "name": "Invoiced", "key": "invoiced", "status_group": "in_progress",
        "triggers_invoice": True, "locks_edits": True}, headers=tenant)
    assert made.status_code in (200, 201), made.text

    o = await _order(client, tenant)
    await _line(client, tenant, o["id"])
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "invoiced"}, headers=tenant)
    assert r.status_code == 200, r.text

    invoices = (await client.get(f"/api/cash/orders/{o['id']}/invoices", headers=tenant)).json()
    assert len(invoices["invoices"]) >= 1             # triggers_invoice fired

    locked = await _line(client, tenant, o["id"])     # locks_edits should block further edits
    assert locked.status_code == 409


# ── estimates ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_estimate_convert_endpoint(client, tenant):
    est = await _order(client, tenant, is_estimate=True)
    await _line(client, tenant, est["id"])
    r = await client.post(f"/api/orders/{est['id']}/convert", json={}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    wos = (await client.get("/api/orders?is_estimate=false", headers=tenant)).json()["items"]
    assert len(wos) >= 1


# ── attachments + activity timeline ────────────────────────
@pytest.mark.asyncio
async def test_attachments_and_timeline(client, tenant):
    o = await _order(client, tenant)
    # attach a photo
    att = await client.post(f"/api/feed/order/{o['id']}", json={
        "item_type": "photo", "media_data": "data:image/png;base64,iVBORw0KGgo=",
        "media_name": "device.png", "body": "Photo of device"}, headers=tenant)
    assert att.status_code in (200, 201), att.text
    # a status change records a system event
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "in_repair"}, headers=tenant)

    feed = (await client.get(f"/api/feed/order/{o['id']}", headers=tenant)).json()
    items = feed if isinstance(feed, list) else feed.get("items", [])
    types = {i["item_type"] for i in items}
    assert "photo" in types
    assert "system" in types                          # timeline captured the status change


# ── scheduling + calendar ──────────────────────────────────
@pytest.mark.asyncio
async def test_scheduling_and_calendar(client, tenant):
    o = await _order(client, tenant)
    r = await client.patch(f"/api/orders/{o['id']}/schedule", json={
        "scheduled_for": "2026-07-01T10:00:00", "scheduled_to": "2026-07-01T11:00:00"}, headers=tenant)
    assert r.status_code == 200, r.text
    got = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert got["scheduled_for"] is not None

    cal = (await client.get(
        "/api/orders/scheduler/range?from_str=2026-06-30T00:00:00&to_str=2026-07-02T00:00:00",
        headers=tenant)).json()
    blob = str(cal)
    assert o["id"] in blob or o["order_number"] in blob   # the order shows on the calendar range


# ── warranty ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_warranty_activates_on_close(client, tenant):
    o = await _order(client, tenant)
    await _line(client, tenant, o["id"], warranty_days=90)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=tenant)
    got = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert got["warranty_until"] is not None          # warranty activated from the line's 90 days


# ── signatures ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_signature_capture(client, tenant):
    o = await _order(client, tenant)
    r = await client.post(f"/api/orders/{o['id']}/signature", json={
        "signature": "data:image/png;base64,iVBORw0KGgo=", "note": "Collected by customer"}, headers=tenant)
    assert r.status_code == 200, r.text
    feed = (await client.get(f"/api/feed/order/{o['id']}", headers=tenant)).json()
    items = feed if isinstance(feed, list) else feed.get("items", [])
    assert any(i["item_type"] == "signature" for i in items)


# ── customer portal ────────────────────────────────────────
@pytest.mark.asyncio
async def test_customer_portal_lists_and_detail(client, tenant):
    cid = await _client_record(client, tenant)
    o = await _order(client, tenant, client_id=cid)
    await _line(client, tenant, o["id"])

    link = await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)
    assert link.status_code == 200, link.text
    token = link.json()["portal_token"]

    home = (await client.get(f"/api/public/portal/{token}")).json()   # no auth header — public
    assert home["client_name"]
    assert any(w["id"] == o["id"] for w in home["work_orders"])

    detail = (await client.get(f"/api/public/portal/{token}/order/{o['id']}")).json()
    assert detail["order_number"] == o["order_number"]
    assert len(detail["lines"]) >= 1


@pytest.mark.asyncio
async def test_portal_estimate_approval_converts(client, tenant):
    await client.get("/api/statuses?is_estimate=true", headers=tenant)   # seed estimate statuses
    cid = await _client_record(client, tenant, "Bob")
    est = await _order(client, tenant, client_id=cid, is_estimate=True)
    await _line(client, tenant, est["id"])
    token = (await client.post(f"/api/clients/{cid}/portal-link", headers=tenant)).json()["portal_token"]

    detail = (await client.get(f"/api/public/portal/{token}/order/{est['id']}")).json()
    assert detail["can_approve"] is True

    resp = await client.post(f"/api/public/portal/{token}/order/{est['id']}/respond",
                             json={"decision": "accept", "signature": "data:image/png;base64,iVBORw0KGgo="})
    assert resp.status_code == 200, resp.text
    assert resp.json()["converted_order_id"]          # accept auto-converted to a work order


@pytest.mark.asyncio
async def test_portal_invalid_token_is_404(client):
    assert (await client.get("/api/public/portal/nope-not-a-real-token")).status_code == 404


# ── SLA automation (per-status time_limit_min) ─────────────
@pytest.mark.asyncio
async def test_sla_breach_detection_and_sweep(client, tenant, db):
    from datetime import datetime, timedelta

    from backend.services.sla import find_breaches, sweep_breaches

    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]

    # A work status with a 30-minute time limit.
    st = await client.post("/api/statuses", json={
        "name": "Under SLA", "status_group": "in_progress", "time_limit_min": 30}, headers=tenant)
    assert st.status_code == 201, st.text
    key = st.json()["key"]

    o = (await client.post("/api/orders",
                           json={"location_id": loc, "complaint": "sla test", "duration_min": 30},
                           headers=tenant)).json()
    moved = await client.patch(f"/api/orders/{o['id']}/status", json={"status": key}, headers=tenant)
    assert moved.status_code == 200, moved.text

    # Just entered the status -> no breach yet.
    assert await find_breaches(db, org_id) == []

    # 31 minutes later -> breach surfaces with the right arithmetic.
    future = now_utc() + timedelta(minutes=31)
    breaches = await find_breaches(db, org_id, now=future)
    mine = [b for b in breaches if b["order_id"] == o["id"]]
    assert mine and mine[0]["limit_min"] == 30 and mine[0]["over_by_min"] >= 1

    # The sweep records exactly one timeline event, and is idempotent.
    assert await sweep_breaches(db, now=future) >= 1
    assert await sweep_breaches(db, now=future) == 0
    feed = (await client.get(f"/api/feed/order/{o['id']}", headers=tenant)).json()
    assert any("SLA breach" in (i.get("body") or "") for i in feed["items"])


@pytest.mark.asyncio
async def test_sla_endpoint_shape_and_detail_flag(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    # Endpoint is well-formed even with no breaches.
    r = await client.get("/api/orders/sla/breaches", headers=tenant)
    assert r.status_code == 200
    assert "items" in r.json() and "total" in r.json()
    # Fresh order is not SLA-overdue on its detail.
    detail = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert detail.get("status_sla_overdue") in (False, None)


@pytest.mark.asyncio
async def test_status_transition_enforced(client, tenant):
    """A transition rule must block disallowed status moves (403)."""
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    statuses = (await client.get("/api/statuses", headers=tenant)).json()["items"]
    a, b = statuses[0]["key"], statuses[-1]["key"]
    if a == b:
        pytest.skip("need at least two work statuses")
    ida = statuses[0]["id"]
    # Define a rule FROM a that only permits a->a (i.e. nothing else), then try a->b.
    await client.post("/api/statuses/transitions", json={"from_status_id": ida, "to_status_id": ida},
                      headers=tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x", "status": a},
                           headers=tenant)).json()
    moved = await client.patch(f"/api/orders/{o['id']}/status", json={"status": b}, headers=tenant)
    assert moved.status_code == 403
