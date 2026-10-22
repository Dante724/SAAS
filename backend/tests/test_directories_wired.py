"""Directories feed real fields (phase 4 usability pass).

Before: five seeded lists, none of which drove the field it was named after (lead source was free
text, cancelling never asked why, payment methods/manufacturers/asset types duplicated other
settings), no rename/reorder, and merging left records pointing at the retired value.
"""
import pytest
from sqlalchemy import select


async def _dirs(client, h):
    return (await client.get("/api/directories", headers=h)).json()


async def _dir(client, h, key):
    return next(d for d in (await _dirs(client, h))["items"] if d["key"] == key)


async def _items(client, h, did, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return (await client.get(f"/api/directories/{did}/items?{q}", headers=h)).json()["items"]


@pytest.mark.asyncio
async def test_only_wired_lists_are_listed_with_defaults_and_uses(client, tenant):
    body = await _dirs(client, tenant)
    keys = {d["key"] for d in body["items"]}
    assert {"lead_source", "cancel_reason"} <= keys
    assert not keys & {"asset_type", "manufacturer", "payment_method"}
    assert {e["name"] for e in body["managed_elsewhere"]} == {"Asset types", "Manufacturers", "Payment methods"}
    ls = await _dir(client, tenant, "lead_source")
    assert ls["item_count"] >= 5 and any("New client" in u for u in ls["used_in"])
    assert "Yelp" in [i["value"] for i in await _items(client, tenant, ls["id"])]


@pytest.mark.asyncio
async def test_rename_and_merge_carry_client_records_along(client, tenant):
    ls = await _dir(client, tenant, "lead_source")
    items = {i["value"]: i for i in await _items(client, tenant, ls["id"])}
    for name in ("A", "B"):
        await client.post("/api/clients", json={"name": name, "phone": "+1555" + name, "source": "Yelp"}, headers=tenant)
    assert {i["value"]: i for i in await _items(client, tenant, ls["id"])}["Yelp"]["uses"] == 2

    r = await client.patch(f"/api/directories/items/{items['Yelp']['id']}", json={"value": "Yelp reviews"}, headers=tenant)
    assert r.status_code == 200 and r.json()["records_updated"] == 2
    r = await client.patch(f"/api/directories/items/{items['Google search']['id']}", json={"value": "yelp REVIEWS"}, headers=tenant)
    assert r.status_code == 409   # case-insensitive duplicate: merge instead

    r = await client.post(f"/api/directories/items/{items['Yelp']['id']}/merge",
                          json={"target_id": items["Google search"]["id"]}, headers=tenant)
    assert r.status_code == 200 and r.json()["records_updated"] == 2
    clients = (await client.get("/api/clients?limit=200", headers=tenant)).json()["items"]
    assert {c["source"] for c in clients if c["name"] in ("A", "B")} == {"Google search"}

    # archived (merged) item can be listed and restored
    archived = [i for i in await _items(client, tenant, ls["id"], include_archived="true") if not i["active"]]
    assert any(i["value"] == "Yelp reviews" for i in archived)
    r = await client.post(f"/api/directories/items/{items['Yelp']['id']}/restore", headers=tenant)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_reorder_and_duplicate_add(client, tenant):
    cr = await _dir(client, tenant, "cancel_reason")
    ids = [i["id"] for i in await _items(client, tenant, cr["id"])]
    r = await client.post(f"/api/directories/{cr['id']}/reorder", json={"ids": list(reversed(ids))}, headers=tenant)
    assert r.status_code == 200
    assert [i["id"] for i in await _items(client, tenant, cr["id"])] == list(reversed(ids))
    r = await client.post(f"/api/directories/{cr['id']}/items", json={"value": "too EXPENSIVE"}, headers=tenant)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_bound_custom_field_keeps_a_retired_list_visible(client, tenant):
    r = await client.post("/api/forms/entity-fields", json={
        "entity_type": "client", "field_label": "Make", "field_type": "select", "directory_key": "manufacturer"},
        headers=tenant)
    assert r.status_code in (200, 201), r.text
    # the shop never had the list; the first inline add creates it and the page then shows it
    r = await client.post("/api/directories/by-key/manufacturer/items", json={"value": "Bosch"}, headers=tenant)
    assert r.status_code == 201, r.text
    body = await _dirs(client, tenant)
    mf = next(d for d in body["items"] if d["key"] == "manufacturer")
    assert any("Make" in u for u in mf["used_in"]) and mf["item_count"] == 1
    assert "Manufacturers" not in {e["name"] for e in body["managed_elsewhere"]}


@pytest.mark.asyncio
async def test_cancel_reason_is_captured_and_reported(client, tenant, db):
    from backend.models import Order
    statuses = (await client.get("/api/statuses", headers=tenant)).json()
    statuses = statuses.get("items", statuses) if isinstance(statuses, dict) else statuses
    dropped = next((s["key"] for s in statuses if s.get("status_group") == "dropped"), None)
    if not dropped:
        pytest.skip("no dropped-group status seeded")
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1999", "source": "Referral"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=tenant)).json()["id"]
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": dropped, "reason": "Too expensive"}, headers=tenant)
    assert r.status_code == 200, r.text
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    await db.refresh(o)
    assert o.cancel_reason == "Too expensive"

    rep = (await client.get("/api/reports/run/cancellations", headers=tenant)).json()
    rows = rep.get("rows") or rep.get("data") or []
    assert any(r[0] == "Too expensive" and r[1] >= 1 for r in rows), rep

    # a payment linked only through its order still counts toward the client's lead source
    from backend.models import Payment
    db.add(Payment(order_id=oid, amount=40, method="cash", kind="payment"))
    await db.commit()
    rep = (await client.get("/api/reports/run/lead_sources", headers=tenant)).json()
    rows = rep.get("rows") or rep.get("data") or []
    ref = next(r for r in rows if r[0] == "Referral")
    assert float(ref[4]) == 40.0, rep


@pytest.mark.asyncio
async def test_first_setup_adopts_free_text_lead_sources(client, tenant, db):
    """Shops typed lead sources freely before the list existed; the first setup keeps them."""
    from backend.models import Client, Directory, DirectoryItem
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org = me["org_id"]
    # an org whose list was never set up, with legacy values already on clients
    d = (await db.execute(select(Directory).where(Directory.org_id == org, Directory.key == "lead_source"))).scalar_one_or_none()
    if d:
        await db.execute(DirectoryItem.__table__.delete().where(DirectoryItem.directory_id == d.id))
    db.add_all([Client(org_id=org, name="L1", source="walk_in"), Client(org_id=org, name="L2", source="google_ads"),
                Client(org_id=org, name="L3", source="Trade show")])
    await db.commit()
    ls = await _dir(client, tenant, "lead_source")
    values = [i["value"] for i in await _items(client, tenant, ls["id"])]
    assert "Walk-in" in values and "Google Ads" in values and "Trade show" in values
    assert "walk_in" not in values
    db.expire_all()
    srcs = {c.name: c.source for c in (await db.execute(select(Client).where(Client.org_id == org, Client.name.in_(["L1", "L2", "L3"])))).scalars()}
    assert srcs == {"L1": "Walk-in", "L2": "Google Ads", "L3": "Trade show"}
