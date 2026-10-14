"""Regression: the shared category tree is used by products, services AND bundles, so
delete_category must refuse to delete a category still referenced by any of them (else
the referencing rows are orphaned). Previously only Part references were checked."""
import pytest


@pytest.mark.asyncio
async def test_service_category_delete_blocked_when_in_use(client, tenant):
    # a service category
    cid = (await client.post("/api/catalog/categories", headers=tenant,
                             json={"entity_type": "service", "name": "Diagnostics"})).json()["id"]
    # a service assigned to it
    r = await client.post("/api/catalog/services", headers=tenant,
                          json={"name": "Full diagnostic", "category_id": cid, "price": 50})
    assert r.status_code in (200, 201), r.text

    # deleting the in-use service category must be refused (would orphan the service)
    d = await client.delete(f"/api/catalog/categories/{cid}", headers=tenant)
    assert d.status_code == 409, d.text


@pytest.mark.asyncio
async def test_unused_category_still_deletable(client, tenant):
    cid = (await client.post("/api/catalog/categories", headers=tenant,
                             json={"entity_type": "service", "name": "Empty cat"})).json()["id"]
    d = await client.delete(f"/api/catalog/categories/{cid}", headers=tenant)
    assert d.status_code == 200, d.text
