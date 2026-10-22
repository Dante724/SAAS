"""Intake: tidy identifiers, warn on typos, recognise returning devices, one brand spelling.

Before: identifiers were stored as typed (a lower-case VIN never matched a scan), nothing caught
a mistyped IMEI, every ticket created a new device even for a returning phone, and brand/model
were free text ("Samsung" / "samsung" / "SAMSUNG ").
"""
import pytest
from sqlalchemy import select

from backend.services import identifiers as ids


def test_normalize_problem_and_guess():
    assert ids.normalize("imei", "35-209900 176148-1") == "352099001761481"
    assert ids.normalize("vin", " 1hgcm82633a004352 ") == "1HGCM82633A004352"
    assert ids.normalize("serial", "c02x-l0gt jgh5") == "C02X-L0GTJGH5"
    assert ids.problem("imei", "352099001761481") is None
    assert "check digit" in ids.problem("imei", "352099001761482")
    assert "15 digits" in ids.problem("imei", "3520990017")
    assert ids.problem("vin", "1HGCM82633A004352") is None
    assert "check digit" in ids.problem("vin", "1HGCM82633A004353")
    assert ids.guess_kind("35-209900-176148-1") == "imei"
    assert ids.guess_kind("1HGCM82633A004352") == "vin"
    assert ids.guess_kind("C02XL0GTJGH5") == "serial"


async def _client(client, h, name):
    return (await client.post("/api/clients", json={"name": name, "phone": "+1555" + name}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_order_intake_tidies_and_canonicalises(client, tenant, db):
    from backend.models import Device
    cid = await _client(client, tenant, "Jane")
    r = await client.post("/api/orders", json={"client_id": cid, "asset_brand": "  samsung ",
                                               "asset_model": "galaxy s24 ultra", "asset_imei": "35-209900 176148-1"},
                          headers=tenant)
    assert r.status_code in (200, 201), r.text
    d = (await db.execute(select(Device).where(Device.client_id == cid))).scalar_one()
    assert (d.brand, d.model, d.imei) == ("Samsung", "Galaxy S24 Ultra", "352099001761481")


@pytest.mark.asyncio
async def test_identify_recognises_a_returning_device(client, tenant):
    jane = await _client(client, tenant, "Jane2")
    bob = await _client(client, tenant, "Bob")
    await client.post("/api/orders", json={"client_id": jane, "asset_brand": "Apple", "asset_model": "iPhone 15",
                                           "asset_imei": "352099001761481"}, headers=tenant)
    r = await client.post("/api/assets/identify", json={"value": "35 209900 176148 1", "client_id": jane}, headers=tenant)
    body = r.json()
    assert body["kind"] == "imei" and body["problem"] is None
    m = body["matches"][0]
    assert m["same_client"] is True and m["client"]["name"] == "Jane2" and m["jobs"] == 1
    assert m["last_order"]["order_number"]

    # same device, different customer at the counter -> still found, flagged as someone else's
    other = (await client.post("/api/assets/identify", json={"value": "352099001761481", "client_id": bob},
                               headers=tenant)).json()["matches"][0]
    assert other["same_client"] is False

    typo = (await client.post("/api/assets/identify", json={"value": "352099001761482", "kind": "imei"},
                              headers=tenant)).json()
    assert typo["matches"] == [] and "check digit" in typo["problem"]


@pytest.mark.asyncio
async def test_reusing_the_device_does_not_duplicate_it(client, tenant, db):
    from backend.models import Device
    jane = await _client(client, tenant, "Jane3")
    first = (await client.post("/api/orders", json={"client_id": jane, "asset_serial": "SN-777"}, headers=tenant)).json()
    dev = (await client.post("/api/assets/identify", json={"value": "sn-777"}, headers=tenant)).json()["matches"][0]
    r = await client.post("/api/orders", json={"client_id": jane, "device_id": dev["id"]}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    n = len((await db.execute(select(Device).where(Device.client_id == jane))).scalars().all())
    assert n == 1 and first


@pytest.mark.asyncio
async def test_suggest_lists_builtin_and_learned_names(client, tenant):
    brands = (await client.get("/api/assets/suggest?field=brand&q=sam", headers=tenant)).json()["items"]
    assert "Samsung" in brands
    models = (await client.get("/api/assets/suggest?field=model&brand=apple&q=iphone 16", headers=tenant)).json()["items"]
    assert "iPhone 16 Pro" in models
    # a model typed at intake is remembered for next time
    cid = await _client(client, tenant, "Kim")
    await client.post("/api/orders", json={"client_id": cid, "asset_brand": "Nothing", "asset_model": "Phone (2a)"}, headers=tenant)
    assert "Nothing" in (await client.get("/api/assets/suggest?field=brand&q=noth", headers=tenant)).json()["items"]
    assert "Phone (2a)" in (await client.get("/api/assets/suggest?field=model&brand=nothing", headers=tenant)).json()["items"]


@pytest.mark.asyncio
async def test_scan_matches_regardless_of_how_it_was_typed(client, tenant):
    cid = await _client(client, tenant, "Lee")
    await client.post("/api/orders", json={"client_id": cid, "asset_vin": "1hgcm82633a004352"}, headers=tenant)
    r = (await client.post("/api/scan/resolve", json={"code": "1HGCM82633A004352"}, headers=tenant)).json()
    assert r["matched"] and r["type"] == "asset"
