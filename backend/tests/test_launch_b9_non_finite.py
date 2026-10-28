"""Launch blocker B9: one NaN value made the inventory look wiped.

On Postgres a part saved with cost NaN returned 201; after that every inventory list returned
500 (JSON can't carry NaN) and the UI showed "No stock yet". Every money or quantity input now
refuses NaN and Infinity, whether sent as a JSON number, a bare NaN/Infinity token, or a string."""
import pytest

BAD = ["NaN", "nan", "Infinity", "-Infinity", "inf"]


async def _raw(client, method, url, body: str, h):
    return await client.request(method, url, content=body,
                                headers={**h, "Content-Type": "application/json"})


@pytest.mark.asyncio
@pytest.mark.parametrize("value", BAD)
async def test_part_cost_rejects_non_finite_strings(client, tenant, value):
    r = await client.post("/api/inventory/parts", json={"name": "Screen", "cost_price": value}, headers=tenant)
    assert r.status_code == 422, r.text
    r = await client.post("/api/catalog/products", json={"name": "Screen", "cost_price": value}, headers=tenant)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
async def test_part_cost_rejects_bare_json_tokens(client, tenant, token):
    """Python's JSON parser accepts NaN / Infinity tokens (and 1e999 overflows to inf)."""
    r = await _raw(client, "POST", "/api/inventory/parts", f'{{"name": "Screen", "cost_price": {token}}}', tenant)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_inventory_lists_survive_a_rejected_nan(client, tenant):
    await _raw(client, "POST", "/api/inventory/parts", '{"name": "Bad", "cost_price": NaN}', tenant)
    ok = await client.post("/api/inventory/parts", json={"name": "Good", "cost_price": 4.5}, headers=tenant)
    assert ok.status_code == 201, ok.text
    for url in ("/api/inventory/parts", "/api/catalog/products"):
        r = await client.get(url, headers=tenant)
        assert r.status_code == 200, (url, r.text)
        assert "NaN" not in r.text


@pytest.mark.asyncio
async def test_other_money_and_quantity_inputs_reject_nan(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "P"}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/inventory/parts/{pid}/adjust",
                          json={"location_id": loc, "qty": "NaN"}, headers=tenant)
    assert r.status_code == 422, r.text
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    r = await client.patch(f"/api/clients/{cid}", json={"disc_services_value": "Infinity"}, headers=tenant)
    assert r.status_code == 422, r.text
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={"kind": "service", "description": "Fix",
                                                            "qty": 1, "unit_price": "NaN"}, headers=tenant)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_untyped_json_bodies_reject_bare_nan_tokens(client, tenant):
    """Some routes take a plain dict; a bare NaN token is refused before it reaches them."""
    r = await _raw(client, "PATCH", "/api/finance/invoices/x", '{"discount": NaN}', tenant)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_text_that_mentions_nan_is_still_fine(client, tenant):
    r = await client.post("/api/clients", json={"name": "Nan Infinity", "notes": "NaN"}, headers=tenant)
    assert r.status_code in (200, 201), r.text


def test_every_request_model_refuses_non_finite_numbers():
    """The rule is global, so a model added later is covered without anyone remembering it."""
    import main  # noqa: F401 — imports every router
    from pydantic import BaseModel

    def _all(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from _all(sub)

    ours = [m for m in _all(BaseModel) if m.__module__.startswith("backend.")]
    assert len(ours) > 100
    loose = [f"{m.__module__}.{m.__name__}" for m in ours if m.model_config.get("allow_inf_nan", True)]
    assert not loose, loose
