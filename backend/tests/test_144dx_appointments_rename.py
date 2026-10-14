"""144dx — Bookings → Appointments rename is complete across API, schema and DB.

Two deliberately different back-compat stances:
- Admin API (internal-only): HARD rename, no alias. Old /api/bookings must 404 as an
  *unknown route* ("Not Found"); the new /api/appointments exists.
- Public API (shareable links / integrations): the old /api/public/booking/* path is
  kept alive as a 308 redirect to /api/public/appointment/* (path + query preserved),
  so links already handed out to customers don't break.
"""
import pytest


@pytest.mark.asyncio
async def test_appointments_route_works_and_old_bookings_is_gone(client, tenant):
    # new admin route exists and lists (org-scoped, empty is fine)
    r = await client.get("/api/appointments", headers=tenant)
    assert r.status_code == 200, r.text
    assert isinstance(r.json().get("items", r.json()), (list, dict))

    # old admin route is an unknown route now (not just an app-level 404)
    old = await client.get("/api/bookings", headers=tenant)
    assert old.status_code == 404
    assert old.json().get("detail") == "Not Found"   # FastAPI route-not-found


@pytest.mark.asyncio
async def test_create_appointment_roundtrips(client, tenant):
    r = await client.post("/api/appointments",
                          json={"client_name": "Test Cust", "scheduled_for": "2099-01-01T10:00:00"},
                          headers=tenant)
    assert r.status_code in (200, 201), r.text
    lst = (await client.get("/api/appointments", headers=tenant)).json()
    items = lst.get("items", lst) if isinstance(lst, dict) else lst
    assert any("Test Cust" in str(x) for x in items)


@pytest.mark.asyncio
async def test_public_appointment_route_exists(client):
    # new public route exists → app-level 404 ("disabled"/"not found"), NOT route-not-found
    new = await client.get("/api/public/appointment/nonexistent-slug")
    assert new.status_code == 404
    assert new.json().get("detail") != "Not Found"


@pytest.mark.asyncio
async def test_old_public_booking_path_redirects_to_appointment(client):
    # Back-compat: old /api/public/booking/* 308-redirects to /api/public/appointment/*
    # (keeps already-shared links / integrations alive). Don't follow — assert the hop.
    r = await client.get("/api/public/booking/some-slug/slots?date=2099-01-01",
                         follow_redirects=False)
    assert r.status_code in (307, 308)
    assert "/api/public/appointment/some-slug/slots" in r.headers.get("location", "")
    assert "date=2099-01-01" in r.headers.get("location", "")   # query preserved


@pytest.mark.asyncio
async def test_old_admin_bookings_route_is_gone(client, tenant):
    # Admin API is internal-only — no alias; the old route is removed.
    old = await client.get("/api/bookings", headers=tenant)
    assert old.status_code == 404
    assert old.json().get("detail") == "Not Found"
