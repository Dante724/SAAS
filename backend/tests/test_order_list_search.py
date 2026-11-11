"""The work-order list search box: one box finds a job by its number (with or without the
prefix), the client's name (any part, any case, any word order, or the company they're billed
to) and the client's phone however it's typed. Only the shop's own jobs ever match."""
import uuid

import pytest


async def _shop(client, country="US"):
    r = await client.post("/api/auth/register", json={
        "org_name": f"Shop {uuid.uuid4().hex[:6]}", "name": "Admin", "country": country,
        "email": f"admin-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _client(client, h, **kw):
    r = await client.post("/api/clients", json=kw, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _order(client, h, loc, cid):
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc,
                                               "complaint": "x"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _found(client, h, text):
    r = await client.get("/api/orders", params={"search": text}, headers=h)
    assert r.status_code == 200, r.text
    return {o["order_number"] for o in r.json()["items"]}


@pytest.fixture
async def shop(client):
    h = await _shop(client)
    loc = await _loc(client, h)
    jane = await _client(client, h, name="Jane O'Neill", phone="(415) 555-0134")
    acme = await _client(client, h, name="Acme Plumbing Ltd", client_type="organization",
                         phone="+1 212 555 0199")
    bob = await _client(client, h, name="Bob Stone", phone="020 7946 0958")
    orders = [await _order(client, h, loc, c) for c in (jane, acme, bob)]
    for _ in range(9):                     # pad the sequence so WO-00012 exists
        orders.append(await _order(client, h, loc, bob))
    return h, {o["order_number"]: o for o in orders}


@pytest.mark.asyncio
async def test_order_number_with_and_without_prefix(client, shop):
    h, orders = shop
    assert "WO-00012" in orders
    for typed in ("WO-00012", "wo-00012", "WO00012", "WO12", "wo 12", "#12", "12", "00012"):
        assert await _found(client, h, typed) == {"WO-00012"}, typed
    assert await _found(client, h, "WO-0001") >= {"WO-00010", "WO-00011", "WO-00012"}


@pytest.mark.asyncio
async def test_client_name_partial_any_case_any_order(client, shop):
    h, orders = shop
    jane = await _found(client, h, "jane")
    assert jane == {"WO-00001"}
    for typed in ("JANE", "o'neill", "neill", "Jane O'Neill", "o'neill jane", "  jane   o'n "):
        assert await _found(client, h, typed) == jane, typed
    assert len(await _found(client, h, "acme")) == 1         # company client
    assert len(await _found(client, h, "plumbing ltd")) == 1
    assert await _found(client, h, "jane stone") == set()     # every word must match one client


@pytest.mark.asyncio
async def test_phone_typed_differently_than_stored(client, shop):
    h, _ = shop
    jane = await _found(client, h, "jane")
    acme = await _found(client, h, "acme")
    bob = await _found(client, h, "bob")
    # stored "(415) 555-0134"
    for typed in ("4155550134", "415-555-0134", "415 555 0134", "+1 415 555 0134",
                  "1-415-555-0134", "+14155550134", "555-0134", "(415)555"):
        assert await _found(client, h, typed) == jane, typed
    # stored "+1 212 555 0199" — typed nationally, without the country code
    for typed in ("(212) 555-0199", "2125550199", "212.555.0199", "001 212 555 0199"):
        assert await _found(client, h, typed) == acme, typed
    # stored "020 7946 0958" (national, trunk 0) — typed internationally
    for typed in ("+44 20 7946 0958", "0044 20 7946 0958", "20 7946 0958", "7946-0958"):
        assert await _found(client, h, typed) == bob, typed


@pytest.mark.asyncio
async def test_another_shops_orders_never_match(client, shop):
    h, _ = shop
    other = await _shop(client)
    oloc = await _loc(client, other)
    twin = await _client(client, other, name="Jane O'Neill", phone="(415) 555-0134")
    theirs = await _order(client, other, oloc, twin)
    assert theirs["order_number"] == "WO-00001"
    for typed in ("jane", "4155550134", "+1 415 555 0134", "WO-00001", "1"):
        mine = await _found(client, h, typed)
        ids = {o["id"] for o in (await client.get("/api/orders", params={"search": typed},
                                                  headers=h)).json()["items"]}
        assert theirs["id"] not in ids, typed
        assert len(mine) >= 1
    # and the other shop only sees its own job
    assert await _found(client, other, "jane") == {"WO-00001"}
    assert await _found(client, other, "acme") == set()
    assert await _found(client, other, "WO-00012") == set()


@pytest.mark.asyncio
async def test_board_and_q_alias_use_the_same_search(client, shop):
    h, _ = shop
    r = (await client.get("/api/orders", params={"q": "415-555-0134"}, headers=h)).json()
    assert r["total"] == 1
    b = (await client.get("/api/orders/board", params={"search": "+1 415 555 0134"},
                          headers=h)).json()
    assert sum(len(c["cards"]) for c in b["columns"]) == 1
