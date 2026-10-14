"""144cy — a job kit (bundle) can have its components set on create AND changed on edit.
Backend already supports it; this locks the contract the editor relies on."""
import pytest


async def _svc(client, h, name, price):
    return (await client.post("/api/catalog/services", json={"name": name, "price": price}, headers=h)).json()["id"]


async def _prod(client, h, name, price):
    return (await client.post("/api/catalog/products", json={"name": name, "sell_price": price}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_create_then_edit_components(client, tenant):
    s1 = await _svc(client, tenant, "Diagnostic", 50)
    p1 = await _prod(client, tenant, "Screen", 300)
    s2 = await _svc(client, tenant, "Fitting", 80)

    # create with two components
    created = (await client.post("/api/bundles", json={
        "name": "Screen combo", "price_mode": "sum", "price": 0,
        "components": [{"ref_type": "service", "ref_id": s1, "qty": 1},
                       {"ref_type": "product", "ref_id": p1, "qty": 1}],
    }, headers=tenant))
    assert created.status_code == 201, created.text
    bid = created.json()["id"]
    assert {c["ref_id"] for c in created.json()["components"]} == {s1, p1}

    # edit: drop the product, add a second service, bump qty
    edited = await client.put(f"/api/bundles/{bid}", json={
        "name": "Screen combo v2", "price_mode": "fixed", "price": 400,
        "components": [{"ref_type": "service", "ref_id": s1, "qty": 2},
                       {"ref_type": "service", "ref_id": s2, "qty": 1}],
    }, headers=tenant)
    assert edited.status_code == 200, edited.text

    # GET reflects the new component set
    got = next(b for b in (await client.get("/api/bundles", headers=tenant)).json()["items"] if b["id"] == bid)
    assert got["name"] == "Screen combo v2" and got["price_mode"] == "fixed"
    comps = {c["ref_id"]: c["qty"] for c in got["components"]}
    assert comps == {s1: 2, s2: 1}, comps
