"""144da — Forms Editor system-field layout store round-trips per entity."""
import uuid

import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_layout_defaults_empty_then_saves(client):
    H = await _register(client)
    # unknown-but-valid entity starts empty
    r = await client.get("/api/forms/system-layout?entity=vehicle", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["fields"] == []

    # save a layout
    r = await client.put("/api/forms/system-layout", headers=H, json={
        "entity": "order", "type_id": None,
        "fields": [{"key": "comment", "visible": False, "required": True, "sort": 0},
                   {"key": "client", "visible": True, "required": True, "sort": 1}]})
    assert r.status_code == 200, r.text

    # read it back
    r = await client.get("/api/forms/system-layout?entity=order", headers=H)
    fields = r.json()["fields"]
    assert len(fields) == 2
    by = {f["key"]: f for f in fields}
    assert by["comment"]["visible"] is False and by["comment"]["required"] is True
    assert by["client"]["required"] is True


@pytest.mark.asyncio
async def test_layout_is_upsert_and_entity_scoped(client):
    H = await _register(client)
    await client.put("/api/forms/system-layout", headers=H,
                     json={"entity": "contact", "fields": [{"key": "phone", "visible": True, "required": True, "sort": 0}]})
    # overwrite same entity
    await client.put("/api/forms/system-layout", headers=H,
                     json={"entity": "contact", "fields": [{"key": "email", "visible": False, "required": False, "sort": 0}]})
    r = await client.get("/api/forms/system-layout?entity=contact", headers=H)
    keys = [f["key"] for f in r.json()["fields"]]
    assert keys == ["email"]                     # replaced, not appended
    # a different entity is untouched / empty
    r2 = await client.get("/api/forms/system-layout?entity=product", headers=H)
    assert r2.json()["fields"] == []


@pytest.mark.asyncio
async def test_layout_rejects_unknown_entity(client):
    H = await _register(client)
    r = await client.get("/api/forms/system-layout?entity=nonsense", headers=H)
    assert r.status_code == 400
