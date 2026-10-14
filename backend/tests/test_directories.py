"""Batch 2: Directories (org-customizable reference lists)."""
import pytest


@pytest.mark.asyncio
async def test_seed_and_system_directories(client, tenant):
    dirs = (await client.get("/api/directories", headers=tenant)).json()["items"]
    keys = {d["key"] for d in dirs}
    assert {"asset_type", "manufacturer", "cancel_reason", "lead_source"} <= keys
    assert all(d["is_system"] for d in dirs if d["key"] == "asset_type")
    # system directories can't be deleted
    sysdir = next(d for d in dirs if d["is_system"])
    r = await client.delete(f"/api/directories/{sysdir['id']}", headers=tenant)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_custom_directory_crud(client, tenant):
    d = await client.post("/api/directories",
                         json={"key": "warranty_provider", "name": "Warranty providers"}, headers=tenant)
    assert d.status_code == 201
    did = d.json()["id"]
    # duplicate key rejected
    dup = await client.post("/api/directories",
                           json={"key": "warranty_provider", "name": "x"}, headers=tenant)
    assert dup.status_code == 409
    # delete works for custom
    assert (await client.delete(f"/api/directories/{did}", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_items_add_list_delete_and_by_key(client, tenant):
    dirs = (await client.get("/api/directories", headers=tenant)).json()["items"]
    manuf = next(d for d in dirs if d["key"] == "manufacturer")
    for v in ("Bosch", "Makita", "DeWalt"):
        r = await client.post(f"/api/directories/{manuf['id']}/items", json={"value": v}, headers=tenant)
        assert r.status_code == 201
    items = (await client.get(f"/api/directories/{manuf['id']}/items", headers=tenant)).json()["items"]
    assert {i["value"] for i in items} == {"Bosch", "Makita", "DeWalt"}

    # by-key resolution (what dropdowns call)
    bk = (await client.get("/api/directories/by-key/manufacturer/items", headers=tenant)).json()
    assert bk["name"] == "Manufacturers" and len(bk["items"]) == 3

    # soft delete
    victim = items[0]["id"]
    assert (await client.delete(f"/api/directories/items/{victim}", headers=tenant)).status_code == 200
    after = (await client.get(f"/api/directories/{manuf['id']}/items", headers=tenant)).json()["items"]
    assert len(after) == 2
