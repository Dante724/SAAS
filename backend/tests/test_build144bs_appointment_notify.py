"""Build 144bs — appointment confirmations & change notifications are actually
wired. A module="appointment" rule now fires on create, reschedule and status
change (the evaluate_appointment loop was previously never called)."""
import uuid
from datetime import datetime, timedelta


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Bs Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _rule(client, H, event):
    r = await client.post("/api/notifications/rules", json={
        "module": "appointment", "event": event, "channel": "email"}, headers=H)
    assert r.status_code == 201, r.text


async def _logs(client, H):
    return (await client.get("/api/notifications/log", headers=H)).json()["items"]


async def _mk_appointment(client, H, emp_id, cid, hhmm="10:00"):
    day = datetime.utcnow().date().isoformat()
    r = await client.post("/api/appointments", json={
        "client_id": cid, "employee_id": emp_id, "scheduled_for": f"{day}T{hhmm}:00",
        "duration_min": 60}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def test_created_rule_fires_confirmation(client):
    H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    cid = (await client.post("/api/clients", json={"name": "Jo", "email": "jo@x.dev"}, headers=H)).json()["id"]
    await _rule(client, H, "created")
    await _mk_appointment(client, H, me["id"], cid)
    logs = await _logs(client, H)
    hits = [x for x in logs if x["trigger_event"] == "created" and x["recipient"] == "jo@x.dev"]
    assert hits, logs


async def test_reschedule_fires_changed(client):
    H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    cid = (await client.post("/api/clients", json={"name": "Jo", "email": "jo@x.dev"}, headers=H)).json()["id"]
    await _rule(client, H, "changed")
    b = await _mk_appointment(client, H, me["id"], cid, "10:00")
    day = datetime.utcnow().date().isoformat()
    await client.patch(f"/api/appointments/{b['id']}", json={"scheduled_for": f"{day}T14:00:00"}, headers=H)
    logs = await _logs(client, H)
    assert any(x["trigger_event"] == "changed" for x in logs), logs


async def test_cancel_fires_status_changed(client):
    H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    cid = (await client.post("/api/clients", json={"name": "Jo", "email": "jo@x.dev"}, headers=H)).json()["id"]
    await _rule(client, H, "status_changed")
    b = await _mk_appointment(client, H, me["id"], cid, "11:00")
    await client.patch(f"/api/appointments/{b['id']}", json={"status": "canceled"}, headers=H)
    logs = await _logs(client, H)
    cancels = [x for x in logs if x["trigger_event"] == "status_changed"]
    assert cancels and "cancel" in (cancels[0]["subject"] + cancels[0]["body"]).lower(), logs


async def test_no_rule_means_no_send(client):
    # without an appointment rule, creating an appointment must not error and must log nothing
    H = await _tenant(client)
    me = (await client.get("/api/auth/me", headers=H)).json()
    cid = (await client.post("/api/clients", json={"name": "Jo", "email": "jo@x.dev"}, headers=H)).json()["id"]
    await _mk_appointment(client, H, me["id"], cid)
    logs = await _logs(client, H)
    assert all(x["trigger_event"] not in ("created", "changed", "status_changed") for x in logs), logs
