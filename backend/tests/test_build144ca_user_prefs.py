"""Build 144ca — server-side saved views (per-user UI prefs).

Verifies GET default is empty, PUT then GET round-trips, and that only the
allowlisted `listview:` key prefix is stored.
"""
import pytest


@pytest.mark.asyncio
async def test_pref_get_default_empty(client, tenant):
    r = await client.get("/api/prefs/listview:client", headers=tenant)
    assert r.status_code == 200
    assert r.json() == {"value": {}}


@pytest.mark.asyncio
async def test_pref_put_then_get_roundtrips(client, tenant):
    payload = {"value": {"cols": ["f1", "f2"], "sort": {"key": "c:Name", "dir": "asc"},
                         "views": [{"name": "Mine", "cols": ["f1"], "sort": None}]}}
    put = await client.put("/api/prefs/listview:product", json=payload, headers=tenant)
    assert put.status_code == 200, put.text
    assert put.json()["ok"] is True

    got = await client.get("/api/prefs/listview:product", headers=tenant)
    assert got.status_code == 200
    assert got.json()["value"] == payload["value"]


@pytest.mark.asyncio
async def test_pref_put_overwrites(client, tenant):
    await client.put("/api/prefs/listview:device", json={"value": {"cols": ["a"]}}, headers=tenant)
    await client.put("/api/prefs/listview:device", json={"value": {"cols": ["b", "c"]}}, headers=tenant)
    got = await client.get("/api/prefs/listview:device", headers=tenant)
    assert got.json()["value"] == {"cols": ["b", "c"]}


@pytest.mark.asyncio
async def test_pref_key_prefix_guarded(client, tenant):
    # non-allowlisted key is rejected on write and returns empty on read
    put = await client.put("/api/prefs/secret:token", json={"value": {"x": 1}}, headers=tenant)
    assert put.status_code == 200
    assert put.json().get("ok") is False
    got = await client.get("/api/prefs/secret:token", headers=tenant)
    assert got.json() == {"value": {}}


@pytest.mark.asyncio
async def test_pref_requires_auth(client):
    assert (await client.get("/api/prefs/listview:client")).status_code == 401
