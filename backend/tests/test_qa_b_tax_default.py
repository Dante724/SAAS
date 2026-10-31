"""QA 2026-10-31 (B, high): making a tax the default must change what jobs charge.

The default flag (/catalog/taxes is_default) and the job tax stack (/settings/tax-rules
tickets_sales) used to be two unrelated switches: a shop that made HST 13% its default kept
charging the seeded rate (or nothing). They now move together."""
import pytest


async def _shop(client, email, **extra):
    r = await client.post("/api/auth/register", json={
        "org_name": "Shop", "name": "O", "email": email, "password": "pw-12345678", **extra})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _line_tax(client, h):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15559"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", headers=h, json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": 100})
    assert r.status_code in (200, 201), r.text
    o = (await client.get(f"/api/orders/{oid}", headers=h)).json()
    return o["lines"][0]["tax_rate"], o["total"]


async def _stack(client, h):
    return (await client.get("/api/settings/tax-rules", headers=h)).json()["stacks"].get("tickets_sales", [])


async def _taxes(client, h):
    return {t["name"]: t for t in (await client.get("/api/catalog/taxes", headers=h)).json()["items"]}


@pytest.mark.asyncio
async def test_seeded_tax_is_what_jobs_charge(client):
    h = await _shop(client, "ca-seed@qa.dev", region="ca", country="CA")
    gst = (await _taxes(client, h))["GST"]
    assert await _stack(client, h) == [gst["id"]]
    assert await _line_tax(client, h) == (5.0, 105.0)


@pytest.mark.asyncio
async def test_new_default_tax_replaces_seeded_one_on_jobs(client):
    h = await _shop(client, "ca-new@qa.dev", region="ca", country="CA")
    r = await client.post("/api/catalog/taxes", headers=h, json={"name": "HST", "rate": 13, "is_default": True})
    assert r.status_code == 201, r.text
    assert await _stack(client, h) == [r.json()["id"]]
    assert await _line_tax(client, h) == (13.0, 113.0)


@pytest.mark.asyncio
async def test_put_default_moves_the_stack_too(client):
    h = await _shop(client, "us-put@qa.dev", region="us", country="US")
    tx = (await client.post("/api/catalog/taxes", headers=h, json={"name": "TX", "rate": 8.25})).json()
    assert await _line_tax(client, h) == (0.0, 100.0)         # seeded 0% still the default
    r = await client.put(f"/api/catalog/taxes/{tx['id']}", headers=h, json={"name": "TX", "is_default": True})
    assert r.status_code == 200, r.text
    assert await _stack(client, h) == [tx["id"]]
    taxes = await _taxes(client, h)
    assert taxes["TX"]["is_default"] and not taxes["Sales tax"]["is_default"]
    rate, total = await _line_tax(client, h)
    assert rate == 8.25 and total == pytest.approx(108.25)


@pytest.mark.asyncio
async def test_unsetting_default_stops_charging_it(client):
    h = await _shop(client, "ca-off@qa.dev", region="ca", country="CA")
    gst = (await _taxes(client, h))["GST"]
    await client.put(f"/api/catalog/taxes/{gst['id']}", headers=h, json={"name": "GST", "is_default": False})
    assert await _stack(client, h) == []
    assert await _line_tax(client, h) == (0.0, 100.0)


@pytest.mark.asyncio
async def test_saving_the_stack_marks_its_first_tax_default(client):
    h = await _shop(client, "ca-stack@qa.dev", region="ca", country="CA")
    hst = (await client.post("/api/catalog/taxes", headers=h, json={"name": "HST", "rate": 13})).json()
    r = await client.put("/api/settings/tax-rules", headers=h,
                         json={"doc_family": "tickets_sales", "tax_ids": [hst["id"]]})
    assert r.status_code == 200, r.text
    taxes = await _taxes(client, h)
    assert taxes["HST"]["is_default"] and not taxes["GST"]["is_default"]


@pytest.mark.asyncio
async def test_custom_stack_keeps_other_taxes_when_default_changes(client):
    """A shop stacking GST + PST keeps PST when it swaps GST for another default."""
    h = await _shop(client, "ca-mix@qa.dev", region="ca", country="CA")
    gst = (await _taxes(client, h))["GST"]
    pst = (await client.post("/api/catalog/taxes", headers=h, json={"name": "PST", "rate": 7})).json()
    await client.put("/api/settings/tax-rules", headers=h,
                     json={"doc_family": "tickets_sales", "tax_ids": [gst["id"], pst["id"]]})
    hst = (await client.post("/api/catalog/taxes", headers=h, json={"name": "HST", "rate": 13, "is_default": True})).json()
    assert await _stack(client, h) == [hst["id"], pst["id"]]


@pytest.mark.asyncio
async def test_tax_rules_reject_unknown_tax(client, tenant):
    r = await client.put("/api/settings/tax-rules", headers=tenant,
                         json={"doc_family": "tickets_sales", "tax_ids": ["nope"]})
    assert r.status_code == 422, r.text
