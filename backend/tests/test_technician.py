"""Technician mobile API: dashboard buckets, notifications, push, auth refresh."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.services.dates import now_utc


async def _me(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()


async def _loc(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_dashboard_shape_and_buckets(client, tenant, db):
    from backend.models import Order

    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    ids = []
    for _ in range(4):
        r = await client.post("/api/orders", json={
            "location_id": loc, "complaint": "job", "assigned_to_id": me["id"]}, headers=tenant)
        assert r.status_code in (200, 201), r.text
        ids.append(r.json()["id"])

    now = now_utc()
    today_noon = datetime(now.year, now.month, now.day, 12)
    async def setfields(oid, **kw):
        o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
        for k, v in kw.items():
            setattr(o, k, v)
    await setfields(ids[0], scheduled_for=today_noon)                       # today
    await setfields(ids[1], scheduled_for=now + timedelta(days=2))          # upcoming
    await setfields(ids[2], closed_at=now)                                  # completed
    await setfields(ids[3], scheduled_for=today_noon, due_at=now - timedelta(hours=2))  # overdue
    await db.commit()

    d = (await client.get("/api/tech/dashboard", headers=tenant)).json()
    for key in ("today", "upcoming", "completed", "overdue", "stats", "clock", "unread_notifications"):
        assert key in d
    assert ids[2] in [c["id"] for c in d["completed"]]
    assert ids[1] in [c["id"] for c in d["upcoming"]]
    assert ids[3] in [c["id"] for c in d["overdue"]]
    assert d["stats"]["assigned_total"] >= 4
    assert d["clock"]["clocked_in"] is False


@pytest.mark.asyncio
async def test_dashboard_reflects_clock_in(client, tenant):
    r = await client.post("/api/timeclock/start", json={"category": "job"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    d = (await client.get("/api/tech/dashboard", headers=tenant)).json()
    assert d["clock"]["clocked_in"] is True
    assert d["clock"]["category"] == "job"


@pytest.mark.asyncio
async def test_notifications_flow(client, tenant, db):
    from backend.services.push import notify

    me = await _me(client, tenant)
    await notify(db, org_id=me["org_id"], employee_id=me["id"], kind="job.assigned",
                 title="New job WO-1", body="cracked screen", deep_link="/tech#/order/x")
    lst = (await client.get("/api/tech/notifications", headers=tenant)).json()["items"]
    assert lst and lst[0]["title"] == "New job WO-1" and lst[0]["read"] is False
    assert (await client.get("/api/tech/dashboard", headers=tenant)).json()["unread_notifications"] >= 1

    nid = lst[0]["id"]
    assert (await client.post(f"/api/tech/notifications/{nid}/read", headers=tenant)).status_code == 200
    assert (await client.get("/api/tech/dashboard", headers=tenant)).json()["unread_notifications"] == 0
    # idempotent + read-all
    await notify(db, org_id=me["org_id"], employee_id=me["id"], kind="chat.message", title="Msg")
    res = (await client.post("/api/tech/notifications/read-all", headers=tenant)).json()
    assert res["ok"] and res["marked"] >= 1


@pytest.mark.asyncio
async def test_assignment_creates_notification_for_other_tech(client, tenant, db):
    """Assigning a new order to a *different* employee notifies that employee."""
    from backend.models import Employee, StaffNotification
    from backend.services.auth import hash_password

    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    # second tech in the same org
    tech = Employee(org_id=me["org_id"], name="Tech Two", email="tech2@iso.dev",
                    password_hash=hash_password("pw-test-12345"), active=True)
    db.add(tech)
    await db.commit()
    await db.refresh(tech)

    r = await client.post("/api/orders", json={
        "location_id": loc, "complaint": "assign me", "assigned_to_id": tech.id}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    notes = (await db.execute(select(StaffNotification).where(
        StaffNotification.employee_id == tech.id))).scalars().all()
    assert any(n.kind == "job.assigned" for n in notes)


@pytest.mark.asyncio
async def test_push_subscribe_unsubscribe(client, tenant):
    key = (await client.get("/api/push/vapid-public-key", headers=tenant)).json()
    assert "enabled" in key and key["enabled"] is False   # no VAPID configured in test
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"},
           "user_agent": "pytest"}
    r = await client.post("/api/push/subscribe", json=sub, headers=tenant)
    assert r.status_code == 201 and r.json()["ok"]
    # re-subscribe same endpoint updates, not duplicates
    r2 = await client.post("/api/push/subscribe", json=sub, headers=tenant)
    assert r2.json()["updated"] is True
    assert (await client.post("/api/push/unsubscribe", json={"endpoint": sub["endpoint"]},
                              headers=tenant)).json()["ok"]


@pytest.mark.asyncio
async def test_auth_refresh_returns_usable_token(client, tenant):
    r = await client.post("/api/auth/refresh", headers=tenant)
    assert r.status_code == 200
    new_tok = r.json()["access_token"]
    assert new_tok
    headers = {"Authorization": f"Bearer {new_tok}"}
    assert (await client.get("/api/tech/dashboard", headers=headers)).status_code == 200
