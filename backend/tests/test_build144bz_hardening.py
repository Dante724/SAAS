"""Build 144bz — hardening: DB guard against the appointment slot race (R1) and
rate limiting on login + public appointment (R2)."""
import uuid
from datetime import datetime, timedelta


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bz Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_same_slot_same_tech_blocked_by_db(client):
    _, H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    day = datetime.utcnow().date().isoformat()
    body = {"employee_id": me["id"], "scheduled_for": f"{day}T10:00:00", "duration_min": 60, "client_name": "A"}
    r1 = await client.post("/api/appointments", json=body, headers=H)
    assert r1.status_code == 201, r1.text
    # exact same tech + start → unique index rejects with 409 (not a 500)
    r2 = await client.post("/api/appointments", json={**body, "client_name": "B"}, headers=H)
    assert r2.status_code == 409, r2.text


async def test_login_rate_limited(client):
    email, _ = await _tenant(client)
    last = None
    for _ in range(14):
        last = await client.post("/api/auth/token", data={"username": email, "password": "wrong-pw"})
    # after 10 bad tries in the window, the endpoint returns 429 (not 401 forever)
    assert last.status_code == 429, last.status_code


async def test_public_appointment_rate_limited(client):
    email, H = await _tenant(client)
    slug = (await client.get("/api/staff/public-pages", headers=H)).json()["slug"]
    day = (datetime.utcnow().date() + timedelta(days=3)).isoformat()
    codes = []
    for i in range(10):
        r = await client.post(f"/api/public/appointment/{slug}", json={
            "name": f"C{i}", "date": day, "time": "10:00"})
        codes.append(r.status_code)
    assert 429 in codes, codes
