"""Tekly Sense sprint 1: suggestions learned from the shop's own history.

No AI service and no new dependency: settings per shop, a learning threshold, job-line
suggestions from similar closed jobs, work the customer deferred last time, and a margin
check against the shop's usual margin. Every reply stays inside the shop and the viewer's
permissions.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.services.permissions import OPT_IN, new_role_permissions, role_allows


async def _register(client):
    email = f"s1-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Shop", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, me.get("org_id") or me.get("orgId")


async def _staff(client, H, perms):
    rid = (await client.post("/api/settings/rbac/roles", json={
        "name": f"R-{uuid.uuid4().hex[:4]}", "permissions": perms}, headers=H)).json()["id"]
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tm", "email": email, "password": "pw-test-12345", "role_id": rid}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


_n = 0


async def _closed_job(db, org_id, complaint, lines, device_id=None, days_ago=10):
    """A closed job straight in the database (fast): lines are (description, price, cost)."""
    global _n
    from backend.models import Location, Order, OrderLine
    _n += 1
    loc = (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    when = datetime.utcnow() - timedelta(days=days_ago)
    total = sum(p for _, p, _ in lines)
    o = Order(org_id=org_id, order_number=f"H-{_n}-{uuid.uuid4().hex[:4]}", location_id=loc,
              complaint=complaint, status="closed", created_at=when, closed_at=when,
              device_id=device_id, total=total)
    db.add(o)
    await db.flush()
    for desc, price, cost in lines:
        db.add(OrderLine(order_id=o.id, line_type="labour", kind="service", description=desc,
                         qty=1, unit_price=price, cost_price=cost, total=price))
    return o


async def _history(db, org_id, screens=12, batteries=8):
    for _ in range(screens):
        await _closed_job(db, org_id, "Cracked screen on iPhone", [("Screen replacement", 129, 60), ("Tempered glass", 15, 3)])
    for _ in range(batteries):
        await _closed_job(db, org_id, "Battery drains fast", [("Battery replacement", 59, 20)])
    await db.commit()


# ── Foundation ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_settings_default_on_and_only_the_owner_can_change_them(client, db):
    from backend.models import AuditLog
    H, org_id = await _register(client)
    s = (await client.get("/api/sense/settings", headers=H)).json()
    assert s["enabled"] is True
    assert s["features"] == {"job_suggestions": True, "deferred": True, "ready_time_why": True, "margin_check": True,
                             "morning_brief": True, "added_together": True, "service_due": True, "watchdog": True}
    assert s["brief"] == {"recipients": [], "hour": 7, "email": False, "push": False}   # nothing sent until asked
    assert s["learning"]["needed"] == 20 and s["learning"]["closed_jobs"] == 0 and s["learning"]["ready"] is False

    T = await _staff(client, H, {"can_use_sense_suggestions": True, "can_manage_settings": True})
    assert (await client.put("/api/sense/settings", json={"enabled": False}, headers=T)).status_code == 403

    r = await client.put("/api/sense/settings", json={"features": {"margin_check": False}}, headers=H)
    assert r.status_code == 200 and r.json()["features"]["margin_check"] is False and r.json()["enabled"] is True
    assert (await db.execute(select(AuditLog).where(
        AuditLog.org_id == org_id, AuditLog.action == "sense.settings.update"))).scalars().first()


def test_the_new_permission_is_off_unless_granted():
    assert "can_use_sense_suggestions" in OPT_IN

    class Legacy:                       # an old, non-explicit role that never heard of the code
        permissions = {"can_create_tickets": True}
        perms_explicit = False
    assert role_allows(Legacy(), "can_use_sense_suggestions") is False
    assert new_role_permissions("Specialist")["can_use_sense_suggestions"] is False
    assert new_role_permissions("Partner")["can_use_sense_suggestions"] is False
    assert new_role_permissions("Manager")["can_use_sense_suggestions"] is True
    assert new_role_permissions("Full access")["can_use_sense_suggestions"] is True


# ── Job suggestions ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_new_shop_is_still_learning(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id, screens=5, batteries=3)
    r = (await client.post("/api/sense/draft", json={"complaint": "cracked screen"}, headers=H)).json()
    assert r["learning"] is True and r["items"] == []


@pytest.mark.asyncio
async def test_suggestions_come_from_similar_jobs_in_this_shop(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    r = await client.post("/api/sense/draft", json={"complaint": "iPhone 13 screen cracked"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["learning"] is False
    names = [i["description"] for i in body["items"]]
    assert names[:2] == ["Screen replacement", "Tempered glass"] and "Battery replacement" not in names
    top = body["items"][0]
    assert top["unit_price"] == 129
    assert top["why"]["used"] == 12 and top["why"]["similar"] == 12
    assert "H-" not in r.text                      # never other jobs' numbers


@pytest.mark.asyncio
async def test_suggestions_never_cross_shops(client, db):
    HA, org_a = await _register(client)
    HB, org_b = await _register(client)
    await _history(db, org_a)
    await _history(db, org_b, screens=0, batteries=20)
    names = [i["description"] for i in (await client.post(
        "/api/sense/draft", json={"complaint": "cracked screen"}, headers=HB)).json()["items"]]
    assert "Screen replacement" not in names


@pytest.mark.asyncio
async def test_staff_need_the_permission_and_prices_follow_the_price_permission(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    no = await _staff(client, H, {"can_create_tickets": True})
    assert (await client.post("/api/sense/draft", json={"complaint": "cracked screen"}, headers=no)).status_code == 403
    no_prices = await _staff(client, H, {"can_use_sense_suggestions": True, "can_view_item_prices": False})
    items = (await client.post("/api/sense/draft", json={"complaint": "cracked screen"}, headers=no_prices)).json()["items"]
    assert items and all("unit_price" not in i for i in items)


@pytest.mark.asyncio
async def test_switching_a_feature_off_stops_it(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    await client.put("/api/sense/settings", json={"features": {"job_suggestions": False}}, headers=H)
    r = (await client.post("/api/sense/draft", json={"complaint": "cracked screen"}, headers=H)).json()
    assert r["enabled"] is False and r["items"] == []
    await client.put("/api/sense/settings", json={"enabled": False, "features": {"job_suggestions": True}}, headers=H)
    r = (await client.post("/api/sense/draft", json={"complaint": "cracked screen"}, headers=H)).json()
    assert r["enabled"] is False and r["items"] == []


# ── Deferred last time ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_work_deferred_on_an_earlier_visit_comes_back(client, db):
    from backend.models import Device, Recommendation
    H, org_id = await _register(client)
    cid = (await client.post("/api/clients", json={"name": "Jo", "phone": "5551110000"}, headers=H)).json()["id"]
    dev = Device(org_id=org_id, client_id=cid, brand="Honda", model="Civic")
    db.add(dev)
    await db.flush()
    old = await _closed_job(db, org_id, "Oil change", [("Oil change", 60, 20)], device_id=dev.id, days_ago=120)
    old.client_id = cid
    db.add(Recommendation(org_id=org_id, order_id=old.id, title="Front tyres", unit_price=340,
                          status="declined", decline_reason="not_now", remind_after=date.today()))
    db.add(Recommendation(org_id=org_id, order_id=old.id, title="Wiper blades", unit_price=30,
                          status="declined", decline_reason="not_needed"))
    await db.commit()
    new = (await client.post("/api/orders", json={"client_id": cid, "device_id": dev.id, "complaint": "service"}, headers=H)).json()

    r = await client.get(f"/api/sense/orders/{new['id']}/deferred", headers=H)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["title"] for i in items] == ["Front tyres"]
    assert items[0]["unit_price"] == 340 and items[0]["from_order_number"] == old.order_number

    HB, _ = await _register(client)
    assert (await client.get(f"/api/sense/orders/{new['id']}/deferred", headers=HB)).status_code == 404


# ── Margin check ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_margin_check_flags_a_thin_job_and_is_owner_or_profit_only(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)                      # usual margin about 55-66%
    thin = await _closed_job(db, org_id, "Screen", [("Screen replacement", 100, 90)], days_ago=0)
    thin.closed_at = None
    thin.status = "new"
    await db.commit()

    r = await client.get(f"/api/sense/orders/{thin.id}/margin", headers=H)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["flag"] is True and m["margin_pct"] == 10.0 and m["usual_pct"] > 50 and m["sample"] >= 20

    T = await _staff(client, H, {"can_use_sense_suggestions": True, "can_see_profit": False})
    assert (await client.get(f"/api/sense/orders/{thin.id}/margin", headers=T)).status_code == 403


@pytest.mark.asyncio
async def test_deferred_work_hides_earlier_jobs_the_caller_cannot_open(client, db):
    """An own-jobs-only technician sees deferred work only from earlier jobs they could open."""
    from backend.models import Device, Order, Recommendation
    H, org_id = await _register(client)
    owner_id = (await client.get("/api/auth/me", headers=H)).json()["id"]
    rid = (await client.post("/api/settings/rbac/roles", json={
        "name": "Own jobs", "order_scope": "own",
        "permissions": {"can_use_sense_suggestions": True, "can_create_tickets": True}}, headers=H)).json()["id"]
    email = f"own-{uuid.uuid4().hex[:6]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Own", "email": email, "password": "pw-test-12345", "role_id": rid}, headers=H)
    T = {"Authorization": "Bearer " + (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-test-12345"})).json()["access_token"]}
    tech_id = (await client.get("/api/auth/me", headers=T)).json()["id"]

    dev = Device(org_id=org_id, brand="Yamaha", model="F150")
    db.add(dev)
    await db.flush()
    old = await _closed_job(db, org_id, "Service", [("Service", 200, 80)], device_id=dev.id, days_ago=200)
    old.assigned_to_id = owner_id                       # someone else's job
    db.add(Recommendation(org_id=org_id, order_id=old.id, title="Impeller", note="internal: owner quote",
                          status="declined", decline_reason="not_now"))
    new = await _closed_job(db, org_id, "Service", [], device_id=dev.id, days_ago=0)
    new.closed_at, new.status, new.assigned_to_id = None, "new", tech_id
    await db.commit()

    assert (await client.get(f"/api/sense/orders/{new.id}/deferred", headers=H)).json()["items"]
    r = await client.get(f"/api/sense/orders/{new.id}/deferred", headers=T)
    assert r.status_code == 200 and r.json()["items"] == [], r.text


@pytest.mark.asyncio
async def test_only_the_owner_gets_the_learning_count(client):
    H, _ = await _register(client)
    T = await _staff(client, H, {"can_use_sense_suggestions": True})
    assert "learning" in (await client.get("/api/sense/settings", headers=H)).json()
    staff_view = (await client.get("/api/sense/settings", headers=T)).json()
    assert "learning" not in staff_view and staff_view["enabled"] is True
    assert (await client.get("/api/auth/me", headers=H)).json()["is_shop_owner"] is True
    assert (await client.get("/api/auth/me", headers=T)).json()["is_shop_owner"] is False


@pytest.mark.asyncio
async def test_the_owner_sees_permissions_added_after_their_role_was_saved(client, db):
    """The owner's saved role predates a new code; the server already grants it to the owner,
    and /me must say so too, or the app hides the feature from the person who pays for it."""
    from backend.models import Employee, Role
    H, org_id = await _register(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    role = (await db.execute(select(Role).where(Role.id == emp.role_id))).scalar_one()
    role.permissions = {k: v for k, v in (role.permissions or {}).items() if k != "can_use_sense_suggestions"}
    await db.commit()
    me = (await client.get("/api/auth/me", headers=H)).json()
    assert me["permissions"]["can_use_sense_suggestions"] is True
