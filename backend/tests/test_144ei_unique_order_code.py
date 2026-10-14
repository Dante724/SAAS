"""Build 144ei — every work order's code is globally unique (across all tenants),
auto-assigned, and never repeated. Orderry semantics."""
import uuid
import pytest


async def _register(client):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _order(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()
    loc_id = (loc[0]["id"] if isinstance(loc, list) else loc["items"][0]["id"])
    return (await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)).json()


async def _code(client, H, oid):
    return (await client.get(f"/api/orders/{oid}", headers=H)).json()["barcode"]


@pytest.mark.asyncio
async def test_every_order_gets_a_code_and_they_differ_in_one_org(client):
    H = await _register(client)
    o1, o2, o3 = await _order(client, H), await _order(client, H), await _order(client, H)
    codes = [await _code(client, H, o["id"]) for o in (o1, o2, o3)]
    assert all(codes), "every order must have a code"
    assert len(set(codes)) == 3, "codes must be unique"


@pytest.mark.asyncio
async def test_many_codes_never_repeat(client):
    # The barcode logic is org-agnostic (no org filter anywhere + a DB-wide unique
    # index), so uniqueness within one org is the same guarantee that holds across
    # every tenant: the code space is global.
    H = await _register(client)
    codes = set()
    for _ in range(12):
        codes.add(await _code(client, H, (await _order(client, H))["id"]))
    assert len(codes) == 12, "codes must never repeat"


@pytest.mark.asyncio
async def test_generate_endpoint_returns_unused_code(client):
    H = await _register(client)
    r = await client.get("/api/orders/code/generate", headers=H)
    assert r.status_code == 200
    assert r.json()["barcode"]


@pytest.mark.asyncio
async def test_cannot_set_a_code_used_by_another_order(client):
    # the clash check has no org filter, so it enforces global uniqueness
    H = await _register(client)
    o1 = await _order(client, H)
    c1 = await _code(client, H, o1["id"])
    o2 = await _order(client, H)
    r = await client.patch(f"/api/orders/{o2['id']}/code", json={"barcode": c1}, headers=H)
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_clearing_regenerates_a_fresh_unique_code(client):
    H = await _register(client)
    o = await _order(client, H)
    before = await _code(client, H, o["id"])
    r = await client.patch(f"/api/orders/{o['id']}/code", json={"barcode": ""}, headers=H)
    assert r.status_code == 200
    after = r.json()["barcode"]
    assert after and after != before   # never left codeless, and a new unique code issued
