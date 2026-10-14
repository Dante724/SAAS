"""Build 144ax — bundle categories: tree counts by bundle, filter, and the
category_id round-trips through the bundle create/update path."""
import pytest


async def test_bundle_category_tree_counts_and_filter(client, tenant):
    # a product to use as a bundle component
    pid = (await client.post("/api/catalog/products", json={"name": "Widget"}, headers=tenant)).json()["id"]
    # bundle category
    cat = (await client.post("/api/catalog/categories",
           json={"entity_type": "bundle", "name": "Kits"}, headers=tenant)).json()["id"]
    # bundle in that category
    b = (await client.post("/api/bundles", json={
        "name": "Repair kit", "category_id": cat,
        "components": [{"ref_type": "product", "ref_id": pid, "qty": 2}]}, headers=tenant)).json()
    assert b["category_id"] == cat
    # the bundle tree counts bundles (not products) in the category
    tree = (await client.get("/api/catalog/categories?entity_type=bundle", headers=tenant)).json()["tree"]
    node = next(n for n in tree if n["id"] == cat)
    assert node["count"] == 1
    # filter bundles by category
    got = (await client.get(f"/api/bundles?category_id={cat}", headers=tenant)).json()["items"]
    assert [x["name"] for x in got] == ["Repair kit"]
    # moving to no category clears it
    await client.put(f"/api/bundles/{b['id']}", json={
        "name": "Repair kit", "components": [{"ref_type": "product", "ref_id": pid, "qty": 2}]}, headers=tenant)
    again = (await client.get("/api/bundles", headers=tenant)).json()["items"][0]
    assert again["category_id"] is None
