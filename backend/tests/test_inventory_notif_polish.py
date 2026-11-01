"""Builds 78-80 — product merge, markup auto-pricing, notification rules engine."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import NotificationLog, NotificationRule, Part, SerializedItem, StockLevel
from backend.services.dates import now_utc


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


# ── Build 78: merge duplicate products ───────────────────────────────────────
@pytest.mark.asyncio
async def test_merge_folds_stock_and_fills_blanks(client, tenant, db):
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    target = Part(org_id=me["org_id"], name="Screen", sell_price=50)          # no SKU
    source = Part(org_id=me["org_id"], name="Screen dup", sku="SCR-1", brand="Acme")
    db.add_all([target, source])
    await db.flush()
    db.add_all([
        StockLevel(part_id=target.id, location_id=loc, qty_available=2, qty_reserved=0),
        StockLevel(part_id=source.id, location_id=loc, qty_available=3, qty_reserved=0),
    ])
    unit = SerializedItem(org_id=me["org_id"], part_id=source.id, serial_number="M-1",
                          location_id=loc, status="in_stock")
    db.add(unit)
    await db.commit()

    r = await client.post("/api/inventory/parts/merge", json={
        "target_id": target.id, "source_ids": [source.id]}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["merged"] == 1

    await db.refresh(target)
    await db.refresh(source)
    await db.refresh(unit)
    assert target.sku == "SCR-1" and target.brand == "Acme"   # blanks filled from source
    assert target.sell_price == 50                            # real values NOT overwritten
    assert source.active is False                             # deactivated, not deleted
    assert unit.part_id == target.id                          # units re-pointed

    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == target.id, StockLevel.location_id == loc))).scalar_one()
    await db.refresh(sl)
    assert sl.qty_available == 5                              # 2 + 3 folded together


@pytest.mark.asyncio
async def test_merge_rejects_serialized_mismatch(client, tenant, db):
    me = await _me(client, tenant)
    a = Part(org_id=me["org_id"], name="A", serialized=True)
    b = Part(org_id=me["org_id"], name="B", serialized=False)
    db.add_all([a, b])
    await db.commit()
    r = await client.post("/api/inventory/parts/merge", json={
        "target_id": a.id, "source_ids": [b.id]}, headers=tenant)
    assert r.status_code == 400
    assert "serialized" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_merge_caps_at_ten(client, tenant, db):
    me = await _me(client, tenant)
    t = Part(org_id=me["org_id"], name="T")
    db.add(t)
    await db.commit()
    r = await client.post("/api/inventory/parts/merge", json={
        "target_id": t.id, "source_ids": [f"x{i}" for i in range(10)]}, headers=tenant)
    assert r.status_code == 400


# ── Build 79: markup auto-pricing ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_apply_markup_prices_from_cost(client, tenant, db):
    me = await _me(client, tenant)
    p = Part(org_id=me["org_id"], name="Battery", cost_price=10, avg_cost=12, sell_price=0)
    nocost = Part(org_id=me["org_id"], name="Freebie", cost_price=0, avg_cost=0, sell_price=99)
    db.add_all([p, nocost])
    await db.commit()

    r = await client.post("/api/inventory/parts/apply-markup", json={
        "markup_pct": 50, "part_ids": [p.id, nocost.id]}, headers=tenant)
    assert r.status_code == 200, r.text
    await db.refresh(p)
    await db.refresh(nocost)
    assert p.sell_price == 18.0        # avg_cost 12 × 1.5 (moving-average basis)
    assert nocost.sell_price == 99     # no cost basis → price left alone


@pytest.mark.asyncio
async def test_apply_markup_can_use_cost_price_basis(client, tenant, db):
    me = await _me(client, tenant)
    p = Part(org_id=me["org_id"], name="Cable", cost_price=10, avg_cost=12)
    db.add(p)
    await db.commit()
    await client.post("/api/inventory/parts/apply-markup", json={
        "markup_pct": 100, "part_ids": [p.id], "basis": "cost_price"}, headers=tenant)
    await db.refresh(p)
    assert p.sell_price == 20.0        # cost_price 10 × 2


# ── Build 80: notification rules engine ──────────────────────────────────────
async def _order_with_client(client, tenant, email="cust@x.dev"):
    cid = (await client.post("/api/clients", json={
        "name": "Cust", "phone": "+1555", "email": email}, headers=tenant)).json()["id"]
    loc = await _loc(client, tenant)
    o = await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)
    assert o.status_code == 201, o.text
    return o.json()


@pytest.mark.asyncio
async def _clear_seeded_rules(client, db, headers):
    """Drop the default rules seeded on org creation (144ah) so a test's own rule is the only
    one that can fire — otherwise seeded rules add extra NotificationLog rows and skew the counts."""
    from sqlalchemy import delete as _del
    oid = (await client.get("/api/auth/me", headers=headers)).json()["org_id"]
    await db.execute(_del(NotificationRule).where(NotificationRule.org_id == oid))
    await db.commit()


async def test_rule_delay_schedules_instead_of_sending(client, tenant, db):
    """A delayed rule queues the send with a due time rather than firing now."""
    await _clear_seeded_rules(client, db, tenant)
    await client.post("/api/statuses", json={
        "name": "Done nr", "key": "done-nr", "status_group": "closed", "is_terminal": True},
        headers=tenant)
    r = await client.post("/api/notifications/rules", json={
        "module": "order", "event": "closed", "trigger_status_key": "done-nr",
        "channel": "email", "delay_min": 60}, headers=tenant)
    assert r.status_code == 201, r.text

    o = await _order_with_client(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done-nr"}, headers=tenant)

    logs = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == o["id"], NotificationLog.rule_id.isnot(None)))).scalars().all()
    assert len(logs) == 1
    assert logs[0].status == "scheduled"          # not sent yet
    assert logs[0].send_at > now_utc()    # due in the future


@pytest.mark.asyncio
async def test_run_due_dispatches_when_time_passes(client, tenant, db):
    await _clear_seeded_rules(client, db, tenant)
    await client.post("/api/statuses", json={
        "name": "Done nr2", "key": "done-nr2", "status_group": "closed", "is_terminal": True},
        headers=tenant)
    await client.post("/api/notifications/rules", json={
        "module": "order", "event": "closed", "trigger_status_key": "done-nr2",
        "channel": "email", "delay_min": 60}, headers=tenant)
    o = await _order_with_client(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done-nr2"}, headers=tenant)

    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == o["id"]))).scalars().first()
    # pretend the hour has passed
    log.send_at = now_utc() - timedelta(minutes=1)
    await db.commit()

    res = (await client.post("/api/notifications/rules/run-due", headers=tenant)).json()
    assert res["due"] == 1 and res["sent"] == 1
    await db.refresh(log)
    assert log.status == "sent"


@pytest.mark.asyncio
async def test_immediate_rule_sends_now(client, tenant, db):
    await client.post("/api/statuses", json={
        "name": "Done nr3", "key": "done-nr3", "status_group": "closed", "is_terminal": True},
        headers=tenant)
    await client.post("/api/notifications/rules", json={
        "module": "order", "event": "closed", "trigger_status_key": "done-nr3",
        "channel": "email", "delay_min": 0}, headers=tenant)
    o = await _order_with_client(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done-nr3"}, headers=tenant)
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == o["id"], NotificationLog.rule_id.isnot(None)))).scalars().first()
    assert log.status == "sent"


@pytest.mark.asyncio
async def test_working_hours_only_defers_night_sends():
    """A send that would land at 23:10 is held to the next working morning."""
    from backend.services.notification_rules import shift_into_working_hours
    night = datetime(2026, 8, 1, 23, 10)
    assert shift_into_working_hours(night) == datetime(2026, 8, 2, 9, 0)
    early = datetime(2026, 8, 1, 6, 30)
    assert shift_into_working_hours(early) == datetime(2026, 8, 1, 9, 0)
    midday = datetime(2026, 8, 1, 13, 0)
    assert shift_into_working_hours(midday) == midday      # already in hours


@pytest.mark.asyncio
async def test_reminder_rule_queues_before_appointment(client, tenant, db):
    """A 120-minute reminder queues a send for an appointment 1h away, timed to
    the appointment minus the lead, and doesn't double-queue."""
    await client.post("/api/notifications/rules", json={
        "module": "order", "event": "reminder", "channel": "email",
        "reminder_offset_min": 120}, headers=tenant)
    o = await _order_with_client(client, tenant)
    # Scheduled times are the shop's wall clock (the test shop runs on the default India time),
    # not UTC: "UTC + 1h" was 4.5 hours in the past there (launch B6).
    from backend.services.dates import local_now
    soon = local_now("Asia/Kolkata") + timedelta(minutes=60)
    r = await client.patch(f"/api/orders/{o['id']}/schedule", json={
        "scheduled_for": soon.strftime("%Y-%m-%dT%H:%M")}, headers=tenant)
    assert r.status_code == 200, r.text

    q1 = (await client.post("/api/notifications/rules/queue-reminders", headers=tenant)).json()
    assert q1["queued"] == 1
    q2 = (await client.post("/api/notifications/rules/queue-reminders", headers=tenant)).json()
    assert q2["queued"] == 0            # idempotent — no double reminder

    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == o["id"],
        NotificationLog.trigger_event == "reminder"))).scalars().one()
    assert log.status == "scheduled"


@pytest.mark.asyncio
async def test_reminder_rule_requires_offset(client, tenant):
    r = await client.post("/api/notifications/rules", json={
        "module": "order", "event": "reminder", "channel": "email"}, headers=tenant)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_rule_location_scoping(client, tenant, db):
    """A rule scoped to another location doesn't fire for this order."""
    from backend.models import Location
    me = await _me(client, tenant)
    other = Location(org_id=me["org_id"], name="Other branch")
    db.add(other)
    await db.commit()
    await db.refresh(other)
    await _clear_seeded_rules(client, db, tenant)

    await client.post("/api/statuses", json={
        "name": "Done nr4", "key": "done-nr4", "status_group": "closed", "is_terminal": True},
        headers=tenant)
    await client.post("/api/notifications/rules", json={
        "module": "order", "event": "closed", "trigger_status_key": "done-nr4",
        "channel": "email", "location_id": other.id}, headers=tenant)

    o = await _order_with_client(client, tenant)      # created at the default location
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done-nr4"}, headers=tenant)
    logs = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == o["id"],
        NotificationLog.rule_id.isnot(None)))).scalars().all()
    assert logs == []                                 # wrong location → no send


@pytest.mark.asyncio
async def test_variable_library_includes_custom_fields(client, tenant, db):
    from backend.models import CustomField
    me = await _me(client, tenant)
    db.add(CustomField(org_id=me["org_id"], entity_type="client", field_key="loyalty_tier",
                       field_label="Loyalty tier", field_type="text"))
    await db.commit()
    v = (await client.get("/api/notifications/variables", headers=tenant)).json()
    assert "{Order Number}" in v["groups"]["Order"]
    assert "{Client Feedback URL}" in v["groups"]["Links"]   # 144em: client-facing URLs grouped under Links
    assert "{Loyalty tier}" in v["groups"]["Custom fields"]
