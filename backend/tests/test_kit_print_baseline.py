"""Characterisation of how a kit (bundle) behaves on a job — written BEFORE "kits print as one
line" so the change can prove it leaves money, stock and old jobs alone.

Everything here must stay true after the change for a kit whose print setting is the default
("Item by item"). The one deliberate change is pinned in test_fixed_price_kit_hits_price_exactly.
"""
import pytest

from backend.tests import kit_helpers as k


async def _shop(client, h):
    loc = await k.loc(client, h)
    screen = await k.part(client, h, loc, "iPhone 13 Screen", sell=120, cost=50, sku="SCR-13")
    glue = await k.part(client, h, loc, "Adhesive strip", sell=10, cost=1, sku="ADH-1")
    fit = await k.service(client, h, "Screen fitting", 70)
    return loc, screen, glue, fit


def _comps(screen, glue, fit):
    return [{"ref_type": "product", "ref_id": screen, "qty": 1},
            {"ref_type": "product", "ref_id": glue, "qty": 1},
            {"ref_type": "service", "ref_id": fit, "qty": 1}]


@pytest.mark.asyncio
async def test_sum_kit_explodes_into_named_component_lines(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit))
    oid, _ = await k.job(client, tenant, loc)
    r = await k.add_kit(client, tenant, oid, b["id"])
    assert r["lines_created"] == 3 and r["bundle"] == "Screen Replacement"
    ls = await k.lines(client, tenant, oid)
    assert sorted(ln["description"] for ln in ls) == [
        "Adhesive strip (Screen Replacement)", "Screen fitting (Screen Replacement)",
        "iPhone 13 Screen (Screen Replacement)"]
    by = {ln["description"].split(" (")[0]: ln for ln in ls}
    assert by["iPhone 13 Screen"]["unit_price"] == 120 and by["iPhone 13 Screen"]["kind"] == "product"
    assert by["iPhone 13 Screen"]["line_type"] == "part"
    assert by["Screen fitting"]["line_type"] == "labour" and by["Screen fitting"]["kind"] == "service"
    assert (await k.order(client, tenant, oid))["total"] == 200


@pytest.mark.asyncio
async def test_fixed_kit_scales_components_to_the_price(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit), price_mode="fixed", price=150)
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"])
    ls = await k.lines(client, tenant, oid)
    # 150 / 200 = 0.75 on every component
    assert sorted(ln["unit_price"] for ln in ls) == [7.5, 52.5, 90]
    assert (await k.order(client, tenant, oid))["total"] == 150


@pytest.mark.asyncio
async def test_fixed_price_kit_hits_price_exactly(client, tenant):
    """Three equal components at 100 for a kit priced 100: each is 33.33…, so rounding each line
    on its own landed on 99.99. The remainder now goes on the last component."""
    loc = await k.loc(client, tenant)
    a = await k.service(client, tenant, "A", 100)
    b_ = await k.service(client, tenant, "B", 100)
    c = await k.service(client, tenant, "C", 100)
    b = await k.kit(client, tenant, [{"ref_type": "service", "ref_id": x, "qty": 1} for x in (a, b_, c)],
                    price_mode="fixed", price=100)
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"])
    assert (await k.order(client, tenant, oid))["total"] == 100
    assert round(sum(ln["total"] for ln in await k.lines(client, tenant, oid)), 2) == 100


@pytest.mark.asyncio
async def test_kit_deducts_stock_on_jobs_not_estimates(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit))
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"])
    assert await k.stock(client, tenant, screen) == 9 and await k.stock(client, tenant, glue) == 9
    eid, _ = await k.job(client, tenant, loc, estimate=True)
    await k.add_kit(client, tenant, eid, b["id"])
    assert await k.stock(client, tenant, screen) == 9


@pytest.mark.asyncio
async def test_kit_qty_two_doubles_components_and_stock(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit), price_mode="fixed", price=150)
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"], qty=2)
    ls = await k.lines(client, tenant, oid)
    assert all(ln["qty"] == 2 for ln in ls)
    assert await k.stock(client, tenant, screen) == 8
    assert (await k.order(client, tenant, oid))["total"] == 300


@pytest.mark.asyncio
async def test_removing_a_kit_part_returns_its_stock(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit))
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"])
    scr = [ln for ln in await k.lines(client, tenant, oid) if ln["ref_id"] == screen][0]
    r = await client.delete(f"/api/orders/{oid}/lines/{scr['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert await k.stock(client, tenant, screen) == 10


@pytest.mark.asyncio
async def test_kit_lines_carry_the_technician_for_commission(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    b = await k.kit(client, tenant, _comps(screen, glue, fit))
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"], technician_id=me["id"])
    assert all(ln["technician_id"] == me["id"] for ln in await k.lines(client, tenant, oid))


@pytest.mark.asyncio
async def test_invoice_display_modes_for_an_exploded_kit(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit))
    oid, cid = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"])
    det = await k.invoice_lines(client, tenant, await k.invoice(client, tenant, oid, cid, "detailed"))
    assert sorted(ln["description"] for ln in det) == [
        "Adhesive strip (Screen Replacement)", "Screen fitting (Screen Replacement)",
        "iPhone 13 Screen (Screen Replacement)"]
    assert round(sum(ln["total"] for ln in det), 2) == 200
    simp = await k.invoice_lines(client, tenant, await k.invoice(client, tenant, oid, cid, "simplified"))
    assert len(simp) == 1 and simp[0]["total"] == 200 and "cracked screen" in simp[0]["description"]
    one = await k.invoice_lines(client, tenant, await k.invoice(client, tenant, oid, cid, "one_line"))
    assert len(one) == 1 and one[0]["total"] == 200


@pytest.mark.asyncio
async def test_partial_invoice_lists_only_billed_kit_lines(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit))
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"])
    ls = await k.lines(client, tenant, oid)
    scr = [ln for ln in ls if ln["ref_id"] == screen][0]
    r = await client.post(f"/api/cash/orders/{oid}/invoices", headers=tenant, json={"line_ids": [scr["id"]]})
    assert r.status_code == 201, r.text
    got = await k.invoice_lines(client, tenant, r.json()["invoice_id"])
    assert [ln["description"] for ln in got] == ["iPhone 13 Screen (Screen Replacement)"]
    assert got[0]["total"] == 120


@pytest.mark.asyncio
async def test_job_printout_and_track_page_list_every_component(client, tenant, db):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit))
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"])
    html = await k.job_print(client, tenant, oid)
    for name in ("iPhone 13 Screen", "Adhesive strip", "Screen fitting", "SCR-13"):
        assert name in html
    assert "200.00" in html
    t = await k.track(client, db, oid)
    assert sorted(x["label"] for x in t["lines"]) == [
        "Adhesive strip (Screen Replacement)", "Screen fitting (Screen Replacement)",
        "iPhone 13 Screen (Screen Replacement)"]


@pytest.mark.asyncio
async def test_sold_products_report_lists_each_kit_part(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit))
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"])
    ls = await k.lines(client, tenant, oid)
    assert {ln["ref_id"] for ln in ls if ln["kind"] == "product"} == {screen, glue}


@pytest.mark.asyncio
async def test_duplicating_a_kit_job_copies_its_lines(client, tenant):
    loc, screen, glue, fit = await _shop(client, tenant)
    b = await k.kit(client, tenant, _comps(screen, glue, fit), price_mode="fixed", price=150)
    oid, _ = await k.job(client, tenant, loc)
    await k.add_kit(client, tenant, oid, b["id"])
    r = await client.post(f"/api/orders/{oid}/duplicate", headers=tenant, json={})
    assert r.status_code in (200, 201), r.text
    new = await k.lines(client, tenant, r.json()["id"])
    assert sorted(ln["total"] for ln in new) == sorted(ln["total"] for ln in await k.lines(client, tenant, oid))
