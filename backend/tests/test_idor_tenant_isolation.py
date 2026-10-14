"""Security regression: cross-tenant IDOR. A user in org A must not be able to mutate
org B's records by id. Covers close_shift (finance) concretely; the same org-scoping
pattern (parent lookup filtered by org) was applied to sales.set_count and
suppliers.reserve/unreserve_po_line."""
import uuid
import pytest


async def _register(client):
    email = f"idor-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Org B", "name": "Owner B", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_close_shift_cross_tenant_blocked(client, tenant):
    # tenant = org A owner. Build a second org B.
    hb = await _register(client)
    locB = (await client.get("/api/staff/locations", headers=hb)).json()["items"][0]["id"]

    # org B opens a cash shift
    shift = (await client.post("/api/finance/shifts/open", headers=hb,
                               json={"location_id": locB, "opening_float": 0})).json()
    sid = shift["id"]

    # org A (tenant) must NOT be able to close org B's shift → 404 (scoped away)
    a = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=0", headers=tenant)
    assert a.status_code == 404, a.text

    # org B can close its own shift → ok
    b = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=0", headers=hb)
    assert b.status_code == 200, b.text


@pytest.mark.asyncio
async def test_open_shift_foreign_location_blocked(client, tenant):
    # org B's location id must be rejected when org A tries to open a shift on it
    hb = await _register(client)
    locB = (await client.get("/api/staff/locations", headers=hb)).json()["items"][0]["id"]
    r = await client.post("/api/finance/shifts/open", headers=tenant,
                          json={"location_id": locB, "opening_float": 0})
    assert r.status_code == 404, r.text
