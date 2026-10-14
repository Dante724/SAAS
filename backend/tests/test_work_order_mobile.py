"""Part 3/8: checklists, completion gate, labor, invoice preview, payment links."""
import pytest
from sqlalchemy import select


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def _order(client, tenant, **extra):
    loc = await _loc(client, tenant)
    body = {"location_id": loc, "complaint": "x"}
    body.update(extra)
    r = await client.post("/api/orders", json=body, headers=tenant)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _terminal_status(client, tenant, key="done-m"):
    r = await client.post("/api/statuses", json={
        "name": "Done M", "key": key, "status_group": "closed", "is_terminal": True}, headers=tenant)
    assert r.status_code == 201, r.text
    return r.json()["key"]


@pytest.mark.asyncio
async def test_checklist_template_apply_and_toggle(client, tenant):
    o = await _order(client, tenant)
    tpl = await client.post("/api/checklist-templates", json={
        "name": "Phone intake", "items": [
            {"label": "Back up data", "required": True},
            {"label": "Test screen", "required": True},
            {"label": "Note cosmetic damage", "required": False}]}, headers=tenant)
    assert tpl.status_code == 201, tpl.text
    tpl_id = tpl.json()["id"]

    applied = await client.post(f"/api/orders/{o['id']}/checklist",
                                json={"template_id": tpl_id}, headers=tenant)
    assert applied.status_code == 201
    data = applied.json()
    assert data["summary"]["total"] == 3 and data["summary"]["required"] == 2
    assert data["summary"]["complete"] is False

    # toggle both required items done
    for item in [i for i in data["items"] if i["required"]]:
        r = await client.patch(f"/api/orders/{o['id']}/checklist/{item['id']}",
                               json={"done": True}, headers=tenant)
        assert r.status_code == 200
    summ = (await client.get(f"/api/orders/{o['id']}/checklist", headers=tenant)).json()["summary"]
    assert summ["required_done"] == 2 and summ["complete"] is True


@pytest.mark.asyncio
async def test_completion_gate_blocks_until_required_checklist_done(client, tenant):
    o = await _order(client, tenant)
    term = await _terminal_status(client, tenant)
    add = await client.post(f"/api/orders/{o['id']}/checklist",
                            json={"label": "Final QC", "required": True}, headers=tenant)
    item_id = add.json()["items"][0]["id"]

    blocked = await client.patch(f"/api/orders/{o['id']}/status", json={"status": term}, headers=tenant)
    assert blocked.status_code == 409 and "checklist" in blocked.json()["detail"].lower()

    await client.patch(f"/api/orders/{o['id']}/checklist/{item_id}", json={"done": True}, headers=tenant)
    ok = await client.patch(f"/api/orders/{o['id']}/status", json={"status": term}, headers=tenant)
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_completion_gate_signature_required(client, tenant, db):
    from backend.models import Order, WorkOrderType

    o = await _order(client, tenant)
    term = await _terminal_status(client, tenant, key="done-sig")
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    wt = WorkOrderType(org_id=me["org_id"], name="Needs sig", requires_signature=True)
    db.add(wt)
    await db.commit()
    await db.refresh(wt)
    od = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    od.type_id = wt.id
    await db.commit()

    blocked = await client.patch(f"/api/orders/{o['id']}/status", json={"status": term}, headers=tenant)
    assert blocked.status_code == 409 and "signature" in blocked.json()["detail"].lower()

    await client.post(f"/api/orders/{o['id']}/signature",
                      json={"signature": "data:image/png;base64,iVBORw0KGgo="}, headers=tenant)
    ok = await client.patch(f"/api/orders/{o['id']}/status", json={"status": term}, headers=tenant)
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_complete_endpoint_uses_default_terminal(client, tenant):
    o = await _order(client, tenant)
    await _terminal_status(client, tenant, key="done-default")
    r = await client.post(f"/api/orders/{o['id']}/complete", json={}, headers=tenant)
    assert r.status_code == 200
    detail = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert detail["closed_at"] is not None


@pytest.mark.asyncio
async def test_labor_summary(client, tenant):
    o = await _order(client, tenant)
    assert (await client.post("/api/timeclock/start",
                              json={"category": "job", "order_id": o["id"]}, headers=tenant)).status_code in (200, 201)
    await client.post("/api/timeclock/stop", json={}, headers=tenant)
    lab = (await client.get(f"/api/orders/{o['id']}/labor", headers=tenant)).json()
    assert lab["order_id"] == o["id"] and len(lab["entries"]) >= 1 and lab["running"] is False


@pytest.mark.asyncio
async def test_invoice_preview_and_payment_link(client, tenant):
    o = await _order(client, tenant)
    line = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "Screen", "qty": 1, "unit_price": 80.0},
        headers=tenant)
    assert line.status_code in (200, 201), line.text

    prev = (await client.get(f"/api/orders/{o['id']}/invoice-preview", headers=tenant)).json()
    assert prev["balance"] == pytest.approx(80.0) and len(prev["lines"]) == 1

    pl = await client.post(f"/api/orders/{o['id']}/payment-link", json={}, headers=tenant)
    assert pl.status_code == 201, pl.text
    d = pl.json()
    assert d["amount"] == pytest.approx(80.0)
    assert d["qr_svg"].startswith("data:image/svg+xml")
    assert d["url"]

    # public pay page can read it, and the simulated gateway can confirm payment
    info = (await client.get(f"/api/public/pay/{d['intent_id']}")).json()
    assert info["amount"] == pytest.approx(80.0)
    sim = await client.post(f"/api/payments/intents/{d['intent_id']}/simulate-pay")
    assert sim.status_code == 200 and sim.json()["status"] in ("succeeded", "paid")


@pytest.mark.asyncio
async def test_payment_link_rejects_zero_balance(client, tenant):
    o = await _order(client, tenant)
    r = await client.post(f"/api/orders/{o['id']}/payment-link", json={}, headers=tenant)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_timeclock_captures_gps(client, tenant, db):
    """GPS coordinates provided at clock-in are persisted structurally."""
    from sqlalchemy import select

    from backend.models import TimeEntry

    o = await _order(client, tenant)
    r = await client.post("/api/timeclock/start", json={
        "category": "job", "order_id": o["id"], "gps_lat": 30.3165, "gps_lng": 78.0322}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    entry = (await db.execute(select(TimeEntry).where(TimeEntry.order_id == o["id"]))).scalars().first()
    assert entry is not None
    assert entry.gps_lat == pytest.approx(30.3165) and entry.gps_lng == pytest.approx(78.0322)
