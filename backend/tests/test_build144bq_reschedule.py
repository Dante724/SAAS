"""Build 144bq — drag-reschedule on the scheduler timeline. PATCH /appointments/{id}
moves an appointment in time and/or to another technician, with the same
no-double-appointment guard as create; status-only PATCH still works."""
import uuid
from datetime import datetime, timedelta


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bq Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _emp(client, H):
    return (await client.get("/api/staff/employees", headers=H))


async def _mk(client, H, emp_id, hhmm, dur=60, day=None):
    day = day or datetime.utcnow().date().isoformat()
    r = await client.post("/api/appointments", json={
        "employee_id": emp_id, "scheduled_for": f"{day}T{hhmm}:00",
        "duration_min": dur, "client_name": "Cust"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def test_reschedule_time(client):
    H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    b = await _mk(client, H, me["id"], "10:00")
    day = datetime.utcnow().date().isoformat()
    r = await client.patch(f"/api/appointments/{b['id']}", json={"scheduled_for": f"{day}T14:00:00"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["scheduled_for"].endswith("14:00:00")


async def test_reschedule_clash_blocked(client):
    H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    await _mk(client, H, me["id"], "15:00")          # occupies 15:00-16:00
    b2 = await _mk(client, H, me["id"], "10:00")
    day = datetime.utcnow().date().isoformat()
    # try to move b2 onto the 15:00 slot → clash
    r = await client.patch(f"/api/appointments/{b2['id']}", json={"scheduled_for": f"{day}T15:30:00"}, headers=H)
    assert r.status_code == 409, r.text


async def test_status_only_patch_still_works(client):
    H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    b = await _mk(client, H, me["id"], "11:00")
    r = await client.patch(f"/api/appointments/{b['id']}", json={"status": "no_show"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "no_show"


async def test_empty_patch_rejected(client):
    H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    b = await _mk(client, H, me["id"], "12:00")
    r = await client.patch(f"/api/appointments/{b['id']}", json={}, headers=H)
    assert r.status_code == 400, r.text
