"""QA fix E (follow-up) — transfers check their location and product ids.

Found in the 5-shop QA run (31 Oct 2026, shop 5 #10): POST /api/transfers with an unknown
location or part, and a cancel with an unknown return_location_id, were 500s (only the
database foreign key stopped them), and another shop's real ids were accepted.
"""
import pytest

from backend.models import Location, Organisation, Part


async def _setup(client, h):
    locs = (await client.get("/api/staff/locations", headers=h)).json()
    a = (locs["items"] if isinstance(locs, dict) else locs)[0]["id"]
    b = (await client.post("/api/staff/locations", json={"name": "Back store"}, headers=h)).json()["id"]
    part = (await client.post("/api/inventory/parts", json={"name": "Brake pad", "sku": "BP-1"},
                              headers=h)).json()["id"]
    return a, b, part


async def _foreign(db):
    other = Organisation(name="Other shop")
    db.add(other)
    await db.flush()
    loc, part = Location(org_id=other.id, name="Their shop"), Part(org_id=other.id, name="Their part")
    db.add_all([loc, part])
    await db.commit()
    return loc.id, part.id


@pytest.mark.asyncio
async def test_unknown_or_foreign_location_is_404(client, tenant, db):
    a, b, part = await _setup(client, tenant)
    their_loc, _ = await _foreign(db)
    for bad in ("no-such-location", their_loc):
        for body in ({"from_location_id": bad, "to_location_id": b},
                     {"from_location_id": a, "to_location_id": bad}):
            r = await client.post("/api/transfers", json={**body, "lines": [{"part_id": part, "qty": 1}]},
                                  headers=tenant)
            assert r.status_code == 404, r.text
            assert r.json()["detail"]["message"] == "That location doesn't exist any more. Pick another."


@pytest.mark.asyncio
async def test_warehouse_id_as_location_names_the_location(client, tenant):
    a, b, part = await _setup(client, tenant)
    wh = next(w for w in (await client.get("/api/settings/warehouses", headers=tenant)).json()["items"]
              if w.get("location_id") == a)
    r = await client.post("/api/transfers", json={"from_location_id": wh["id"], "to_location_id": b,
                                                  "lines": [{"part_id": part, "qty": 1}]}, headers=tenant)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["location_id"] == a


@pytest.mark.asyncio
async def test_unknown_or_foreign_part_is_404_and_nothing_is_saved(client, tenant, db):
    a, b, part = await _setup(client, tenant)
    _, their_part = await _foreign(db)
    for bad in ("no-such-part", their_part):
        r = await client.post("/api/transfers", json={"from_location_id": a, "to_location_id": b, "lines": [
            {"part_id": part, "qty": 1}, {"part_id": bad, "qty": 1}]}, headers=tenant)
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["unknown_ids"] == [bad]
    listed = (await client.get("/api/transfers", headers=tenant)).json()
    listed = listed.get("items", listed) if isinstance(listed, dict) else listed
    assert listed == []
    ok = await client.post("/api/transfers", json={"from_location_id": a, "to_location_id": b,
                                                   "lines": [{"part_id": part, "qty": 1}]}, headers=tenant)
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio
async def test_cancel_with_unknown_return_location_is_404(client, tenant, db):
    a, b, part = await _setup(client, tenant)
    await client.post(f"/api/inventory/parts/{part}/adjust", json={"location_id": a, "qty": 5}, headers=tenant)
    t = (await client.post("/api/transfers", json={"from_location_id": a, "to_location_id": b,
                                                   "lines": [{"part_id": part, "qty": 2}]}, headers=tenant)).json()
    for st in ("ready", "in_transit"):
        r = await client.patch(f"/api/transfers/{t['id']}/status", json={"status": st}, headers=tenant)
        assert r.status_code == 200, r.text
    their_loc, _ = await _foreign(db)
    for bad in ("no-such-location", their_loc):
        r = await client.patch(f"/api/transfers/{t['id']}/status",
                               json={"status": "canceled", "return_location_id": bad}, headers=tenant)
        assert r.status_code == 404, r.text
    # the transfer is still in transit (the refused cancel claimed nothing) and can be cancelled properly
    r = await client.patch(f"/api/transfers/{t['id']}/status",
                           json={"status": "canceled", "return_location_id": a}, headers=tenant)
    assert r.status_code == 200, r.text
