"""Batch 1 finish: Public pages (1a/1b) + Finance defaults (2b). 2a (tax depth)
backend is already covered in test_numbering_entities_tax.py."""
import pytest


# ── 1a: appointment background pattern + subdomain change ────────────────────────
@pytest.mark.asyncio
async def test_public_pages_1a_pattern_and_subdomain(client, tenant):
    r = await client.put("/api/staff/public-pages",
                         json={"page_background_pattern": "gears", "page_color": "#112233"},
                         headers=tenant)
    assert r.status_code == 200
    cfg = (await client.get("/api/staff/public-pages", headers=tenant)).json()
    assert cfg["page_background_pattern"] == "gears" and cfg["page_color"] == "#112233"

    ok = await client.put("/api/staff/public-pages/subdomain",
                         json={"subdomain": "power-tools-1"}, headers=tenant)
    assert ok.status_code == 200 and ok.json()["subdomain"] == "power-tools-1"
    assert (await client.get("/api/staff/public-pages", headers=tenant)).json()["slug"] == "power-tools-1"

    bad = await client.put("/api/staff/public-pages/subdomain",
                          json={"subdomain": "no"}, headers=tenant)      # too short
    assert bad.status_code == 422
    bad2 = await client.put("/api/staff/public-pages/subdomain",
                           json={"subdomain": "Bad Space"}, headers=tenant)
    assert bad2.status_code == 422


# ── 1b: order/estimate header + zero-price toggles ───────────────────────────
@pytest.mark.asyncio
async def test_public_pages_1b_order_estimate_toggles(client, tenant):
    r = await client.put("/api/staff/public-pages", json={
        "oe_show_logo": False, "oe_logo_size": "small", "oe_show_email": False,
        "oe_separate_tables": True, "oe_show_zero_price_products": False}, headers=tenant)
    assert r.status_code == 200
    cfg = (await client.get("/api/staff/public-pages", headers=tenant)).json()
    assert cfg["oe_show_logo"] is False and cfg["oe_logo_size"] == "small"
    assert cfg["oe_show_email"] is False and cfg["oe_separate_tables"] is True
    assert cfg["oe_show_zero_price_products"] is False
    assert cfg["oe_show_name"] is True   # untouched default preserved


# ── 2b: default payment categories per doc type ──────────────────────────────
@pytest.mark.asyncio
async def test_cash_categories_defaults_2b(client, tenant, db):
    from backend.models import CashFlowItem
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    cat = CashFlowItem(org_id=me["org_id"], name="Repair income", direction="income")
    db.add(cat)
    await db.commit()
    await db.refresh(cat)

    listing = (await client.get("/api/staff/cash-categories", headers=tenant)).json()
    assert any(c["id"] == cat.id for c in listing["items"])

    r = await client.put("/api/staff/cash-categories/defaults",
                         json={"default_tickets_id": cat.id}, headers=tenant)
    assert r.status_code == 200
    got = (await client.get("/api/staff/cash-categories", headers=tenant)).json()
    assert got["default_tickets_id"] == cat.id
