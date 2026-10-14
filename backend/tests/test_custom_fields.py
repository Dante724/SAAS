"""Org custom fields for client + asset entities, and their values persisting
through the client / device / work-order create paths."""
import pytest


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def test_entity_field_crud(client, tenant):
    # define, list, reorder, delete a client field
    r = await client.post("/api/forms/entity-fields",
                          json={"entity_type": "client", "field_label": "GSTIN", "field_type": "text"}, headers=tenant)
    assert r.status_code == 201
    fid = r.json()["id"]
    items = (await client.get("/api/forms/entity-fields?entity=client", headers=tenant)).json()["items"]
    assert [f["field_label"] for f in items] == ["GSTIN"]
    # bad entity rejected
    assert (await client.get("/api/forms/entity-fields?entity=order", headers=tenant)).status_code == 400
    assert (await client.patch("/api/forms/entity-fields/reorder?entity=client",
                               json={"ordered_ids": [fid]}, headers=tenant)).status_code == 200
    assert (await client.request("DELETE", f"/api/forms/fields/{fid}", headers=tenant)).status_code == 200
    assert (await client.get("/api/forms/entity-fields?entity=client", headers=tenant)).json()["items"] == []


async def test_client_custom_data_roundtrip_and_partial_edit(client, tenant):
    ck = (await client.post("/api/forms/entity-fields",
          json={"entity_type": "client", "field_label": "GSTIN", "field_type": "text"}, headers=tenant)).json()["field_key"]
    cid = (await client.post("/api/clients",
           json={"name": "Acme", "custom_data": {ck: "29ABCDE1234F1Z5"}}, headers=tenant)).json()["id"]
    got = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()
    assert got["custom_data"] == {ck: "29ABCDE1234F1Z5"}
    # a partial edit (no consent, no custom_data) must not 500 or wipe custom values
    r = await client.put(f"/api/clients/{cid}", json={"name": "Acme Corp"}, headers=tenant)
    assert r.status_code == 200
    assert (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["custom_data"] == {ck: "29ABCDE1234F1Z5"}


async def test_device_and_order_asset_custom_data(client, tenant):
    dk = (await client.post("/api/forms/entity-fields",
          json={"entity_type": "device", "field_label": "Warranty months", "field_type": "number"}, headers=tenant)).json()["field_key"]
    cid = (await client.post("/api/clients", json={"name": "Bo"}, headers=tenant)).json()["id"]
    # asset form path
    await client.post(f"/api/clients/{cid}/devices",
                      json={"brand": "Dell", "model": "XPS", "custom_data": {dk: 24}}, headers=tenant)
    dev = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["devices"][0]
    assert dev["custom_data"] == {dk: 24}
    # work-order create path (asset_custom_data → new device)
    loc = await _loc(client, tenant)
    oid = (await client.post("/api/orders",
           json={"client_id": cid, "location_id": loc, "complaint": "x",
                 "asset_brand": "HP", "asset_model": "Elite", "asset_custom_data": {dk: 12}}, headers=tenant)).json()["id"]
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["device"]["custom_data"] == {dk: 12}
