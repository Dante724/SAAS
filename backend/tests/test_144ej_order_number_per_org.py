"""Build 144ej — order numbers are unique PER ORG, not globally (Orderry scopes
numbering to the account). Two tenants can each hold their own "WO-00001"."""
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
    r = await client.post("/api/orders", json={"location_id": loc_id, "complaint": "x"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_two_tenants_can_both_have_their_first_order(client):
    # This is the exact collision the global-unique constraint caused.
    H1 = await _register(client)
    H2 = await _register(client)
    o1 = await _order(client, H1)
    o2 = await _order(client, H2)   # must NOT 500 on a duplicate order_number
    assert o1["order_number"] == o2["order_number"], "each org numbers from its own sequence"


@pytest.mark.asyncio
async def test_order_number_still_unique_within_one_org(client):
    H = await _register(client)
    nums = [(await _order(client, H))["order_number"] for _ in range(3)]
    assert len(set(nums)) == 3, "within an org, order numbers never repeat"


@pytest.mark.asyncio
async def test_scannable_codes_are_unique_across_tenants(client):
    # 144ei + 144ej together: order NUMBERS may repeat per-org, but the scannable
    # CODE (barcode) is globally unique — different tenants never share one.
    H1 = await _register(client)
    H2 = await _register(client)
    codes = set()
    for _ in range(4):
        for H in (H1, H2):
            o = await _order(client, H)
            codes.add((await client.get(f"/api/orders/{o['id']}", headers=H)).json()["barcode"])
    assert len(codes) == 8, "scannable codes must be unique across every tenant"
