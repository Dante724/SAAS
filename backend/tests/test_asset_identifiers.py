"""VIN/HIN/IMEI lookups + industry templates + identifier persistence."""
import pytest

VALID_VIN = "1M8GDM9AXKP042788"    # classic check-digit-valid example
VALID_IMEI = "490154203237518"     # classic Luhn-valid example
VALID_HIN = "BWCE1234C404"


@pytest.mark.asyncio
async def test_vin_lookup_structural(client, tenant, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "VIN_LOOKUP_LIVE", False)   # deterministic offline
    r = (await client.get(f"/api/integrations/lookup/vin/{VALID_VIN}", headers=tenant)).json()
    assert r["valid"] and r["kind"] == "vin" and r["region"] == "North America"
    bad = (await client.get(f"/api/integrations/lookup/vin/{VALID_VIN[:-1]}0", headers=tenant)).json()
    assert bad["valid"] is False and "check digit" in bad["error"].lower()


@pytest.mark.asyncio
async def test_hin_and_imei_lookup(client, tenant):
    h = (await client.get(f"/api/integrations/lookup/hin/{VALID_HIN}", headers=tenant)).json()
    assert h["valid"] and h["mic"] == "BWC" and h["model_year"] == 2004 and h["manufacturer"] == "Boston Whaler"
    i = (await client.get(f"/api/integrations/lookup/imei/{VALID_IMEI}", headers=tenant)).json()
    assert i["valid"] and i["tac"] == "49015420"
    bad = (await client.get("/api/integrations/lookup/imei/490154203237519", headers=tenant)).json()
    assert bad["valid"] is False


@pytest.mark.asyncio
async def test_legacy_templates_endpoint_retired(client, tenant):
    # the old /integrations/lookup/templates registry is retired; templates now
    # come from the single source of truth at /api/settings/industry-template
    assert (await client.get("/api/integrations/lookup/templates", headers=tenant)).status_code == 404
    new = (await client.get("/api/settings/industry-template", headers=tenant)).json()
    assert new["current"] == "general" and len(new["templates"]) == 10
    # switching still works via the company endpoint (unified registry)
    ok = await client.put("/api/staff/company", json={"industry_template": "marine"}, headers=tenant)
    assert ok.status_code == 200
    assert (await client.get("/api/staff/company", headers=tenant)).json()["industry_template"] == "marine"


@pytest.mark.asyncio
async def test_device_persists_vin_hin(client, tenant):
    from sqlalchemy import select
    cl = await client.post("/api/clients", json={"name": "Marina Max"}, headers=tenant)
    cid = cl.json()["id"]
    d = await client.post(f"/api/clients/{cid}/devices", json={
        "brand": "Boston Whaler", "model": "Montauk", "hin": VALID_HIN, "asset_type": "Marine"}, headers=tenant)
    assert d.status_code in (200, 201), d.text
    devs = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()["devices"]
    assert devs and devs[0]["hin"] == VALID_HIN


@pytest.mark.asyncio
async def test_order_creates_asset_with_vin(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cl = (await client.post("/api/clients", json={"name": "Vinny"}, headers=tenant)).json()
    o = await client.post("/api/orders", json={
        "location_id": loc, "client_id": cl["id"], "complaint": "oil change",
        "asset_type": "Car", "asset_brand": "Toyota", "asset_model": "Camry",
        "asset_vin": VALID_VIN}, headers=tenant)
    assert o.status_code in (200, 201), o.text
    detail = (await client.get(f"/api/orders/{o.json()['id']}", headers=tenant)).json()
    assert detail["device"], "asset should be auto-created"
