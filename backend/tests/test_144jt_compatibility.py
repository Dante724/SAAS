"""144jt — parts↔device compatibility: pure matcher + search flagging + isolation."""
import uuid

from backend.models import Device
from backend.services.compatibility import part_fits


def test_matcher():
    fits = {"fits": [{"brand": "Apple", "model": "iPhone 13"}, {"brand": "Samsung"}]}
    assert part_fits(fits, "Apple", "iPhone 13") is True          # brand+model exact
    assert part_fits(fits, "apple", " iPhone 13 ") is True        # case/space tolerant
    assert part_fits(fits, "Apple", "iPhone 12") is False         # model mismatch
    assert part_fits(fits, "Samsung", "Galaxy S22") is True       # brand-only entry = any model
    assert part_fits(fits, "Google", "Pixel") is False            # unknown brand
    assert part_fits({}, "Apple", "iPhone 13") is False           # no data → not a fit
    assert part_fits({"from_asset": "x"}, "Apple", "iPhone 13") is False  # unrelated key ignored
    assert part_fits(fits, None, None) is False                   # no device brand


async def _org(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]


async def test_search_flags_fit(client, tenant, db):
    org = await _org(client, tenant)
    # a compatible + an incompatible product via the real API (so org scoping is exercised)
    await client.post("/api/catalog/products", headers=tenant, json={
        "name": "Screen for iPhone 13", "sell_price": 100,
        "compatibility_json": {"fits": [{"brand": "Apple", "model": "iPhone 13"}]}})
    await client.post("/api/catalog/products", headers=tenant, json={
        "name": "Generic cable", "sell_price": 5})
    dev = Device(id=uuid.uuid4().hex, org_id=org, brand="Apple", model="iPhone 13")
    db.add(dev)
    await db.commit()

    # /catalog/search flags the fit; without device_id there's no 'fits' key (unchanged behavior)
    def _items(resp):
        return resp if isinstance(resp, list) else resp.get("items", [])
    r = _items((await client.get(f"/api/catalog/search?type=product&device_id={dev.id}", headers=tenant)).json())
    fits_map = {i["name"]: i.get("fits") for i in r if i.get("kind") == "product"}
    assert fits_map.get("Screen for iPhone 13") is True
    assert fits_map.get("Generic cable") is False

    plain = _items((await client.get("/api/catalog/search?type=product", headers=tenant)).json())
    assert all("fits" not in i for i in plain)   # no device_id → additive field absent

    # /inventory/parts compatible_only filters to the fitting product
    inv = (await client.get(f"/api/inventory/parts?device_id={dev.id}&compatible_only=true", headers=tenant)).json()
    names = [i["name"] for i in inv["items"]]
    assert "Screen for iPhone 13" in names and "Generic cable" not in names
