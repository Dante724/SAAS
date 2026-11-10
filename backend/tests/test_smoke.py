"""Smoke tests: the app boots, health is green, auth works, routing is correct."""
import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    # The database check moved from liveness to readiness (test_health_liveness.py).
    ready = await client.get("/api/readyz")
    assert ready.status_code == 200
    assert ready.json()["database"] == "up"


@pytest.mark.asyncio
async def test_health_alias(client):
    assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_register_and_login(client):
    reg = await client.post("/api/auth/register", json={
        "org_name": "Acme", "name": "Owner", "email": "owner@acme.dev", "password": "pw-test-12345"})
    assert reg.status_code == 201, reg.text
    assert "access_token" in reg.json()
    tok = await client.post("/api/auth/token",
                            data={"username": "owner@acme.dev", "password": "pw-test-12345"})
    assert tok.status_code == 200
    assert tok.json()["access_token"]


@pytest.mark.asyncio
async def test_orders_requires_auth(client):
    r = await client.get("/api/orders")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_orders_authed(client, tenant):
    r = await client.get("/api/orders", headers=tenant)
    assert r.status_code == 200
    assert "items" in r.json()


@pytest.mark.asyncio
async def test_unknown_api_path_is_404_not_spa(client):
    r = await client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_bad_login_rejected(client):
    r = await client.post("/api/auth/token",
                          data={"username": "nobody@x.dev", "password": "wrong"})
    assert r.status_code == 401
