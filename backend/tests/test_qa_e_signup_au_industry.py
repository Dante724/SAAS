"""QA fix E5 — Australia at sign-up, and industry words map to a template.

Found in the 5-shop QA run (31 Oct 2026): an AU shop got no GST; industry_template
"computer_repair" / "electronics" silently became "general". Council: sign-up never fails on the
industry (unknown → general, logged), and AUD prints "$" like CAD.
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
async def test_au_signup_gets_gst_and_aud(client, body):
    r = await client.post("/api/auth/register", json=_reg(**body))
    assert r.status_code == 201, r.text
    co, taxes = await _company_and_taxes(client, r)
    assert co["currency_code"] == "AUD" and co["currency"] == "$"     # printed like CA/US; the code says AUD
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
async def test_unknown_industry_never_blocks_sign_up(client, monkeypatch):
    # Record the warning on the module's logger itself: global logging state (Alembic's fileConfig
    # in other tests, the app's own setup) varies by test order and made caplog unreliable on CI.
    from backend.routers import auth
    seen = []
    monkeypatch.setattr(auth._signup_log, "warning", lambda msg, *a: seen.append(msg % a))
    r = await client.post("/api/auth/register", json=_reg(industry_template="spaceships"))
    assert r.status_code == 201, r.text
    co, _ = await _company_and_taxes(client, r)
    assert co["industry_template"] == "general"
    assert any("spaceships" in m for m in seen), seen


def test_one_country_tax_table():
    from backend.routers import auth
    from backend.services import regions
    assert auth.COUNTRY_VAT is regions.COUNTRY_TAX
    assert regions.COUNTRY_TAX["AU"] == ("GST", 10.0, "included")
    assert regions.COUNTRY_TAX["AE"] == ("VAT", 5.0, "included")


@pytest.mark.asyncio
@pytest.mark.parametrize("country,code,tax", [
    ("AU", "AUD", ("GST", 10.0, "included")), ("US", "USD", ("Sales tax", 0.0, "imposed")),
    # QA wave 3: Canada's tax follows the province; with none given, Toronto time reads as Ontario (HST
    # 13%). Province by province: test_qa_w3e_jobs_roles.test_canadian_signup_picks_the_provinces_tax.
    ("GB", "GBP", ("VAT", 20.0, "included")), ("CA", "CAD", ("HST", 13.0, "imposed")),
    ("DE", "EUR", ("VAT", 19.0, "included")), ("NZ", "NZD", ("GST", 15.0, "included")),
])
async def test_country_alone_sets_currency_timezone_and_tax(client, country, code, tax):
    """Final gate (1 Nov 2026): a shop that picks only its country got INR and Asia/Kolkata.
    The country decides currency, timezone and the default tax, from the one country table."""
    r = await client.post("/api/auth/register", json=_reg(country=country))
    assert r.status_code == 201, r.text
    co, taxes = await _company_and_taxes(client, r)
    assert co["currency_code"] == code, co
    assert co["timezone"] != "Asia/Kolkata", co
    default = [t for t in taxes if t["is_default"]]
    assert default and (default[0]["name"], default[0]["rate"], default[0]["mode"]) == tax, taxes
