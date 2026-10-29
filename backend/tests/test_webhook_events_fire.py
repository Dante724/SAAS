"""Webhook events match the public API docs, and every registered event actually fires.

The v1 docs promised `booking.created` and `/bookings` while the code had moved to
appointments; `order.closed` and `estimate.accepted` were registered but nothing emitted them.
"""
import pytest
from sqlalchemy import select

from backend.models import WebhookDelivery, WebhookEndpoint
from backend.services.webhooks import EVENTS, _delivery_name

_PNG = ("data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


async def _org_id(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def _endpoint(db, org_id, events="*"):
    # Inserted directly: the API's public-URL check would try DNS for a test hostname.
    ep = WebhookEndpoint(org_id=org_id, url="https://hooks.example.com/x", secret="s", events=events, active=True)
    db.add(ep)
    await db.commit()
    return ep


async def _events(db, org_id):
    rows = (await db.execute(select(WebhookDelivery).where(WebhookDelivery.org_id == org_id))).scalars().all()
    return [r.event for r in rows]


async def _key(client, h):
    r = await client.post("/api/api-keys", json={"name": "zap", "scopes": "*"}, headers=h)
    body = r.json()
    return {"Authorization": f"Bearer {body.get('key') or body.get('token')}"}


def test_old_booking_name_still_subscribes_to_appointments():
    assert _delivery_name("booking.created", "appointment.created") == "booking.created"
    assert _delivery_name("appointment.created", "appointment.created") == "appointment.created"
    assert _delivery_name("*", "appointment.created") == "appointment.created"
    assert _delivery_name("order.created", "appointment.created") is None
    assert "appointment.created" in EVENTS and "booking.created" not in EVENTS


@pytest.mark.asyncio
async def test_appointment_created_and_bookings_alias(client, tenant, db):
    org = await _org_id(client, tenant)
    await _endpoint(db, org, "appointment.created")
    await _endpoint(db, org, "booking.created")
    auth = await _key(client, tenant)
    from backend.models import Location
    loc_id = (await db.execute(select(Location.id).where(Location.org_id == org))).scalars().first()
    body = {"location_id": loc_id, "scheduled_for": "2099-07-01T14:00:00", "client_name": "Jane"}
    r = await client.post("/api/v1/appointments", json=body, headers=auth)
    assert r.status_code == 201, r.text
    # the old documented path still works
    r = await client.post("/api/v1/bookings", json=body, headers=auth)
    assert r.status_code == 201, r.text
    assert (await client.get("/api/v1/bookings", headers=auth)).json()["total"] == 2
    ev = await _events(db, org)
    assert ev.count("appointment.created") == 2 and ev.count("booking.created") == 2


@pytest.mark.asyncio
async def test_order_closed_fires_once(client, tenant, db):
    org = await _org_id(client, tenant)
    await _endpoint(db, org, "order.closed")
    oid = (await client.post("/api/orders", json={"complaint": "Screen"}, headers=tenant)).json()["id"]
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _events(db, org) == []
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code == 200, r.text
    # closed → closed (done → delivered) is not a second close
    await client.patch(f"/api/orders/{oid}/status", json={"status": "delivered"}, headers=tenant)
    assert await _events(db, org) == ["order.closed"]


@pytest.mark.asyncio
async def test_estimate_accepted_fires(client, tenant, db):
    org = await _org_id(client, tenant)
    await _endpoint(db, org, "estimate.accepted")
    oid = (await client.post("/api/orders", json={"complaint": "Quote", "is_estimate": True},
                             headers=tenant)).json()["id"]
    token = (await client.post(f"/api/orders/{oid}/public-link", headers=tenant)).json()["token"]
    r = await client.post(f"/api/public/track/{token}/respond",
                          json={"decision": "accept", "signature": _PNG, "name": "Jane"})
    assert r.status_code == 200, r.text
    assert await _events(db, org) == ["estimate.accepted"]


@pytest.mark.asyncio
async def test_declined_estimate_does_not_fire(client, tenant, db):
    org = await _org_id(client, tenant)
    await _endpoint(db, org, "estimate.accepted")
    oid = (await client.post("/api/orders", json={"complaint": "Quote", "is_estimate": True},
                             headers=tenant)).json()["id"]
    token = (await client.post(f"/api/orders/{oid}/public-link", headers=tenant)).json()["token"]
    await client.post(f"/api/public/track/{token}/respond", json={"decision": "decline", "name": "Jane"})
    assert await _events(db, org) == []
