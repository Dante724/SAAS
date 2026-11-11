"""Launch recheck 2: an AI-drafted estimate is priced from the shop's own services and products
only (never a model-invented number), and a coupon's minimum spend is checked against what is
left after the sale discount."""
import json
import uuid

import pytest

from backend.services import ai


async def _service(client, H, name, price):
    r = await client.post("/api/catalog/services", json={"name": name, "price": price}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _product(client, H, name, sell, repair=0):
    r = await client.post("/api/inventory/parts", json={
        "name": name, "sku": f"P-{uuid.uuid4().hex[:6]}", "sell_price": sell, "repair_price": repair,
        "cost_price": 5}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _model_says(monkeypatch, lines, seen=None):
    """A live model that answers with `lines` (and records the system prompt it was given)."""
    async def fake(system, user, max_tokens=700):
        if seen is not None:
            seen.append(system)
        return json.dumps(lines)
    monkeypatch.setattr(ai, "is_live", lambda: True)
    monkeypatch.setattr(ai, "_complete", fake)


@pytest.mark.asyncio
async def test_live_ai_draft_takes_prices_from_the_catalog_not_the_model(client, tenant, monkeypatch):
    sid = await _service(client, tenant, "Screen replacement", 89)
    pid = await _product(client, tenant, "iPhone 12 display", sell=120, repair=110)
    seen: list[str] = []
    # The model tries to price every line itself, and adds one the shop doesn't sell.
    _model_says(monkeypatch, [
        {"description": "Screen replacement", "qty": 1, "catalog": "S1", "unit_price": 35},
        {"description": "Display part", "qty": 1, "catalog": "P1", "unit_price": 60},
        {"description": "Ultrasonic board clean", "qty": 1, "catalog": None, "unit_price": 120},
    ], seen)
    r = await client.post("/api/ai/estimate/draft", json={"complaint": "cracked screen", "device": "iPhone 12"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["provider"] == "anthropic"
    by = {line["description"]: line for line in d["lines"]}
    assert by["Screen replacement"]["unit_price"] == 89 and by["Screen replacement"]["ref_id"] == sid
    assert by["Screen replacement"]["kind"] == "service"
    # A product on a work order uses its repair price.
    assert by["iPhone 12 display"]["unit_price"] == 110 and by["iPhone 12 display"]["ref_id"] == pid
    assert by["iPhone 12 display"]["kind"] == "product"
    # Not in the shop's catalog: no price at all, flagged, and the note says to set one.
    assert by["Ultrasonic board clean"]["unit_price"] == 0
    assert by["Ultrasonic board clean"]["needs_price"] is True and by["Ultrasonic board clean"]["ref_id"] is None
    assert "Set a price" in d["note"]
    # The model is shown the catalog by name and code, never a price to copy.
    assert "S1: Screen replacement" in seen[0] and "89" not in seen[0] and "110" not in seen[0]


@pytest.mark.asyncio
async def test_live_ai_draft_with_an_unknown_catalog_code_stays_unpriced(client, tenant, monkeypatch):
    _model_says(monkeypatch, [{"description": "Battery swap", "qty": 1, "catalog": "S99", "unit_price": 49}])
    d = (await client.post("/api/ai/estimate/draft", json={"complaint": "battery dies"}, headers=tenant)).json()
    assert [line["unit_price"] for line in d["lines"]] == [0]
    assert d["lines"][0]["needs_price"] is True and "Set a price" in d["note"]


@pytest.mark.asyncio
async def test_ai_draft_never_offers_another_shops_catalog(client, tenant, monkeypatch):
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Other", "email": f"o-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await _service(client, other, "Screen replacement", 500)
    _model_says(monkeypatch, [{"description": "Screen replacement", "qty": 1, "catalog": "S1"}])
    d = (await client.post("/api/ai/estimate/draft", json={"complaint": "cracked screen"}, headers=tenant)).json()
    assert d["lines"][0]["unit_price"] == 0 and d["lines"][0]["needs_price"] is True


@pytest.mark.asyncio
async def test_stub_draft_prices_a_line_only_when_the_catalog_has_it(client, tenant, monkeypatch):
    monkeypatch.setattr(ai, "is_live", lambda: False)
    await _service(client, tenant, "Diagnostic / inspection", 25)
    d = (await client.post("/api/ai/estimate/draft", json={"complaint": "cracked screen"}, headers=tenant)).json()
    by = {line["description"]: line for line in d["lines"]}
    assert by["Diagnostic / inspection"]["unit_price"] == 25
    assert by["Display assembly replacement"]["unit_price"] == 0
    assert by["Display assembly replacement"]["needs_price"] is True
    assert "Set a price" in d["note"]


# ── a coupon's minimum spend is what's left after the sale discount ──────────

def _svc(price):
    return {"kind": "service", "ref_id": None, "description": "Diagnostic", "qty": 1, "unit_price": price}


@pytest.mark.asyncio
async def test_coupon_minimum_is_checked_after_the_sale_discount(client, tenant):
    r = await client.post("/api/coupons", json={"code": "MIN50", "kind": "fixed", "value": 5, "min_total": 50},
                          headers=tenant)
    assert r.status_code in (200, 201), r.text
    # 60 in the cart, 20 off: only 40 is being spent, under the coupon's 50 minimum.
    q = (await client.post("/api/sales/quote", json={"discount": 20, "coupon_code": "MIN50",
                                                     "lines": [_svc(60)]}, headers=tenant)).json()
    assert q["coupon"] == 0 and "coupon" in q["problems"]
    assert q["total"] == pytest.approx(40)
    r = await client.post("/api/sales", json={"discount": 20, "coupon_code": "MIN50", "lines": [_svc(60)]},
                          headers=tenant)
    assert r.status_code == 400 and "minimum" in r.json()["detail"]
    # With a smaller discount the spend clears the minimum and the coupon applies.
    q = (await client.post("/api/sales/quote", json={"discount": 10, "coupon_code": "MIN50",
                                                     "lines": [_svc(60)]}, headers=tenant)).json()
    assert q["coupon"] == 5 and q["total"] == pytest.approx(45)
