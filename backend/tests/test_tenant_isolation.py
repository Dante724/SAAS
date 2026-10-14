"""Tenant isolation: one org must never read another org's data."""
import pytest


async def _register(client, email):
    r = await client.post("/api/auth/register", json={
        "org_name": "Org", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_orders_are_tenant_scoped(client):
    org_a = await _register(client, "a@iso.dev")
    org_b = await _register(client, "b@iso.dev")

    # Each org gets its own location (register seeds a default one); fetch B's.
    loc_b = (await client.get("/api/staff/locations", headers=org_b)).json()["items"][0]["id"]
    created = await client.post("/api/orders",
                                json={"location_id": loc_b, "complaint": "secret B work", "duration_min": 30},
                                headers=org_b)
    assert created.status_code in (200, 201), created.text
    order_b_id = created.json()["id"]

    # Org A must not see B's order in the list…
    a_list = (await client.get("/api/orders", headers=org_a)).json()["items"]
    assert all(o["id"] != order_b_id for o in a_list)

    # …nor fetch it directly.
    direct = await client.get(f"/api/orders/{order_b_id}", headers=org_a)
    assert direct.status_code == 404

    # Org B still sees its own order.
    b_list = (await client.get("/api/orders", headers=org_b)).json()["items"]
    assert any(o["id"] == order_b_id for o in b_list)


@pytest.mark.asyncio
async def test_cross_tenant_location_isolation(client):
    org_a = await _register(client, "a2@iso.dev")
    org_b = await _register(client, "b2@iso.dev")
    locs_a = {loc["id"] for loc in (await client.get("/api/staff/locations", headers=org_a)).json()["items"]}
    locs_b = {loc["id"] for loc in (await client.get("/api/staff/locations", headers=org_b)).json()["items"]}
    assert locs_a and locs_b
    assert locs_a.isdisjoint(locs_b)
