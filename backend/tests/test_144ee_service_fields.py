"""Build 144ee — richer service fields (Orderry New Service parity):
description, type (labor/service), unit, duration, barcode, bookable.
"""
import uuid
import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_create_service_round_trips_new_fields(client):
    H = await _register(client)
    r = await client.post("/api/catalog/services", json={
        "name": "Screen replacement labour",
        "description": "Full teardown + reassembly",
        "is_labor": True,
        "unit": "h",
        "price": 120, "cost_price": 40,
        "duration_min": 90,
        "warranty_days": 90,
        "barcode": "SVC-001",
        "bookable": False,
    }, headers=H)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["description"] == "Full teardown + reassembly"
    assert s["is_labor"] is True
    assert s["unit"] == "h"
    assert s["duration_min"] == 90
    assert s["barcode"] == "SVC-001"
    assert s["bookable"] is False
    assert s["margin_pct"] == 67  # (120-40)/120


@pytest.mark.asyncio
async def test_update_service_changes_new_fields(client):
    H = await _register(client)
    s = (await client.post("/api/catalog/services", json={"name": "Diag"}, headers=H)).json()
    # defaults: service (not labor), unit pc, bookable true, duration 60
    assert s["is_labor"] is False and s["unit"] == "pc" and s["bookable"] is True
    r = await client.put(f"/api/catalog/services/{s['id']}", json={
        "name": "Diag", "is_labor": True, "unit": "visit", "duration_min": 30,
        "bookable": False, "description": "On-site check",
    }, headers=H)
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["is_labor"] is True and u["unit"] == "visit"
    assert u["duration_min"] == 30 and u["bookable"] is False
    assert u["description"] == "On-site check"


@pytest.mark.asyncio
async def test_defaults_are_sane(client):
    H = await _register(client)
    s = (await client.post("/api/catalog/services", json={"name": "Bare"}, headers=H)).json()
    assert s["description"] == ""
    assert s["unit"] == "pc"
    assert s["duration_min"] == 60
    assert s["bookable"] is True
    assert s["is_labor"] is False
