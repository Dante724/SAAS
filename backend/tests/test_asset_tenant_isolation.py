"""Build 144do (security) — asset multi-tenant isolation + per-request org lifecycle.

Regression tests for the audit findings:
  HIGH-1  devices lacked org_id -> cross-tenant IDOR on the assets endpoints.
  MED-2   org suspension was enforced only at login, not per request.
"""
import uuid

import pytest
from sqlalchemy import select


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["org_id"], email


async def _make_company_asset(client, H):
    r = await client.post("/api/assets", json={
        "owner_type": "company", "asset_type": "Device", "brand": "Acme", "model": "X1"},
        headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_company_asset_not_visible_or_editable_cross_tenant(client):
    A, _, _ = await _register(client)
    B, _, _ = await _register(client)
    asset = await _make_company_asset(client, A)

    # A sees it; B does not (list is org-scoped now, not "all company assets")
    a_list = (await client.get("/api/assets", headers=A)).json()
    assert any(x["id"] == asset for x in a_list["items"])
    b_list = (await client.get("/api/assets", headers=B)).json()
    assert all(x["id"] != asset for x in b_list["items"])

    # B cannot read A's asset by id
    assert (await client.get(f"/api/assets/{asset}", headers=B)).status_code == 404
    # …but A can
    assert (await client.get(f"/api/assets/{asset}", headers=A)).status_code == 200

    # B cannot edit, transfer, write off, or read history of A's asset
    assert (await client.put(f"/api/assets/{asset}", json={"brand": "Hacked"}, headers=B)).status_code == 404
    assert (await client.post(f"/api/assets/{asset}/transfer", json={"owner_type": "company"}, headers=B)).status_code in (403, 404)
    assert (await client.post(f"/api/assets/{asset}/write-off", json={}, headers=B)).status_code in (403, 404)
    assert (await client.get(f"/api/tech/device/{asset}/history", headers=B)).status_code == 404

    # A's asset is unchanged
    still = (await client.get(f"/api/assets/{asset}", headers=A)).json()
    assert still["brand"] == "Acme"


@pytest.mark.asyncio
async def test_suspended_org_is_blocked_on_existing_token(client, db):
    from backend.models import Organisation
    H, org_id, _ = await _register(client)

    # token works before suspension
    assert (await client.get("/api/auth/me", headers=H)).status_code == 200

    # suspend the org directly (simulates the platform-admin action)
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.sub_status = "suspended"
    await db.commit()

    # the SAME token is now rejected per-request (not just at next login)
    r = await client.get("/api/auth/me", headers=H)
    assert r.status_code == 403
    assert "suspend" in r.json()["detail"].lower()
