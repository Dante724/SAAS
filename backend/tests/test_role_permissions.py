"""/api/auth/me surfaces effective role permissions so the UI can hide denied controls."""
import pytest


@pytest.mark.asyncio
async def test_me_admin_role_denies_nothing(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    # the registering admin gets the built-in "admin" role, which denies nothing,
    # so every can_* code stays allowed and the UI hides nothing
    assert me["role"] == "admin"
    assert not any(v is False for v in me["permissions"].values())


@pytest.mark.asyncio
async def test_me_reflects_explicit_denies(client, tenant):
    role = await client.post("/api/settings/rbac/roles", json={
        "name": "Front desk",
        "permissions": {"can_view_cashflow": False,
                        "can_view_client_info": False,
                        "can_create_tickets": True}}, headers=tenant)
    assert role.status_code == 201, role.text
    rid = role.json()["id"]

    emp = await client.post("/api/staff/employees", json={
        "name": "Desk User", "email": "desk-perm@test.dev",
        "password": "pw-desk-12345", "role_id": rid}, headers=tenant)
    assert emp.status_code == 201, emp.text

    tok = await client.post("/api/auth/token", data={
        "username": "desk-perm@test.dev", "password": "pw-desk-12345"})
    assert tok.status_code == 200, tok.text
    h = {"Authorization": f"Bearer {tok.json()['access_token']}"}

    me = (await client.get("/api/auth/me", headers=h)).json()
    assert me["role"] == "Front desk"
    assert me["is_owner"] is False
    # denied codes come through as False -> frontend hides those controls
    assert me["permissions"]["can_view_cashflow"] is False
    assert me["permissions"]["can_view_client_info"] is False
    # explicitly allowed stays True, and unlisted codes stay absent (= allowed)
    assert me["permissions"]["can_create_tickets"] is True
    assert me["permissions"]["can_create_estimates"] is True    # every code is listed explicitly


@pytest.mark.asyncio
async def test_denied_permission_is_still_enforced_server_side(client, tenant):
    """Hiding is a convenience; the API must refuse regardless of the UI."""
    role = await client.post("/api/settings/rbac/roles", json={
        "name": "No settings", "permissions": {"can_manage_settings": False}},
        headers=tenant)
    rid = role.json()["id"]
    await client.post("/api/staff/employees", json={
        "name": "Limited", "email": "limited-perm@test.dev",
        "password": "pw-lim-12345", "role_id": rid}, headers=tenant)
    tok = await client.post("/api/auth/token", data={
        "username": "limited-perm@test.dev", "password": "pw-lim-12345"})
    h = {"Authorization": f"Bearer {tok.json()['access_token']}"}

    # creating a location requires can_manage_settings -> 403 even if called directly
    r = await client.post("/api/staff/locations", json={"name": "Sneaky"}, headers=h)
    assert r.status_code == 403
