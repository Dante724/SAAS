"""Build 144ci — the per-user language preference persists via /api/prefs/lang."""
import pytest


@pytest.mark.asyncio
async def test_lang_pref_roundtrips(client, tenant):
    put = await client.put("/api/prefs/lang", json={"value": {"lang": "es"}}, headers=tenant)
    assert put.status_code == 200 and put.json()["ok"] is True
    got = await client.get("/api/prefs/lang", headers=tenant)
    assert got.json()["value"] == {"lang": "es"}


@pytest.mark.asyncio
async def test_lang_default_empty(client, tenant):
    got = await client.get("/api/prefs/lang", headers=tenant)
    assert got.json() == {"value": {}}


@pytest.mark.asyncio
async def test_unrelated_key_still_guarded(client, tenant):
    # the allowlist widening for 'lang' must not open arbitrary keys
    put = await client.put("/api/prefs/secret", json={"value": {"x": 1}}, headers=tenant)
    assert put.json().get("ok") is False
