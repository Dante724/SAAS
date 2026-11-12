"""Audit SEC-08: plain-string `*_id` columns (no foreign key) were stored as sent.

Shop A could save shop B's price list id on a location (probe P8), or an id that is nobody's, and
the flush-time tenant guard never looked. Every soft id a staff write can set is now checked when
it is written: it must name a row of the right kind in the writer's own shop, else 422.
"""
import uuid

import pytest


async def _shop(client):
    email = f"sec08-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Other", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _price_type(client, h, name="Wholesale"):
    r = await client.post("/api/pricing/types", json={"name": name}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _is_unknown_ref(r):
    return r.status_code == 422 and "doesn't exist" in r.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_location_price_list_must_be_this_shops(client, tenant):
    other = await _shop(client)
    foreign = await _price_type(client, other)
    r = await client.post("/api/staff/locations", json={"name": "Annex", "price_type_id": foreign},
                          headers=tenant)
    assert _is_unknown_ref(r), r.text                         # probe P8 was a 201
    r = await client.post("/api/staff/locations", json={"name": "Annex", "price_type_id": "nope"},
                          headers=tenant)
    assert _is_unknown_ref(r), r.text
    mine = await _price_type(client, tenant)
    r = await client.post("/api/staff/locations", json={"name": "Annex", "price_type_id": mine},
                          headers=tenant)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_org_price_and_cash_defaults_must_be_this_shops(client, tenant):
    other = await _shop(client)
    foreign = await _price_type(client, other)
    r = await client.put("/api/pricing/defaults", json={"price_type_tickets_id": foreign}, headers=tenant)
    assert _is_unknown_ref(r), r.text
    r = await client.put("/api/staff/cash-categories/defaults",
                         json={"default_tickets_id": str(uuid.uuid4())}, headers=tenant)
    assert _is_unknown_ref(r), r.text
    mine = await _price_type(client, tenant)
    r = await client.put("/api/pricing/defaults", json={"price_type_tickets_id": mine}, headers=tenant)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_tag_target_must_be_a_record_of_this_shop(client, tenant):
    other = await _shop(client)
    theirs = (await client.post("/api/clients", json={"name": "Theirs"}, headers=other)).json()["id"]
    mine = (await client.post("/api/clients", json={"name": "Mine"}, headers=tenant)).json()["id"]
    tag = (await client.post("/api/tags", json={"name": "VIP"}, headers=tenant)).json()["id"]
    for bad in (theirs, "nope"):
        r = await client.post("/api/tags/attach", json={
            "tag_id": tag, "entity_type": "contact", "entity_id": bad}, headers=tenant)
        assert _is_unknown_ref(r), r.text
    # an order id is not a contact id
    r = await client.post("/api/tags/attach", json={
        "tag_id": tag, "entity_type": "order", "entity_id": mine}, headers=tenant)
    assert _is_unknown_ref(r), r.text
    r = await client.post("/api/tags/attach", json={
        "tag_id": tag, "entity_type": "contact", "entity_id": mine}, headers=tenant)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_tax_exception_target_must_be_this_shops(client, tenant):
    other = await _shop(client)
    theirs = (await client.post("/api/catalog/services", json={"name": "Their fix", "price": 10},
                                headers=other)).json()["id"]
    r = await client.post("/api/settings/tax-exceptions", json={
        "doc_family": "sales", "target_type": "service", "target_id": theirs, "exempt": True},
        headers=tenant)
    assert _is_unknown_ref(r), r.text
    mine = (await client.post("/api/catalog/services", json={"name": "My fix", "price": 10},
                              headers=tenant)).json()["id"]
    r = await client.post("/api/settings/tax-exceptions", json={
        "doc_family": "sales", "target_type": "service", "target_id": mine, "exempt": True},
        headers=tenant)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_unchanged_dangling_soft_id_does_not_block_other_edits(client, tenant, db):
    """Only a soft id being written is checked: a row that already holds a stale one (from before
    this check) can still be edited in other ways."""
    from sqlalchemy import update

    from backend.models import Location
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await db.execute(update(Location).where(Location.id == me["location_id"])
                     .values(price_type_id="deleted-long-ago"))
    await db.commit()
    r = await client.put(f"/api/staff/locations/{me['location_id']}", json={"name": "Renamed"},
                         headers=tenant)
    assert r.status_code == 200, r.text
