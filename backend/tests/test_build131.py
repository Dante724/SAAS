"""Build 131 — the full roles & permissions matrix is wired to the rbac catalog."""
import pytest


@pytest.mark.asyncio
async def test_permission_catalog_is_rich(client, tenant):
    d = (await client.get("/api/settings/rbac/catalog", headers=tenant)).json()
    cat = d["catalog"]
    total = sum(len(v) for v in cat.values())
    assert total >= 40                       # the editor exposes the whole app, not ~8 toggles
    # every entry has a code + label the UI renders
    for perms in cat.values():
        for p in perms:
            assert p["code"] and p["label"]


@pytest.mark.asyncio
async def test_create_and_edit_role_with_denied_permission(client, tenant):
    # create a role that denies deleting invoices, allows the rest
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Front desk", "permissions": {"can_delete_invoices": False}}, headers=tenant)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["permissions"]["can_delete_invoices"] is False

    # it comes back in the list with the denial intact
    roles = (await client.get("/api/settings/rbac/roles", headers=tenant)).json()["items"]
    role = next(x for x in roles if x["id"] == rid)
    assert role["permissions"]["can_delete_invoices"] is False

    # edit: re-allow it
    up = await client.put(f"/api/settings/rbac/roles/{rid}", json={
        "name": "Front desk", "permissions": {"can_delete_invoices": True}}, headers=tenant)
    assert up.status_code == 200, up.text
    assert up.json()["permissions"]["can_delete_invoices"] is True


@pytest.mark.asyncio
async def test_unknown_permission_rejected(client, tenant):
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Bad", "permissions": {"can_do_magic": False}}, headers=tenant)
    assert r.status_code == 422
