"""QA fix E5 — Australia at sign-up, and an unknown industry is refused instead of becoming "general".

Found in the 5-shop QA run (31 Oct 2026): an AU shop got no GST and "$" instead of "A$";
industry_template "computer_repair" / "electronics" silently became "general".
"""
import uuid

import pytest


def _reg(**kw):
    return {"org_name": "Tempus", "name": "Owner", "email": f"o-{uuid.uuid4().hex[:8]}@test.dev",
            "password": "pw-test-12345", "accept_terms": True, **kw}


async def _company_and_taxes(client, r):
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    co = (await client.get("/api/staff/company", headers=h)).json()
    taxes = (await client.get("/api/catalog/taxes", headers=h)).json()
    taxes = taxes.get("items", taxes) if isinstance(taxes, dict) else taxes
    return co, taxes


@pytest.mark.asyncio
async def test_signup_options_offer_australia(client):
    o = (await client.get("/api/auth/signup-options", headers={"CF-IPCountry": "AU"})).json()
    assert {"key": "au", "label": "Australia"} in o["regions"]
    assert o["region_hint"] == "au"
    assert o["region_defaults"]["au"]["currency_code"] == "AUD"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"region": "au", "currency_code": "AUD", "currency": "AUD"},           # what the sign-up form sends
    {"region": "other", "country": "AU", "currency_code": "AUD", "currency": "A$"},  # the QA script
])
async def test_au_signup_gets_gst_and_a_dollar(client, body):
    r = await client.post("/api/auth/register", json=_reg(**body))
    assert r.status_code == 201, r.text
    co, taxes = await _company_and_taxes(client, r)
    assert co["currency_code"] == "AUD" and co["currency"] == "A$"
    gst = [t for t in taxes if t["name"] == "GST"]
    assert gst and gst[0]["rate"] == 10 and gst[0]["mode"] == "included" and gst[0]["is_default"]


@pytest.mark.asyncio
@pytest.mark.parametrize("given,stored", [("computer_repair", "computer"), ("electronics", "general"),
                                          ("Phone Repair", "mobile_phone"), ("bike", "bike")])
async def test_known_industry_words_map(client, given, stored):
    r = await client.post("/api/auth/register", json=_reg(industry_template=given))
    assert r.status_code == 201, r.text
    co, _ = await _company_and_taxes(client, r)
    assert co["industry_template"] == stored


@pytest.mark.asyncio
async def test_unknown_industry_is_refused(client):
    r = await client.post("/api/auth/register", json=_reg(industry_template="spaceships"))
    assert r.status_code == 422, r.text
    assert "general" in r.json()["detail"]
