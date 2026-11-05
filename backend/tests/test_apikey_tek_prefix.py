"""Design audit BUG-15: API keys were still issued as `rok_…`, from when the product was RepairOS.

New keys start with `tek_`. Keys already handed out as `rok_…` keep working (only their hash is
stored, so they can't be renamed), and both kinds get their own rate budget.
"""
import pytest
from sqlalchemy import select

from backend.models import ApiKey


async def _make(client, h, name="Zap"):
    r = await client.post("/api/api-keys", json={"name": name, "scopes": "read"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_new_key_starts_with_tek(client, tenant):
    made = await _make(client, tenant)
    assert made["key"].startswith("tek_"), made["key"]
    assert made["prefix"].startswith("tek_") and made["key"].startswith(made["prefix"] + "_"), made
    r = await client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {made['key']}"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_rotated_key_starts_with_tek(client, tenant):
    made = await _make(client, tenant)
    r = await client.post(f"/api/api-keys/{made['id']}/rotate", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["key"].startswith("tek_"), r.json()


@pytest.mark.asyncio
async def test_an_old_rok_key_still_authenticates(client, tenant, db):
    """A key issued before the rename: its stored row (prefix and hash) is left exactly as it was."""
    from backend.services.apikeys import hash_key
    made = await _make(client, tenant)
    legacy = "rok_5f2c9a1e_legacy-secret-from-repairos-days"
    row = (await db.execute(select(ApiKey).where(ApiKey.id == made["id"]))).scalar_one()
    row.prefix, row.key_hash = "rok_5f2c9a1e", hash_key(legacy)
    await db.commit()

    r = await client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200, r.text
    await db.commit()   # fresh read
    row = (await db.execute(select(ApiKey).where(ApiKey.id == made["id"]))).scalar_one()
    assert (row.prefix, row.key_hash) == ("rok_5f2c9a1e", hash_key(legacy))   # nothing rewritten


@pytest.mark.asyncio
async def test_other_bearer_tokens_are_still_not_api_keys(client):
    r = await client.get("/api/v1/contacts", headers={"Authorization": "Bearer xyz_5f2c9a1e_nope"})
    assert r.status_code == 401 and "Not a public API key" in r.text


@pytest.mark.asyncio
async def test_tek_and_rok_keys_both_get_their_own_rate_budget(client, tenant, db, monkeypatch):
    from backend.core import observability
    from backend.services import ratelimit
    from backend.services.apikeys import hash_key
    observability._KEY_CACHE.clear()
    seen = []
    real = ratelimit.aenforce

    async def spy(bucket, key, limit, window):
        if bucket == "global":
            seen.append(key)
        return await real(bucket, key, limit, window)
    monkeypatch.setattr(ratelimit, "aenforce", spy)

    new = await _make(client, tenant, "New")
    old = await _make(client, tenant, "Old")
    legacy = "rok_0badc0de_legacy-secret"
    row = (await db.execute(select(ApiKey).where(ApiKey.id == old["id"]))).scalar_one()
    row.prefix, row.key_hash = "rok_0badc0de", hash_key(legacy)
    await db.commit()

    seen.clear()
    await client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {new['key']}"})
    await client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {legacy}"})
    assert seen == ["k:" + new["id"], "k:" + old["id"]], seen
    seen.clear()
    await client.get("/api/v1/contacts", headers={"Authorization": "Bearer tek_aaaaaaaa_made-up"})
    assert seen and all(k.startswith("ip:") for k in seen), seen
