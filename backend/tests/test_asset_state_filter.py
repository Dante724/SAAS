"""Asset list filter: Active / Written off / All (Orderry, Jul 2026). Written-off assets used to be
mixed into the list."""
import pytest


async def _asset(client, h, model):
    r = await client.post("/api/assets", json={"owner_type": "company", "brand": "Bosch", "model": model}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _models(client, h, state=None):
    q = f"?state={state}" if state else ""
    r = await client.get(f"/api/assets{q}", headers=h)
    assert r.status_code == 200, r.text
    return sorted(a["model"] for a in r.json()["items"]), r.json()["total"]


@pytest.mark.asyncio
async def test_state_filter(client, tenant):
    await _asset(client, tenant, "Drill")
    gone = await _asset(client, tenant, "Saw")
    r = await client.post(f"/api/assets/{gone}/write-off", json={"note": "dropped"}, headers=tenant)
    assert r.status_code == 201, r.text

    assert await _models(client, tenant, "active") == (["Drill"], 1)
    assert await _models(client, tenant, "written_off") == (["Saw"], 1)
    assert await _models(client, tenant, "all") == (["Drill", "Saw"], 2)
    # no state keeps the old behaviour (pickers and other callers see everything)
    assert await _models(client, tenant) == (["Drill", "Saw"], 2)


@pytest.mark.asyncio
async def test_unknown_state_rejected(client, tenant):
    r = await client.get("/api/assets?state=sold-ish", headers=tenant)
    assert r.status_code == 422
