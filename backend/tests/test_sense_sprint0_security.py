"""Tekly Sense sprint 0: public endpoints and messaging are safe to build on.

- The public status lookup throttled by order number across every shop, so flooding one
  shop's "#1001" locked out every other shop's customer with that number.
- The appointment "hello" step turned a phone number into the customer's first name.
- Customers could post unlimited messages into a shop's inbox from the tracking page.
- "View reports" alone showed gross profit through the insights builder.
- A customer who opted out after a message was queued still received it.
- Appointment notifications ignored the customer's consent.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select


async def _register(client):
    email = f"s0-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Shop", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, me.get("org_id") or me.get("orgId")


async def _order(client, H, phone):
    cid = (await client.post("/api/clients", json={"name": "Jo Doe", "phone": phone}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "complaint": "screen"}, headers=H)).json()
    return o


@pytest.mark.asyncio
async def test_flooding_one_shops_order_does_not_lock_out_another_shop(client):
    HA, org_a = await _register(client)
    HB, org_b = await _register(client)
    oa = await _order(client, HA, "5550001111")
    ob = await _order(client, HB, "5550002222")
    assert oa["order_number"] == ob["order_number"]          # numbering is per shop

    codes = [(await client.post("/api/public/lookup", json={
        "order_number": oa["order_number"], "phone": "5559999999", "shop": org_a})).status_code
        for _ in range(6)]
    assert codes[:5] == [404] * 5 and codes[5] == 429, codes  # 5 tries per order per hour

    ok = await client.post("/api/public/lookup", json={
        "order_number": ob["order_number"], "phone": "5550002222", "shop": org_b})
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_hello_recognises_a_phone_without_revealing_the_name(client, db):
    from backend.models import Organisation
    H, org_id = await _register(client)
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.appointment_enabled = True
    await db.commit()
    await client.post("/api/clients", json={"name": "Ravi Kumar", "phone": "9876543210"}, headers=H)
    r = await client.post(f"/api/public/appointment/{org_id}/hello", json={"phone": "9876543210"})
    assert r.status_code == 200 and r.json()["known"] is True
    assert "Ravi" not in r.text


@pytest.mark.asyncio
async def test_tracking_page_messages_are_rate_limited(client, db):
    from backend.models import Order
    H, _ = await _register(client)
    o = await _order(client, H, "5550003333")
    token = (await db.execute(select(Order.public_token).where(Order.id == o["id"]))).scalar_one()
    codes = [(await client.post(f"/api/public/track/{token}/message", json={"body": f"hi {i}"})).status_code
             for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429, codes


@pytest.mark.asyncio
async def test_profit_in_the_insights_builder_needs_see_profit(client):
    H, _ = await _register(client)
    rid = (await client.post("/api/settings/rbac/roles", json={
        "name": "Reports only", "permissions": {"can_view_reports": True, "can_see_profit": False}},
        headers=H)).json()["id"]
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    assert (await client.post("/api/staff/employees", json={
        "name": "Tm", "email": email, "password": "pw-test-12345", "role_id": rid}, headers=H)).status_code == 201
    T = {"Authorization": "Bearer " + (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-test-12345"})).json()["access_token"]}

    url = "/api/reports/insights/data?subject=sales&metric=gross_profit"
    assert (await client.get(url, headers=T)).status_code == 403
    assert (await client.get("/api/reports/insights/data?subject=sales&metric=amount", headers=T)).status_code == 200
    assert (await client.get(url, headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_opting_out_after_a_message_is_queued_stops_it(client, db):
    from backend.models import Client, NotificationLog
    from backend.services import notification_rules as nr
    H, org_id = await _register(client)
    cust = Client(org_id=org_id, name="Later Stop", phone="9995557777", consent_phone=True)
    db.add(cust)
    await db.flush()
    db.add(NotificationLog(org_id=org_id, scope="client", channel="sms", trigger_event="reminder",
                           client_id=cust.id, recipient=cust.phone, body="Reminder",
                           status="scheduled", send_at=datetime.utcnow() - timedelta(minutes=1)))
    await db.commit()
    cust.consent_phone = False                      # customer replied STOP
    await db.commit()

    res = await nr.run_due(db, org_id=org_id)
    assert res["sent"] == 0
    log = (await db.execute(select(NotificationLog).where(NotificationLog.client_id == cust.id))).scalar_one()
    assert log.status == "skipped" and "consent" in (log.error or "").lower()


@pytest.mark.asyncio
async def test_appointment_notifications_respect_consent(client, db):
    from backend.models import Client, NotificationLog, NotificationRule
    H, org_id = await _register(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    cid = (await client.post("/api/clients", json={"name": "No Mail", "email": "nomail@x.dev"}, headers=H)).json()["id"]
    (await db.execute(select(Client).where(Client.id == cid))).scalar_one().consent_email = False
    await db.execute(delete(NotificationRule).where(NotificationRule.org_id == org_id))
    db.add(NotificationRule(org_id=org_id, module="appointment", event="created", channel="email", active=True))
    await db.commit()

    day = datetime.utcnow().date().isoformat()
    r = await client.post("/api/appointments", json={
        "client_id": cid, "employee_id": me["id"], "scheduled_for": f"{day}T10:00:00", "duration_min": 60}, headers=H)
    assert r.status_code == 201, r.text
    db.expire_all()
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.client_id == cid, NotificationLog.trigger_event == "created"))).scalars().first()
    assert log is not None and log.status == "skipped" and "consent" in (log.error or "").lower()


@pytest.mark.asyncio
async def test_queued_message_for_a_client_no_longer_on_file_is_not_sent(client, db):
    """The row points at a client this shop can't see (another shop's id stands in for a
    removed client): nothing is sent to the stored number."""
    from backend.models import Client, NotificationLog
    from backend.services import notification_rules as nr
    H, org_id = await _register(client)
    _, other_org = await _register(client)
    stranger = Client(org_id=other_org, name="Elsewhere", phone="9995558888", consent_phone=True)
    db.add(stranger)
    log = NotificationLog(org_id=org_id, scope="client", channel="sms", trigger_event="reminder",
                          recipient="9995558888", body="Reminder",
                          status="scheduled", send_at=datetime.utcnow() - timedelta(minutes=1))
    db.add(log)
    await db.commit()
    # The app refuses to write a cross-shop reference, so point the row at it directly in SQL,
    # the way stale data would look.
    from sqlalchemy import update
    await db.execute(update(NotificationLog).where(NotificationLog.id == log.id).values(client_id=stranger.id))
    await db.commit()
    res = await nr.run_due(db, org_id=org_id)
    assert res["sent"] == 0
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.client_id == stranger.id))).scalar_one()
    assert log.status == "skipped" and "no longer on file" in (log.error or "")


@pytest.mark.asyncio
async def test_insights_builder_only_offers_profit_to_those_who_can_see_it(client):
    H, _ = await _register(client)
    rid = (await client.post("/api/settings/rbac/roles", json={
        "name": "Reports only", "permissions": {"can_view_reports": True, "can_see_profit": False}},
        headers=H)).json()["id"]
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Tm", "email": email, "password": "pw-test-12345", "role_id": rid}, headers=H)
    T = {"Authorization": "Bearer " + (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-test-12345"})).json()["access_token"]}

    def metrics(meta):
        return {m["key"] for s in meta["subjects"] for m in s["metrics"]}
    assert "gross_profit" not in metrics((await client.get("/api/reports/insights/meta", headers=T)).json())
    assert "gross_profit" in metrics((await client.get("/api/reports/insights/meta", headers=H)).json())


@pytest.mark.asyncio
async def test_lookup_without_a_shop_still_works_and_long_input_is_rejected(client):
    H, _ = await _register(client)
    o = await _order(client, H, "5550004444")
    ok = await client.post("/api/public/lookup", json={"order_number": "#" + o["order_number"], "phone": "555-000-4444"})
    assert ok.status_code == 200, ok.text
    big = await client.post("/api/public/lookup", json={"order_number": "9" * 5000, "phone": "1"})
    assert big.status_code == 422


@pytest.mark.asyncio
async def test_profit_stays_hidden_in_the_trend_view(client):
    H, _ = await _register(client)
    rid = (await client.post("/api/settings/rbac/roles", json={
        "name": "Reports only", "permissions": {"can_view_reports": True, "can_see_profit": False}},
        headers=H)).json()["id"]
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Tm", "email": email, "password": "pw-test-12345", "role_id": rid}, headers=H)
    T = {"Authorization": "Bearer " + (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-test-12345"})).json()["access_token"]}
    r = await client.get("/api/reports/insights/data?kind=trend&subject=sales&metric=gross_profit", headers=T)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_message_flood_on_one_order_does_not_block_another(client, db):
    from backend.models import Order
    H, _ = await _register(client)
    a = await _order(client, H, "5550005555")
    b = await _order(client, H, "5550006666")
    ta, tb = [(await db.execute(select(Order.public_token).where(Order.id == o["id"]))).scalar_one() for o in (a, b)]
    for i in range(10):
        assert (await client.post(f"/api/public/track/{ta}/message", json={"body": f"a{i}"})).status_code == 200
    assert (await client.post(f"/api/public/track/{ta}/message", json={"body": "a10"})).status_code == 429
    assert (await client.post(f"/api/public/track/{tb}/message", json={"body": "b"})).status_code == 200
