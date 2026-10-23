"""Signed-in devices and the shop's per-login device limit."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

from backend.models import AuditLog, AuthSession, Employee
from backend.services import ratelimit
from backend.services.auth import create_access_token
from backend.services.sessions import device_label

PW = "pw-devices-12345"
CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch):
    monkeypatch.setattr(ratelimit, "_redis", False)
    ratelimit._HITS.clear()
    yield
    ratelimit._HITS.clear()


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _register(client, ua=CHROME_MAC):
    email = f"owner-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", headers={"User-Agent": ua}, json={
        "org_name": "Devices Co", "name": "Owner", "email": email, "password": PW})
    assert r.status_code == 201, r.text
    return email, r.json()["access_token"]


async def _login(client, email, ua=SAFARI_IPHONE):
    return await client.post("/api/auth/token", data={"username": email, "password": PW}, headers={"User-Agent": ua})


def test_device_labels():
    assert device_label(CHROME_MAC) == "Chrome on Mac"
    assert device_label(SAFARI_IPHONE) == "Safari on iPhone"
    assert device_label("Mozilla/5.0 (Windows NT 10.0) Gecko/20100101 Firefox/131.0") == "Firefox on Windows"
    assert device_label(None) == "Browser on unknown device"


@pytest.mark.asyncio
async def test_list_and_sign_out_another_device(client):
    email, desk = await _register(client)
    phone = (await _login(client, email)).json()["access_token"]
    items = (await client.get("/api/auth/sessions", headers=_h(desk))).json()["items"]
    assert {i["device"] for i in items} == {"Chrome on Mac", "Safari on iPhone"}
    assert [i["this_device"] for i in items if i["device"] == "Chrome on Mac"] == [True]
    other = next(i for i in items if not i["this_device"])

    # This device can't be signed out from the list (that's the normal Sign out)...
    me = next(i for i in items if i["this_device"])
    assert (await client.post(f"/api/auth/sessions/{me['id']}/sign-out", headers=_h(desk))).status_code == 400
    # ...the other one can, and its token stops working at once.
    assert (await client.post(f"/api/auth/sessions/{other['id']}/sign-out", headers=_h(desk))).status_code == 200
    assert (await client.get("/api/auth/me", headers=_h(phone))).status_code == 401
    assert (await client.get("/api/auth/me", headers=_h(desk))).status_code == 200
    assert len((await client.get("/api/auth/sessions", headers=_h(desk))).json()["items"]) == 1


@pytest.mark.asyncio
async def test_sign_out_all_other_devices_keeps_this_one(client):
    email, desk = await _register(client)
    a = (await _login(client, email)).json()["access_token"]
    b = (await _login(client, email)).json()["access_token"]
    r = await client.post("/api/auth/sessions/sign-out-others", headers=_h(desk))
    assert r.status_code == 200 and r.json()["signed_out"] == 2
    for t in (a, b):
        assert (await client.get("/api/auth/me", headers=_h(t))).status_code == 401
    assert (await client.get("/api/auth/me", headers=_h(desk))).status_code == 200


@pytest.mark.asyncio
async def test_someone_elses_device_is_not_found(client):
    _, mine = await _register(client)
    _, theirs = await _register(client)
    their_id = (await client.get("/api/auth/sessions", headers=_h(theirs))).json()["items"][0]["id"]
    assert (await client.post(f"/api/auth/sessions/{their_id}/sign-out", headers=_h(mine))).status_code == 404
    assert (await client.get("/api/auth/me", headers=_h(theirs))).status_code == 200


@pytest.mark.asyncio
async def test_refresh_keeps_the_device_and_dies_with_it(client):
    email, desk = await _register(client)
    refreshed = (await client.post("/api/auth/refresh", headers=_h(desk))).json()["access_token"]
    assert len((await client.get("/api/auth/sessions", headers=_h(refreshed))).json()["items"]) == 1
    phone = (await _login(client, email)).json()["access_token"]
    desk_id = next(i["id"] for i in (await client.get("/api/auth/sessions", headers=_h(phone))).json()["items"]
                   if i["device"] == "Chrome on Mac")
    await client.post(f"/api/auth/sessions/{desk_id}/sign-out", headers=_h(phone))
    assert (await client.get("/api/auth/me", headers=_h(refreshed))).status_code == 401
    assert (await client.post("/api/auth/refresh", headers=_h(refreshed))).status_code == 401


@pytest.mark.asyncio
async def test_untracked_tokens_work_until_expiry_but_cannot_be_refreshed(client, db):
    email, desk = await _register(client)
    emp = (await db.execute(select(Employee).where(Employee.email == email))).scalar_one()
    legacy = create_access_token({"sub": emp.id, "org_id": emp.org_id, "tv": 0})   # no sid, no row
    assert (await client.get("/api/auth/me", headers=_h(legacy))).status_code == 200
    assert (await client.post("/api/auth/refresh", headers=_h(legacy))).status_code == 401


@pytest.mark.asyncio
async def test_device_limit_picks_a_device_to_sign_out(client, db):
    email, desk = await _register(client)
    assert (await client.put("/api/staff/company", json={"max_sessions_per_login": 7}, headers=_h(desk))).status_code == 422
    assert (await client.put("/api/staff/company", json={"max_sessions_per_login": 2}, headers=_h(desk))).status_code == 200
    phone = (await _login(client, email)).json()["access_token"]          # 2 of 2

    third = await _login(client, email, ua="Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36")
    assert third.status_code == 409 and third.headers.get("x-session-limit") == "1"
    body = third.json()["detail"]
    assert body["limit"] == 2 and {s["device"] for s in body["sessions"]} == {"Chrome on Mac", "Safari on iPhone"}
    assert all(s["ip"] is None for s in body["sessions"])                  # no IPs before a full sign-in
    pending = body["limit_token"]
    assert (await client.get("/api/auth/me", headers=_h(pending))).status_code == 401   # never a session

    phone_id = next(s["id"] for s in body["sessions"] if s["device"] == "Safari on iPhone")
    done = await client.post("/api/auth/session-limit/continue", json={"limit_token": pending, "session_id": phone_id})
    assert done.status_code == 200, done.text
    new = done.json()["access_token"]
    assert (await client.get("/api/auth/me", headers=_h(new))).status_code == 200
    assert (await client.get("/api/auth/me", headers=_h(phone))).status_code == 401
    assert (await client.get("/api/auth/me", headers=_h(desk))).status_code == 200

    emp = (await db.execute(select(Employee).where(Employee.email == email))).scalar_one()
    actions = [a for (a,) in (await db.execute(select(AuditLog.action).where(AuditLog.entity_id == emp.id)))]
    assert "auth.session_limit_hit" in actions and "auth.session_revoked" in actions


@pytest.mark.asyncio
async def test_devices_idle_two_weeks_do_not_count(client, db):
    email, desk = await _register(client)
    await client.put("/api/staff/company", json={"max_sessions_per_login": 2}, headers=_h(desk))
    await _login(client, email)
    emp = (await db.execute(select(Employee).where(Employee.email == email))).scalar_one()
    await db.execute(update(AuthSession).where(AuthSession.employee_id == emp.id)
                     .values(last_seen_at=datetime.utcnow() - timedelta(days=15)))
    await db.commit()
    assert (await _login(client, email)).status_code == 200


@pytest.mark.asyncio
async def test_no_limit_by_default(client):
    email, _ = await _register(client)
    for _ in range(4):
        assert (await _login(client, email)).status_code == 200


@pytest.mark.asyncio
async def test_sign_out_everywhere_clears_the_list(client):
    email, desk = await _register(client)
    await _login(client, email)
    assert (await client.post("/api/auth/logout-all-devices", headers=_h(desk))).status_code == 200
    fresh = (await _login(client, email)).json()["access_token"]
    assert len((await client.get("/api/auth/sessions", headers=_h(fresh))).json()["items"]) == 1


@pytest.mark.asyncio
async def test_admin_sees_and_signs_out_a_teammate(client, db):
    email, owner = await _register(client)
    owner_emp = (await db.execute(select(Employee).where(Employee.email == email))).scalar_one()
    from backend.services.auth import hash_password
    tech_email = f"tech-{uuid.uuid4().hex[:6]}@test.dev"
    tech = Employee(org_id=owner_emp.org_id, name="Tech", email=tech_email, password_hash=hash_password(PW), active=True)
    db.add(tech)
    await db.commit()
    tech_tok = (await _login(client, tech_email)).json()["access_token"]

    listed = await client.get(f"/api/staff/employees/{tech.id}/sessions", headers=_h(owner))
    assert listed.status_code == 200 and len(listed.json()["items"]) == 1
    r = await client.post(f"/api/staff/employees/{tech.id}/sessions/sign-out-all", headers=_h(owner))
    assert r.status_code == 200 and r.json()["signed_out"] == 1
    assert (await client.get("/api/auth/me", headers=_h(tech_tok))).status_code == 401

    _, outsider = await _register(client)
    assert (await client.get(f"/api/staff/employees/{tech.id}/sessions", headers=_h(outsider))).status_code == 404


@pytest.mark.asyncio
async def test_a_lapsed_token_is_not_a_signed_in_device(client, db):
    """The web app doesn't refresh: once a device's 24 h token lapses it's no longer signed in,
    so it isn't listed and doesn't use up the limit (one laptop must not hit a limit of 2)."""
    email, desk = await _register(client)
    await client.put("/api/staff/company", json={"max_sessions_per_login": 2}, headers=_h(desk))
    emp = (await db.execute(select(Employee).where(Employee.email == email))).scalar_one()
    await db.execute(update(AuthSession).where(AuthSession.employee_id == emp.id)
                     .values(token_expires_at=datetime.utcnow() - timedelta(minutes=1)))
    await db.commit()
    first = (await _login(client, email)).json()["access_token"]          # day 2, same laptop
    assert (await _login(client, email)).status_code == 200                  # day 3: 2 live, still fine
    items = (await client.get("/api/auth/sessions", headers=_h(first))).json()["items"]
    assert len(items) == 2


@pytest.mark.asyncio
async def test_refresh_keeps_the_device_signed_in(client, db):
    email, desk = await _register(client)
    emp_id = (await db.execute(select(Employee.id).where(Employee.email == email))).scalar_one()
    soon = datetime.utcnow() + timedelta(minutes=5)
    await db.execute(update(AuthSession).where(AuthSession.employee_id == emp_id).values(token_expires_at=soon))
    await db.commit()
    assert (await client.post("/api/auth/refresh", headers=_h(desk))).status_code == 200
    db.expire_all()
    row = (await db.execute(select(AuthSession).where(AuthSession.employee_id == emp_id))).scalar_one()
    assert row.token_expires_at > soon + timedelta(hours=1)
