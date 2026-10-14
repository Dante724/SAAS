"""Build 144em — Orderry-style notification RULES: CRUD, PATCH edit, chat_first.

The rule engine (module/event/channel/template/delay/working-hours/location) already
existed; this build surfaces it in the UI and adds chat_first + a PATCH edit endpoint.
These tests lock the API the frontend Workflows page depends on.
"""
import pytest
from sqlalchemy import select


async def _me(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()


@pytest.mark.asyncio
async def test_rule_crud_roundtrip(client, tenant):
    # create
    r = await client.post("/api/notifications/rules", json={
        "module": "order", "event": "status_changed", "trigger_status_key": "done",
        "channel": "sms", "delay_min": 15, "working_hours_only": True,
    }, headers=tenant)
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["event"] == "status_changed"
    assert rule["delay_min"] == 15
    assert rule["working_hours_only"] is True
    assert rule["chat_first"] is False          # new field, defaults off

    # list contains it, with meta
    lst = (await client.get("/api/notifications/rules", headers=tenant)).json()
    assert any(x["id"] == rule["id"] for x in lst["items"])
    assert "modules" in lst and "events" in lst

    # delete
    d = await client.delete(f"/api/notifications/rules/{rule['id']}", headers=tenant)
    assert d.status_code == 200
    lst2 = (await client.get("/api/notifications/rules", headers=tenant)).json()
    assert not any(x["id"] == rule["id"] for x in lst2["items"])


@pytest.mark.asyncio
async def test_rule_patch_edits_in_place(client, tenant):
    rule = (await client.post("/api/notifications/rules", json={
        "module": "order", "event": "created", "channel": "email",
    }, headers=tenant)).json()

    # change channel + turn on chat_first (Orderry's "change sending method", but a full edit)
    p = await client.patch(f"/api/notifications/rules/{rule['id']}", json={
        "channel": "sms", "chat_first": True, "delay_min": 30,
    }, headers=tenant)
    assert p.status_code == 200, p.text
    out = p.json()
    assert out["channel"] == "sms"
    assert out["chat_first"] is True
    assert out["delay_min"] == 30
    assert out["event"] == "created"            # untouched fields survive


@pytest.mark.asyncio
async def test_rule_patch_validates(client, tenant):
    rule = (await client.post("/api/notifications/rules", json={
        "module": "order", "event": "created", "channel": "email",
    }, headers=tenant)).json()
    # bad event rejected
    assert (await client.patch(f"/api/notifications/rules/{rule['id']}",
            json={"event": "nonsense"}, headers=tenant)).status_code == 400
    # turning it into a reminder without an offset is rejected
    assert (await client.patch(f"/api/notifications/rules/{rule['id']}",
            json={"event": "reminder"}, headers=tenant)).status_code == 400
    # with an offset it's accepted
    ok = await client.patch(f"/api/notifications/rules/{rule['id']}",
            json={"event": "reminder", "reminder_offset_min": 120}, headers=tenant)
    assert ok.status_code == 200 and ok.json()["reminder_offset_min"] == 120


@pytest.mark.asyncio
async def test_patch_missing_rule_404(client, tenant):
    assert (await client.patch("/api/notifications/rules/does-not-exist",
            json={"channel": "sms"}, headers=tenant)).status_code == 404


@pytest.mark.asyncio
async def test_variable_catalog_is_rich_and_grouped(client, tenant):
    """The picker catalog must be Orderry-grade: many groups, many resolvable vars."""
    groups = (await client.get("/api/notifications/variables", headers=tenant)).json()["groups"]
    # Orderry-style section coverage
    for section in ["Company", "Client", "Order", "Asset", "Employees", "Finance", "Date", "Links"]:
        assert section in groups, f"missing variable group: {section}"
    flat = [v for items in groups.values() for v in items]
    assert len(flat) >= 40, f"expected a rich catalog, got only {len(flat)} variables"
    # variables the resolver actually supports (not the old made-up names)
    assert "{Order Number}" in groups["Order"]
    assert "{Asset Brand}" in groups["Asset"]
    assert "{Tracking URL}" in groups["Links"]
    assert "{Client Feedback URL}" in groups["Links"]
    # table/row blocks must NOT leak into a message-body picker
    assert "{Products Table}" not in flat
    assert "{Row Number}" not in flat


@pytest.mark.asyncio
async def test_tracking_url_variable_resolves(client, tenant, db, monkeypatch):
    """{Tracking URL} renders to the order's public track link."""
    from backend.config import settings
    from backend.routers.documents import _resolve, _render
    from backend.models import Order
    from sqlalchemy import select as _select
    from sqlalchemy.orm import selectinload

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://shop.example")
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    o = (await client.post("/api/orders",
         json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
    order = (await db.execute(_select(Order).where(Order.id == o["id"]).options(
        selectinload(Order.client), selectinload(Order.lines),
        selectinload(Order.device), selectinload(Order.assigned_to)))).scalar_one()

    doc, rows = await _resolve(db, order.org_id, order)
    out = _render("Track: {Tracking URL}", doc, rows)
    assert "https://shop.example/track/" in out
    assert order.public_token in out


@pytest.mark.asyncio
async def test_chat_first_routes_to_latest_chat_channel(client, tenant, db):
    """A chat_first rule sends via the channel of the client's most recent thread."""
    from backend.models import ChatThread, NotificationLog
    from backend.services import notification_rules as rules_svc
    from datetime import datetime

    me = await _me(client, tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients",
            json={"name": "Chatty Cathy", "phone": "+15550001", "email": "c@x.io"},
            headers=tenant)).json()["id"]
    o = (await client.post("/api/orders",
            json={"location_id": loc, "complaint": "x", "client_id": cid}, headers=tenant)).json()

    # client's latest conversation is on whatsapp
    db.add(ChatThread(org_id=me["org_id"], channel="whatsapp", client_id=cid,
                      contact_phone="+15550001", last_message_at=datetime.utcnow()))
    await db.commit()

    # a rule that would normally email, but opts into chat_first
    await client.post("/api/notifications/rules", json={
        "module": "order", "event": "status_changed", "channel": "email", "chat_first": True,
    }, headers=tenant)

    await rules_svc.evaluate(db, org_id=me["org_id"], order_id=o["id"],
                             event="status_changed", status_key=None)

    logs = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == o["id"]))).scalars().all()
    assert logs, "a notification should have been logged"
    assert any(l.channel == "whatsapp" for l in logs), \
        "chat_first should have routed the send to the client's latest chat channel"
