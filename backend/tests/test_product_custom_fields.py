"""Build 144as — product custom fields (Settings → Forms → Products) and their
values persisting through the catalog product create/update paths."""
import pytest


async def test_product_entity_field_crud(client, tenant):
    # product is now a valid custom-field entity
    r = await client.post("/api/forms/entity-fields",
                          json={"entity_type": "product", "field_label": "Grade", "field_type": "select",
                                "options": ["A", "B", "OEM"]}, headers=tenant)
    assert r.status_code == 201
    fid = r.json()["id"]
    items = (await client.get("/api/forms/entity-fields?entity=product", headers=tenant)).json()["items"]
    assert [f["field_label"] for f in items] == ["Grade"]
    # new field types accepted
    assert (await client.post("/api/forms/entity-fields",
            json={"entity_type": "product", "field_label": "Installed at", "field_type": "datetime"},
            headers=tenant)).status_code == 201
    assert (await client.post("/api/forms/entity-fields",
            json={"entity_type": "product", "field_label": "Origin", "field_type": "address"},
            headers=tenant)).status_code == 201
    # reorder + delete
    assert (await client.patch("/api/forms/entity-fields/reorder?entity=product",
                               json={"ordered_ids": [fid]}, headers=tenant)).status_code == 200
    assert (await client.request("DELETE", f"/api/forms/fields/{fid}", headers=tenant)).status_code == 200


async def test_product_custom_data_roundtrip_and_partial_edit(client, tenant):
    ck = (await client.post("/api/forms/entity-fields",
          json={"entity_type": "product", "field_label": "Grade", "field_type": "text"}, headers=tenant)).json()["field_key"]
    pid = (await client.post("/api/catalog/products",
           json={"name": "Screen X", "custom_data": {ck: "OEM"}}, headers=tenant)).json()["id"]
    got = (await client.get(f"/api/catalog/products/{pid}", headers=tenant)).json()["product"]
    assert got["custom_data"] == {ck: "OEM"}
    # partial edit that omits custom_data must not wipe it
    r = await client.put(f"/api/catalog/products/{pid}",
                         json={"name": "Screen X2", "sell_price": 99}, headers=tenant)
    assert r.status_code == 200
    again = (await client.get(f"/api/catalog/products/{pid}", headers=tenant)).json()["product"]
    assert again["custom_data"] == {ck: "OEM"}
    # explicit update replaces values
    await client.put(f"/api/catalog/products/{pid}",
                     json={"name": "Screen X2", "custom_data": {ck: "A-grade"}}, headers=tenant)
    final = (await client.get(f"/api/catalog/products/{pid}", headers=tenant)).json()["product"]
    assert final["custom_data"] == {ck: "A-grade"}


async def test_product_required_field_enforced(client, tenant):
    ck = (await client.post("/api/forms/entity-fields",
          json={"entity_type": "product", "field_label": "Grade", "field_type": "text", "required": True},
          headers=tenant)).json()["field_key"]
    # create without the required value → 422
    r = await client.post("/api/catalog/products", json={"name": "NoGrade"}, headers=tenant)
    assert r.status_code == 422
    # create with it → ok
    pid = (await client.post("/api/catalog/products",
           json={"name": "HasGrade", "custom_data": {ck: "A"}}, headers=tenant)).json()["id"]
    # blanking it on a full update → 422
    r = await client.put(f"/api/catalog/products/{pid}",
                         json={"name": "HasGrade", "custom_data": {ck: ""}}, headers=tenant)
    assert r.status_code == 422
    # partial update that omits custom_data is still allowed (doesn't re-validate)
    assert (await client.put(f"/api/catalog/products/{pid}",
            json={"name": "HasGrade2"}, headers=tenant)).status_code == 200


async def test_product_lock_after_create(client, tenant):
    r = (await client.post("/api/forms/entity-fields",
         json={"entity_type": "product", "field_label": "Serial batch", "field_type": "text",
               "lock_after_create": True}, headers=tenant)).json()
    ck = r["field_key"]
    assert r["lock_after_create"] is True
    pid = (await client.post("/api/catalog/products",
           json={"name": "Locked", "custom_data": {ck: "B-100"}}, headers=tenant)).json()["id"]
    # attempt to change the locked value → ignored, stays as set
    await client.put(f"/api/catalog/products/{pid}",
                     json={"name": "Locked", "custom_data": {ck: "HACKED"}}, headers=tenant)
    got = (await client.get(f"/api/catalog/products/{pid}", headers=tenant)).json()["product"]
    assert got["custom_data"] == {ck: "B-100"}
