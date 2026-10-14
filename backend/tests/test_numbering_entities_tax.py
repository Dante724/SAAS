"""Phase 1 (numbering + legal entities) & Phase 2 (tax depth)."""
import pytest


# ── Phase 1A: numbering engine ───────────────────────────────────────────────
def test_render_tokens():
    import datetime

    from backend.services.numbering import render
    now = datetime.datetime(2026, 3, 7)
    assert render("INV-{Y}-{C:4}", 1, now=now) == "INV-2026-0001"
    assert render("{Y}{M}{D}-{C:3}", 42, now=now) == "20260307-042"
    assert render("WO-{C:5}", 12345, now=now) == "WO-12345"
    assert render("X-{C}", 9, now=now) == "X-9"          # unpadded counter


@pytest.mark.asyncio
async def test_numbering_default_matches_legacy_format(client, tenant, db):
    """With no config, next_number reproduces the historical INV-00001 format."""
    from backend.services.numbering import next_number
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    n1 = await next_number(db, me["org_id"], "invoice")
    n2 = await next_number(db, me["org_id"], "invoice")
    await db.commit()
    assert n1 == "INV-00001" and n2 == "INV-00002"


@pytest.mark.asyncio
async def test_numbering_setting_and_preview(client, tenant, db):
    from backend.services.numbering import next_number
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    prev = await client.post("/api/settings/numbering/preview",
                             json={"doc_type": "order", "template": "WO-{Y}-{C:4}", "begins_from": 5},
                             headers=tenant)
    assert prev.json()["example"] == "WO-2026-0005"

    r = await client.put("/api/settings/numbering",
                         json={"doc_type": "order", "template": "WO-{Y}-{C:4}", "begins_from": 5},
                         headers=tenant)
    assert r.status_code == 200
    n = await next_number(db, me["org_id"], "order")
    await db.commit()
    assert n == "WO-2026-0005"

    bad = await client.put("/api/settings/numbering",
                          json={"doc_type": "order", "template": "WO-noCounter"}, headers=tenant)
    assert bad.status_code == 422   # must contain {C}


@pytest.mark.asyncio
async def test_numbering_scope_isolation(client, tenant, db):
    """entity scope overrides org scope for the same doc_type."""
    from backend.services.numbering import next_number
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    await client.put("/api/settings/numbering",
                     json={"doc_type": "invoice", "template": "ORG-{C:3}", "begins_from": 1,
                           "scope_key": "org"}, headers=tenant)
    await client.put("/api/settings/numbering",
                     json={"doc_type": "invoice", "template": "ENT-{C:3}", "begins_from": 1,
                           "scope_key": "entity:E1"}, headers=tenant)
    org_n = await next_number(db, me["org_id"], "invoice")
    ent_n = await next_number(db, me["org_id"], "invoice", entity_id="E1")
    await db.commit()
    assert org_n == "ORG-001" and ent_n == "ENT-001"


# ── Phase 1B: legal entities ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_legal_entity_crud_single_default(client, tenant):
    a = await client.post("/api/settings/legal-entities",
                          json={"name": "ProEngine", "is_default": True}, headers=tenant)
    b = await client.post("/api/settings/legal-entities",
                          json={"name": "ToolCare", "is_default": True}, headers=tenant)
    assert a.status_code == 201 and b.status_code == 201
    items = (await client.get("/api/settings/legal-entities", headers=tenant)).json()["items"]
    defaults = [e for e in items if e["is_default"]]
    assert len(defaults) == 1 and defaults[0]["name"] == "ToolCare"   # last default wins

    d = await client.delete(f"/api/settings/legal-entities/{a.json()['id']}", headers=tenant)
    assert d.status_code == 200
    names = [e["name"] for e in (await client.get("/api/settings/legal-entities", headers=tenant)).json()["items"]]
    assert "ProEngine" not in names   # soft-deleted


# ── Phase 2: tax depth ───────────────────────────────────────────────────────
async def _tax(client, tenant, name, rate, mode="imposed", **extra):
    body = {"name": name, "rate": rate, "mode": mode}
    body.update(extra)
    r = await client.post("/api/catalog/taxes", json=body, headers=tenant)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_tax_extended_fields_roundtrip(client, tenant):
    t = await _tax(client, tenant, "VAT", 13.5, code="VAT13",
                   name_for_document="VAT 13.5%", location_scope="all")
    assert t["code"] == "VAT13" and t["name_for_document"] == "VAT 13.5%"


@pytest.mark.asyncio
async def test_tax_resolution_stack_and_inclusive_exclusive(client, tenant, db):
    from backend.services.tax import resolve_taxes
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    excl = await _tax(client, tenant, "GST", 10.0, mode="imposed")
    incl = await _tax(client, tenant, "ServiceTax", 20.0, mode="included")

    # ordered stack for tickets_sales: excl first, incl second
    r = await client.put("/api/settings/tax-rules",
                         json={"doc_family": "tickets_sales", "tax_ids": [excl["id"], incl["id"]]},
                         headers=tenant)
    assert r.json()["count"] == 2

    res = await resolve_taxes(db, me["org_id"], doc_family="tickets_sales", net_amount=100.0)
    amounts = {a["name"]: a["amount"] for a in res["taxes"]}
    assert amounts["GST"] == pytest.approx(10.0)                 # exclusive: +10
    assert amounts["ServiceTax"] == pytest.approx(16.67, abs=0.01)  # inclusive: 100-100/1.2
    assert res["tax_total"] == pytest.approx(26.67, abs=0.01)


@pytest.mark.asyncio
async def test_tax_exception_precedence(client, tenant, db):
    from backend.services.tax import resolve_taxes
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    base = await _tax(client, tenant, "Base", 10.0)
    await client.put("/api/settings/tax-rules",
                     json={"doc_family": "tickets_sales", "tax_ids": [base["id"]]}, headers=tenant)

    # category exemption for category "cat1"
    await client.post("/api/settings/tax-exceptions",
                      json={"doc_family": "tickets_sales", "target_type": "category",
                            "target_id": "cat1", "exempt": True}, headers=tenant)
    exempt = await resolve_taxes(db, me["org_id"], doc_family="tickets_sales",
                                 net_amount=100.0, category_id="cat1")
    assert exempt["exempt"] and exempt["tax_total"] == 0.0

    # a product with no exception still gets the default stack
    normal = await resolve_taxes(db, me["org_id"], doc_family="tickets_sales",
                                 net_amount=100.0, item_type="product", item_id="p9")
    assert normal["tax_total"] == pytest.approx(10.0)
