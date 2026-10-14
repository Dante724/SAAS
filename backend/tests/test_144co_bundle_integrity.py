"""144co — product/service/bundle integrity: delete guards, org-scoped components, broken-component
flag, and where-used. Protects the catalog so bundles never silently orphan or leak cross-tenant."""
import uuid

import pytest


async def _svc(client, h, name="Svc A", price=300):
    return (await client.post("/api/catalog/services", json={"name": name, "price": price}, headers=h)).json()["id"]


async def _part(client, h, name="Part A"):
    return (await client.post("/api/inventory/parts", json={"name": name, "sku": "SKU-" + uuid.uuid4().hex[:6]}, headers=h)).json()["id"]


async def _bundle(client, h, comps, name="Bundle A"):
    return (await client.post("/api/bundles", json={"name": name, "components": comps}, headers=h)).json()


@pytest.mark.asyncio
async def test_service_delete_guarded_when_in_bundle(client, tenant):
    sid = await _svc(client, tenant)
    await _bundle(client, tenant, [{"ref_type": "service", "ref_id": sid, "qty": 1}])
    r = await client.delete(f"/api/catalog/services/{sid}", headers=tenant)
    assert r.status_code == 409, r.text
    assert "bundle" in r.text.lower()
    sid2 = await _svc(client, tenant, name="Free Svc")
    assert (await client.delete(f"/api/catalog/services/{sid2}", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_part_bulk_delete_skips_in_use(client, tenant):
    used = await _part(client, tenant, name="Used Part")
    free = await _part(client, tenant, name="Free Part")
    await _bundle(client, tenant, [{"ref_type": "product", "ref_id": used, "qty": 1}], name="Kit P")
    r = await client.post("/api/catalog/products/bulk", json={"ids": [used, free], "action": "delete"}, headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1 and body.get("blocked"), body
    # the in-use part is still resolvable in the bundle (not deleted)
    bundles = (await client.get("/api/bundles", headers=tenant)).json()["items"]
    kit = [b for b in bundles if b["name"] == "Kit P"][0]
    assert kit["components"][0]["name"] == "Used Part"


@pytest.mark.asyncio
async def test_bundle_rejects_foreign_component(client, tenant):
    r = await client.post("/api/bundles", json={
        "name": "Bad", "components": [{"ref_type": "product", "ref_id": "nonexistent-id", "qty": 1}]}, headers=tenant)
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_missing_component_flagged(client, tenant, db):
    sid = await _svc(client, tenant, name="Vanish Svc")
    await _bundle(client, tenant, [{"ref_type": "service", "ref_id": sid, "qty": 1}], name="Kit M")
    from sqlalchemy import delete as sa_delete

    from backend.models import Service
    await db.execute(sa_delete(Service).where(Service.id == sid))
    await db.commit()
    bundles = (await client.get("/api/bundles", headers=tenant)).json()["items"]
    kit = [b for b in bundles if b["name"] == "Kit M"][0]
    assert kit["components"][0]["missing"] is True


@pytest.mark.asyncio
async def test_where_used(client, tenant):
    sid = await _svc(client, tenant, name="Pop Svc")
    await _bundle(client, tenant, [{"ref_type": "service", "ref_id": sid, "qty": 1}], name="Uses It")
    r = await client.get(f"/api/bundles/using?ref_type=service&ref_id={sid}", headers=tenant)
    assert r.status_code == 200, r.text
    assert "Uses It" in [x["name"] for x in r.json()["items"]]
