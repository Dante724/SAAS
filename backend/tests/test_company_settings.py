"""Build 121 — document language + multi-currency company settings."""
import pytest


@pytest.mark.asyncio
async def test_document_language_saved(client, tenant):
    r = await client.put("/api/staff/company", json={
        "name": "Co", "document_language": "hi"}, headers=tenant)
    assert r.status_code == 200, r.text
    c = (await client.get("/api/staff/company", headers=tenant)).json()
    assert c["document_language"] == "hi"


@pytest.mark.asyncio
async def test_multi_currency_saved_and_returned(client, tenant):
    curs = [{"code": "USD", "symbol": "$", "rate": 0.012},
            {"code": "EUR", "symbol": "€", "rate": 0.011}]
    r = await client.put("/api/staff/company", json={
        "name": "Co", "currencies": curs}, headers=tenant)
    assert r.status_code == 200, r.text
    c = (await client.get("/api/staff/company", headers=tenant)).json()
    codes = {x["code"] for x in c["currencies"]}
    assert codes == {"USD", "EUR"}


@pytest.mark.asyncio
async def test_defaults_present(client, tenant):
    c = (await client.get("/api/staff/company", headers=tenant)).json()
    assert c["document_language"] == "en"     # default
    assert c["currencies"] == []              # none by default


@pytest.mark.asyncio
async def test_document_translation_helper():
    """Build 122: printed-document labels translate to the org's document language."""
    from backend.services.printing import _t
    assert _t("Invoice", "es") == "Factura"
    assert _t("Total", "hi") == "कुल"
    assert _t("Invoice", "en") == "Invoice"       # base passes through
    assert _t("Nonstandard label", "es") == "Nonstandard label"  # unknown passes through
