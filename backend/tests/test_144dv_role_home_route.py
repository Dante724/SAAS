"""144dv — role-redirect: a role's home_route round-trips through the rbac API and
surfaces on /auth/me so the app can redirect that person's landing page."""
import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_home_route_roundtrips_and_shows_on_me(client, tenant, db):
    from backend.models import Role, Employee
    me = (await client.get("/api/auth/me", headers=tenant)).json()

    # Create a role with a landing page, via the rbac API.
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Tech", "permissions": {}, "home_route": "/my-work"}, headers=tenant)
    assert r.status_code == 201, r.text
    role_id = r.json()["id"]

    # It serializes back out.
    roles = (await client.get("/api/settings/rbac/roles", headers=tenant)).json()["items"]
    tech = [x for x in roles if x["id"] == role_id][0]
    assert tech["home_route"] == "/my-work"

    # Attach it to this employee → /auth/me surfaces the landing page.
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.role_id = role_id
    await db.commit()
    me2 = (await client.get("/api/auth/me", headers=tenant)).json()
    assert me2["home_route"] == "/my-work"


@pytest.mark.asyncio
async def test_blank_home_route_means_home(client, tenant):
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Owner2", "permissions": {}, "home_route": ""}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["home_route"] == ""   # blank → default Home, not a stray route
