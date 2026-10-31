"""Kits print as one line, extras still shown (design "A + price lock").

Money stays on the kit's real lines; only what the customer sees, and how a fixed kit price is
kept, change. Covers the spread maths, the one fold (customer_lines), every customer surface,
the price lock, role permissions and the audit trail. The leak test is test_no_surface_leaks_*.
"""
import re
import types
import uuid

import pytest
from sqlalchemy import select

from backend.services import kit_print as kp
from backend.tests import kit_helpers as k

# ── pure: spread ───────────────────────────────────────────────────────────────────────────────


def _ln(qty, std):
    return types.SimpleNamespace(qty=qty, bundle_std_price=std, unit_price=std)


def _sum(res):
    return round(sum(round(u * m.qty, 2) + a for m, u, a in res), 2)


@pytest.mark.parametrize("target,members", [
    (100, [(1, 100), (1, 100), (1, 100)]),
    (149.99, [(1, 120), (1, 10), (1, 70)]),
    (200, [(2, 100), (2, 100), (2, 100)]),          # 2 kits of three equal parts
    (299.98, [(2, 120), (2, 10), (2, 70)]),         # 2 kits at 149.99
    (99.99, [(4, 10), (2, 35)]),
    (10, [(3, 1), (3, 1)]),                          # nothing divides: falls back to an adjustment
    (50, [(1, 0), (1, 0)]),                          # no standard prices: split by qty
    (0, [(1, 40), (1, 60)]),
])
def test_spread_hits_the_target_to_the_cent(target, members):
    res = kp.spread(target, [_ln(q, s) for q, s in members])
    assert _sum(res) == round(target, 2)
    assert all(u >= 0 for _m, u, _a in res)


def test_spread_puts_the_remainder_on_one_line_and_keeps_proportion():
    res = kp.spread(100, [_ln(1, 100), _ln(1, 100), _ln(1, 100)])
    assert sorted(u for _m, u, _a in res) == [33.33, 33.33, 33.34]
    res = kp.spread(150, [_ln(1, 120), _ln(1, 10), _ln(1, 70)])
    assert [u for _m, u, _a in res] == [90, 7.5, 52.5]


def test_spread_two_kits_is_exact_per_unit():
    res = kp.spread(200, [_ln(2, 100), _ln(2, 100), _ln(2, 100)])
    assert all(a == 0 for _m, _u, a in res)            # no stray cents: per-kit maths
    assert sorted(u for _m, u, _a in res) == [33.33, 33.33, 33.34]


# ── pure: customer_lines ────────────────────────────────────────────────────────────────────────

def _ol(desc, total, gid=None, mode=None, name=None, item=None, lt="part", qty=1, rate=0.0):
    return types.SimpleNamespace(description=desc, total=total, qty=qty, unit_price=total / qty,
                                 bundle_group_id=gid, bundle_print_mode=mode, bundle_name=name,
                                 bundle_qty=1, order_item_id=item, order_id="o1", line_type=lt,
                                 tax_rate=rate, tax_mode="imposed")


def test_fold_one_line_keeps_extras_and_position():
    ls = [_ol("Case", 5), _ol("Screen (Kit)", 90, "g", "one_line", "Kit"),
          _ol("Fit (Kit)", 60, "g", "one_line", "Kit", lt="labour"), _ol("Charger", 25)]
    out = kp.customer_lines(ls)
    assert [x.description for x in out] == ["Case", "Kit", "Charger"]
    assert out[1].total == 150 and out[1].includes == [] and out[1].ref_id is None
    assert out[0] is ls[0] and out[2] is ls[3]                       # extras untouched
    assert kp.customer_lines(ls, itemised=True) == ls                 # itemised copy


def test_fold_includes_names_only():
    ls = [_ol("Screen (Kit)", 90, "g", "one_line_includes", "Kit"),
          _ol("Fit (Kit)", 60, "g", "one_line_includes", "Kit", lt="labour")]
    (kit,) = kp.customer_lines(ls)
    assert kit.includes == ["Screen", "Fit"]
    assert kit.description == "Kit (includes Screen, Fit)"
    assert set(kp.row(kit)) == {"description", "qty", "unit_price", "total", "kit", "includes"}


def test_fold_itemized_and_untagged_lines_print_as_themselves():
    ls = [_ol("Screen (Kit)", 90, "g", "itemized", "Kit"), _ol("Old (Bundle)", 10, None, None)]
    assert kp.customer_lines(ls) == ls


def test_fold_is_per_job_item():
    ls = [_ol("Screen (Kit)", 90, "g", "one_line", "Kit", item="A"),
          _ol("Screen (Kit)", 90, "g", "one_line", "Kit", item="B")]
    out = kp.customer_lines(ls)
    assert len(out) == 2 and {o.order_item_id for o in out} == {"A", "B"}


def test_tax_summary_splits_by_rate():
    ls = [_ol("a", 110, rate=10), _ol("b", 120, rate=20), _ol("c", 50, rate=10)]
    got = kp.tax_by_rate(ls, lambda ln: {"base": ln.total, "tax": ln.total * ln.tax_rate / 100})
    assert [(r["rate"], r["base"]) for r in got] == [(10, 160), (20, 120)]


# ── API ────────────────────────────────────────────────────────────────────────────────────────

async def _shop(client, h, mode="one_line", price_mode="fixed", price=149.99):
    loc = await k.loc(client, h)
    screen = await k.part(client, h, loc, "iPhone 13 Screen", sell=120, cost=50, sku="SCR-13")
    glue = await k.part(client, h, loc, "Adhesive strip", sell=10, cost=1, sku="ADH-1")
    fit = await k.service(client, h, "Screen fitting", 70)
    charger = await k.part(client, h, loc, "USB-C charger", sell=25, cost=9, sku="CHG-1")
    b = await k.kit(client, h, [{"ref_type": "product", "ref_id": screen, "qty": 1},
                                {"ref_type": "product", "ref_id": glue, "qty": 1},
                                {"ref_type": "service", "ref_id": fit, "qty": 1}],
                    price_mode=price_mode, price=price, print_mode=mode)
    return types.SimpleNamespace(loc=loc, screen=screen, glue=glue, fit=fit, charger=charger, kit=b)


async def _job_with_kit_and_charger(client, h, s, qty=1):
    oid, cid = await k.job(client, h, s.loc)
    r = await k.add_kit(client, h, oid, s.kit["id"], qty=qty)
    await k.add_line(client, h, oid, kind="product", line_type="part", ref_id=s.charger,
                     description="USB-C charger", qty=1, unit_price=25, cost_price=9)
    return oid, cid, r["bundle_group_id"]


@pytest.mark.asyncio
async def test_kit_print_setting_saved_validated_and_audited(client, tenant, db):
    s = await _shop(client, tenant)
    assert s.kit["print_mode"] == "one_line"
    r = await client.put(f"/api/bundles/{s.kit['id']}", headers=tenant,
                         json={"name": s.kit["name"], "print_mode": "nonsense"})
    assert r.status_code == 400
    r = await client.put(f"/api/bundles/{s.kit['id']}", headers=tenant,
                         json={"name": s.kit["name"], "print_mode": "one_line_includes"})
    assert r.status_code == 200 and r.json()["print_mode"] == "one_line_includes"
    from backend.models import AuditLog
    acts = (await db.execute(select(AuditLog.action, AuditLog.diff).where(
        AuditLog.entity_id == s.kit["id"]))).all()
    assert ("bundle.print_mode", {"print_mode": ["one_line", "one_line_includes"]}) in [(a, d) for a, d in acts]
    # a kit made without saying how it prints keeps printing item by item
    plain = await k.kit(client, tenant, [{"ref_type": "service", "ref_id": s.fit, "qty": 1}], name="Plain")
    assert plain["print_mode"] == "itemized"


@pytest.mark.asyncio
async def test_package_line_plus_charger_everywhere(client, tenant, db):
    s = await _shop(client, tenant)
    oid, cid, _gid = await _job_with_kit_and_charger(client, tenant, s)
    assert (await k.order(client, tenant, oid))["total"] == 174.99
    inv = await k.invoice_lines(client, tenant, await k.invoice(client, tenant, oid, cid))
    # Line order isn't fixed (order lines have no sort column; Postgres returns them in any order).
    assert sorted((ln["description"], ln["total"]) for ln in inv) == [("Screen Replacement", 149.99), ("USB-C charger", 25)]
    html = await k.job_print(client, tenant, oid)
    assert "Screen Replacement" in html and "USB-C charger" in html and "174.99" in html
    t = await k.track(client, db, oid)
    assert sorted((x["label"], x["amount"]) for x in t["lines"]) == [("Screen Replacement", 149.99), ("USB-C charger", 25)]
    # stock dropped for the kit's parts and the extra; the staff view is still item by item
    assert await k.stock(client, tenant, s.screen) == 9 and await k.stock(client, tenant, s.charger) == 9
    ls = await k.lines(client, tenant, oid)
    assert len(ls) == 4
    scr = [ln for ln in ls if ln["ref_id"] == s.screen][0]
    assert scr["cost_price"] == 50                      # profit reports see the part's cost


@pytest.mark.asyncio
async def test_itemised_copy_lists_every_line(client, tenant):
    s = await _shop(client, tenant)
    oid, cid, _gid = await _job_with_kit_and_charger(client, tenant, s)
    r = await client.post("/api/documents/print-view", headers=tenant, json={
        "body_html": k.DOC_BODY, "doc_type": "work_order", "order_id": oid, "itemised": True})
    assert r.status_code == 200
    assert "iPhone 13 Screen" in r.json()["html"] and "SCR-13" in r.json()["html"]
    iid = await k.invoice(client, tenant, oid, cid)
    r = await client.get(f"/api/print/invoices/{iid}?itemised=true", headers=tenant)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/pdf")


@pytest.mark.asyncio
async def test_two_kits_correct_to_the_cent(client, tenant):
    s = await _shop(client, tenant)
    oid, cid, _gid = await _job_with_kit_and_charger(client, tenant, s, qty=2)
    assert (await k.order(client, tenant, oid))["total"] == round(149.99 * 2 + 25, 2)
    inv = await k.invoice_lines(client, tenant, await k.invoice(client, tenant, oid, cid))
    # Line order isn't fixed on Postgres, so find the kit line rather than reading inv[0].
    assert [ln for ln in inv if ln.get("kit")] == [
        {"description": "Screen Replacement", "qty": 2, "unit_price": 149.99, "total": 299.98, "kit": True}]
    assert await k.stock(client, tenant, s.screen) == 8


@pytest.mark.asyncio
async def test_partial_invoices_fold_only_billed_lines_and_never_double_bill(client, tenant):
    s = await _shop(client, tenant)
    oid, _cid, _gid = await _job_with_kit_and_charger(client, tenant, s)
    ls = await k.lines(client, tenant, oid)
    kit_ids = [ln["id"] for ln in ls if ln["bundle_group_id"]]
    first = kit_ids[:2]
    rest = [ln["id"] for ln in ls if ln["id"] not in first]
    totals = []
    for ids in (first, rest):
        r = await client.post(f"/api/cash/orders/{oid}/invoices", headers=tenant, json={"line_ids": ids})
        assert r.status_code == 201, r.text
        got = await k.invoice_lines(client, tenant, r.json()["invoice_id"])
        totals.append(round(sum(x["total"] for x in got), 2))
        assert [x["description"] for x in got if x.get("kit")] == ["Screen Replacement"]
    assert round(sum(totals), 2) == 174.99


@pytest.mark.asyncio
async def test_price_lock_qty_change_add_and_remove(client, tenant):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)

    async def kit_total():
        return round(sum(ln["total"] for ln in await k.lines(client, tenant, oid) if ln["bundle_group_id"] == gid), 2)

    glue = [ln for ln in await k.lines(client, tenant, oid) if ln["ref_id"] == s.glue][0]
    r = await client.patch(f"/api/orders/{oid}/lines/{glue['id']}", headers=tenant, json={"qty": 3})
    assert r.status_code == 200, r.text
    assert await kit_total() == 149.99 and await k.stock(client, tenant, s.glue) == 7
    # a line's own price can't be changed inside a fixed kit
    r = await client.patch(f"/api/orders/{oid}/lines/{glue['id']}", headers=tenant, json={"unit_price": 1})
    assert r.status_code == 409 and "kit" in r.text
    # add a part inside the kit: the kit price holds, stock drops
    extra_glue = await k.part(client, tenant, s.loc, "Extra adhesive", sell=5, cost=1)
    await k.add_line(client, tenant, oid, kind="product", line_type="part", ref_id=extra_glue,
                     description="Extra adhesive (Screen Replacement)", qty=1, unit_price=5,
                     bundle_group_id=gid)
    assert await kit_total() == 149.99 and await k.stock(client, tenant, extra_glue) == 9
    # remove the screen from the kit: its stock comes back, the kit price holds
    scr = [ln for ln in await k.lines(client, tenant, oid) if ln["ref_id"] == s.screen][0]
    assert (await client.delete(f"/api/orders/{oid}/lines/{scr['id']}", headers=tenant)).status_code == 200
    assert await kit_total() == 149.99 and await k.stock(client, tenant, s.screen) == 10
    assert (await k.order(client, tenant, oid))["total"] == 174.99
    # a bogus kit id is refused
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "kind": "custom", "line_type": "fee", "description": "x", "qty": 1, "unit_price": 1,
        "bundle_group_id": str(uuid.uuid4())})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_extra_same_part_never_merges_into_the_kit(client, tenant):
    s = await _shop(client, tenant)
    oid, cid, _gid = await _job_with_kit_and_charger(client, tenant, s)
    await k.add_line(client, tenant, oid, kind="product", line_type="part", ref_id=s.screen,
                     description="iPhone 13 Screen", qty=1, unit_price=120)
    ls = await k.lines(client, tenant, oid)
    assert len([ln for ln in ls if ln["ref_id"] == s.screen]) == 2
    inv = await k.invoice_lines(client, tenant, await k.invoice(client, tenant, oid, cid))
    assert ("iPhone 13 Screen", 120) in [(ln["description"], ln["total"]) for ln in inv]


@pytest.mark.asyncio
async def test_manager_price_override_needs_reason_and_is_audited(client, tenant, db):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    url = f"/api/orders/{oid}/kits/{gid}"
    assert (await client.patch(url, headers=tenant, json={"price": 120})).status_code == 400
    r = await client.patch(url, headers=tenant, json={"price": 120, "reason": "Loyal customer"})
    assert r.status_code == 200, r.text
    assert r.json()["order_total"] == 145 and r.json()["price"] == 120
    r = await client.patch(url, headers=tenant, json={"print_mode": "one_line_includes"})
    assert r.status_code == 200
    from backend.models import AuditLog
    rows = (await db.execute(select(AuditLog.action, AuditLog.diff).where(AuditLog.entity_id == oid))).all()
    acts = {a: d for a, d in rows}
    assert acts["order.kit_price_override"]["price"] == [149.99, 120]
    assert acts["order.kit_price_override"]["reason"] == "Loyal customer"
    assert acts["order.kit_print_mode"]["print_mode"] == ["one_line", "one_line_includes"]
    assert (await client.patch(f"/api/orders/{oid}/kits/nope", headers=tenant, json={"print_mode": "one_line"})).status_code == 404


@pytest.mark.asyncio
async def test_duplicate_keeps_kit_tags_with_a_new_group(client, tenant):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    r = await client.post(f"/api/orders/{oid}/duplicate", headers=tenant, json={})
    new = await k.lines(client, tenant, r.json()["id"])
    gids = {ln["bundle_group_id"] for ln in new if ln["bundle_group_id"]}
    assert len(gids) == 1 and gid not in gids
    assert {ln["bundle_print_mode"] for ln in new if ln["bundle_group_id"]} == {"one_line"}


@pytest.mark.asyncio
async def test_old_untagged_kit_lines_print_as_before(client, tenant, db):
    """A job from before this change: its "(Kit)" lines have no tags, so nothing folds."""
    s = await _shop(client, tenant)
    oid, cid, _gid = await _job_with_kit_and_charger(client, tenant, s)
    from backend.models import OrderLine
    for line in (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all():
        line.bundle_group_id = line.bundle_name = line.bundle_print_mode = None
        line.bundle_price_locked = line.bundle_qty = line.bundle_std_price = None
    await db.commit()
    inv = await k.invoice_lines(client, tenant, await k.invoice(client, tenant, oid, cid))
    assert len(inv) == 4 and not any(ln.get("kit") for ln in inv)


# ── roles ──────────────────────────────────────────────────────────────────────────────────────

async def _person(client, h, perms):
    r = await client.post("/api/settings/rbac/roles", headers=h, json={"name": f"R{uuid.uuid4().hex[:4]}", "permissions": perms})
    assert r.status_code == 201, r.text
    email = f"p-{uuid.uuid4().hex[:6]}@test.dev"
    r = await client.post("/api/staff/employees", headers=h, json={
        "name": "Pat", "email": email, "password": "pw-test-12345", "role_id": r.json()["id"]})
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


@pytest.mark.asyncio
async def test_roles_can_switch_each_kit_control_off(client, tenant):
    s = await _shop(client, tenant)
    oid, cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    tech = await _person(client, tenant, {"can_change_kit_print": False, "can_print_itemised_copy": False,
                                          "can_manage_bundles": False})
    url = f"/api/orders/{oid}/kits/{gid}"
    assert (await client.patch(url, headers=tech, json={"print_mode": "itemized"})).status_code == 403
    assert (await client.patch(url, headers=tech, json={"price": 1, "reason": "because"})).status_code == 403
    r = await client.post("/api/documents/print-view", headers=tech, json={
        "body_html": "x", "doc_type": "work_order", "order_id": oid, "itemised": True})
    assert r.status_code == 403
    r = await client.put(f"/api/bundles/{s.kit['id']}", headers=tech, json={"name": "x", "print_mode": "itemized"})
    assert r.status_code == 403
    # a new role gets the per-job print control but NOT the kit price (opt-in, money)
    mgr = await _person(client, tenant, {})
    assert (await client.patch(url, headers=mgr, json={"print_mode": "itemized"})).status_code == 200
    assert (await client.patch(url, headers=mgr, json={"price": 1, "reason": "because"})).status_code == 403
    boss = await _person(client, tenant, {"can_change_kit_price": True})
    assert (await client.patch(url, headers=boss, json={"price": 100, "reason": "goodwill"})).status_code == 200


# ── leak test ──────────────────────────────────────────────────────────────────────────────────

async def _customer_surfaces(client, h, db, oid, cid):
    """Every customer-facing rendering of the job, as text."""
    out = {}
    for mode in ("detailed", "simplified", "one_line"):
        iid = await k.invoice(client, h, oid, cid, mode)
        out[f"invoice_{mode}"] = str(await k.invoice_lines(client, h, iid))
        from backend.models import Invoice
        from backend.services.einvoice import build_ubl
        inv = (await db.execute(select(Invoice).where(Invoice.id == iid))).scalar_one()
        out[f"ubl_{mode}"] = await build_ubl(db, inv.org_id, inv)
        if mode == "detailed":
            tpl = (await client.post("/api/documents/templates", headers=h, json={
                "name": "Inv", "doc_type": "invoice", "body_html": "<table><tbody><tr><td>{Product Name}</td>"
                "<td>{Product Price}</td><td>{Product Amount}</td></tr></tbody></table>"})).json()
            r = await client.get(f"/api/documents/render-invoice/{iid}/{tpl['id']}", headers=h)
            out["invoice_template"] = r.json()["html"]
    out["job_print"] = await k.job_print(client, h, oid)
    out["track"] = str((await k.track(client, db, oid))["lines"])
    return out


def _money_strings(v):
    """Every way an amount can show up: 7.50, 7.5 (a JSON/str float), 1,234.50 and 7,50."""
    v = float(v)
    out = {f"{v:,.2f}", f"{v:.2f}", f"{v:.2f}".replace(".", ",")}
    if v != int(v):                  # "7.5"; a bare "1" would match every qty of 1
        out |= {f"{v:g}", repr(v)}
    return out


@pytest.mark.parametrize("mode", ["one_line", "one_line_includes"])
@pytest.mark.asyncio
async def test_no_surface_leaks_component_prices_costs_or_names(client, tenant, db, mode):
    s = await _shop(client, tenant, mode=mode)
    oid, cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    comps = [ln for ln in await k.lines(client, tenant, oid) if ln["bundle_group_id"] == gid]
    secrets = set()
    for ln in comps:
        secrets |= _money_strings(ln["unit_price"]) | _money_strings(ln["total"])
        secrets |= _money_strings(50) | _money_strings(1)          # component costs
    secrets -= _money_strings(25) | _money_strings(149.99) | _money_strings(174.99)
    names = ["iPhone 13 Screen", "Adhesive strip", "Screen fitting"]
    for where, text in (await _customer_surfaces(client, tenant, db, oid, cid)).items():
        for secret in ("SCR-13", "ADH-1", s.screen, s.glue, s.fit):
            assert secret not in text, f"{secret!r} leaked on {where}"
        for amt in secrets:
            assert not re.search(rf"(?<![\d.]){re.escape(amt)}(?![\d])", text), f"{amt} leaked on {where}"
        if "simplified" in where or where.endswith("one_line"):
            continue                       # those invoices are one line per job anyway
        assert "USB-C charger" in text, f"extra missing on {where}"
        for n in names:
            if mode == "one_line":
                assert n not in text, f"component name {n!r} leaked on {where}"
            elif where.startswith(("invoice_detailed", "job_print", "track", "invoice_template")):
                assert n in text, f"includes mode should name {n!r} on {where}"


@pytest.mark.asyncio
async def test_totals_and_tax_identical_in_every_mode(client, tenant, db):
    s = await _shop(client, tenant, mode="itemized")
    oid, cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    from backend.models import OrderLine
    rates = iter([10.0, 20.0, 10.0, 20.0])
    for line in (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().all():
        line.tax_rate = next(rates)
        line.tax_mode = "imposed"
    await db.commit()
    body = "<div>{Total Amount}|{Tax Value}|{Tax Summary}</div>"
    seen = set()
    for mode in ("itemized", "one_line", "one_line_includes"):
        assert (await client.patch(f"/api/orders/{oid}/kits/{gid}", headers=tenant, json={"print_mode": mode})).status_code == 200
        html = await k.job_print(client, tenant, oid, body=body)
        seen.add(re.sub(r"\s+", "", html))
        assert "10%" in html and "20%" in html
    assert len(seen) == 1


# ── migration: who gets the new controls ───────────────────────────────────────────────────────

def test_kit1p01_grants_on_existing_roles(tmp_path):
    import json
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    dbp = tmp_path / "kit.db"

    def up(target):
        env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=f"sqlite+aiosqlite:///{dbp}")
        env.pop("TEST_DATABASE_URL", None)
        r = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
                           cwd=repo, env=env, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]
    up("opd1")
    con = sqlite3.connect(dbp)
    rows = [("kitmgr", {"can_manage_bundles": True, "can_approve_discount": True}, 1),
            ("tech", {"can_create_tickets": True}, 1),
            ("legacy_no_kits", {"can_manage_bundles": False}, 0),
            ("legacy", {}, 0)]
    for rid, perms, explicit in rows:
        con.execute("INSERT INTO roles (id, org_id, name, permissions, perms_explicit, location_scope) "
                    "VALUES (?, 'o1', ?, ?, ?, 'all')", (rid, rid, json.dumps(perms), explicit))
    con.commit()
    con.close()
    up("kit1p01")
    con = sqlite3.connect(dbp)
    got = {rid: json.loads(p) for rid, p in con.execute("SELECT id, permissions FROM roles")}
    con.close()
    assert got["kitmgr"]["can_change_kit_print"] is True and got["kitmgr"]["can_change_kit_price"] is True
    assert got["tech"]["can_change_kit_print"] is False and got["tech"]["can_change_kit_price"] is False
    assert got["tech"]["can_print_itemised_copy"] is True
    assert got["legacy_no_kits"]["can_change_kit_print"] is False
    assert "can_change_kit_print" not in got["legacy"] and got["legacy"]["can_change_kit_price"] is False


# ── security review fixes ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_giveaways_inside_a_fixed_kit_without_the_kit_price_right(client, tenant, db):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    tech = await _person(client, tenant, {})              # no can_change_kit_price (opt-in)
    scr = [ln for ln in await k.lines(client, tenant, oid) if ln["ref_id"] == s.screen][0]
    r = await client.patch(f"/api/orders/{oid}/lines/{scr['id']}", headers=tech, json={"qty": 8})
    assert r.status_code == 403 and "extra" in r.text
    assert await k.stock(client, tenant, s.screen) == 9
    r = await client.post(f"/api/orders/{oid}/lines", headers=tech, json={
        "kind": "product", "line_type": "part", "ref_id": s.glue, "description": "glue",
        "qty": 1, "unit_price": 10, "bundle_group_id": gid})
    assert r.status_code == 403
    # fewer parts keeps the customer's price: allowed, and the re-share is audited
    glue = [ln for ln in await k.lines(client, tenant, oid) if ln["ref_id"] == s.glue][0]
    assert (await client.delete(f"/api/orders/{oid}/lines/{glue['id']}", headers=tech)).status_code == 200
    from backend.models import AuditLog
    acts = (await db.execute(select(AuditLog.action, AuditLog.diff).where(AuditLog.entity_id == oid))).all()
    resp = [d for a, d in acts if a == "order.kit_respread"]
    assert resp and resp[-1]["kit_price"] == 149.99 and resp[-1]["change"].startswith("Removed")


@pytest.mark.asyncio
async def test_only_catalog_items_join_a_kit_with_catalog_weights_and_kit_tax(client, tenant):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "kind": "custom", "line_type": "fee", "description": "Hidden fee", "qty": 1,
        "unit_price": 1000000, "bundle_group_id": gid})
    assert r.status_code == 400
    await k.add_line(client, tenant, oid, kind="product", line_type="part", ref_id=s.glue, description="glue",
                     qty=1, unit_price=1000000, tax_rate=50, bundle_group_id=gid)
    ls = [ln for ln in await k.lines(client, tenant, oid) if ln["bundle_group_id"] == gid]
    assert round(sum(ln["total"] for ln in ls), 2) == 149.99
    assert max(ln["unit_price"] for ln in ls) < 100           # weights came from the catalog
    assert {ln["tax_rate"] for ln in ls} == {0}


@pytest.mark.asyncio
async def test_hidden_charge_in_a_one_line_sum_kit_needs_the_print_right(client, tenant):
    s = await _shop(client, tenant, price_mode="sum", price=0)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    tech = await _person(client, tenant, {"can_change_kit_print": False})
    r = await client.post(f"/api/orders/{oid}/lines", headers=tech, json={
        "kind": "product", "line_type": "part", "ref_id": s.glue, "description": "glue", "qty": 1,
        "unit_price": 10, "bundle_group_id": gid})
    assert r.status_code == 403 and "one line" in r.text


@pytest.mark.asyncio
async def test_invoiced_kit_lines_are_frozen(client, tenant):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    ls = await k.lines(client, tenant, oid)
    scr = [ln for ln in ls if ln["ref_id"] == s.screen][0]
    glue = [ln for ln in ls if ln["ref_id"] == s.glue][0]
    r = await client.post(f"/api/cash/orders/{oid}/invoices", headers=tenant, json={"line_ids": [scr["id"]]})
    assert r.status_code == 201
    assert (await client.delete(f"/api/orders/{oid}/lines/{glue['id']}", headers=tenant)).status_code == 409
    assert (await client.patch(f"/api/orders/{oid}/kits/{gid}", headers=tenant,
                               json={"price": 100, "reason": "goodwill"})).status_code == 409
    assert (await client.patch(f"/api/orders/{oid}/kits/{gid}", headers=tenant,
                               json={"print_mode": "itemized"})).status_code == 200   # printing only


@pytest.mark.parametrize("price", ["NaN", "Infinity", 1e15, -1])
@pytest.mark.asyncio
async def test_kit_price_must_be_a_real_amount(client, tenant, price):
    s = await _shop(client, tenant)
    oid, _cid, gid = await _job_with_kit_and_charger(client, tenant, s)
    raw = price if isinstance(price, str) else repr(price)
    body = f'{{"price": {raw}, "reason": "test"}}'
    r = await client.patch(f"/api/orders/{oid}/kits/{gid}", headers={**tenant, "Content-Type": "application/json"},
                           content=body)
    assert r.status_code == 400, r.text
    r = await client.post("/api/bundles", headers={**tenant, "Content-Type": "application/json"}, content=(
        f'{{"name": "Bad", "price_mode": "fixed", "price": {raw}, '
        f'"components": [{{"ref_type": "service", "ref_id": "{s.fit}"}}]}}'))
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_kit_lines_stay_with_their_kit_item(client, tenant):
    s = await _shop(client, tenant)
    oid, _cid, _gid = await _job_with_kit_and_charger(client, tenant, s)
    scr = [ln for ln in await k.lines(client, tenant, oid) if ln["ref_id"] == s.screen][0]
    r = await client.patch(f"/api/orders/{oid}/lines/{scr['id']}", headers=tenant, json={"order_item_id": "x"})
    assert r.status_code in (404, 409)


# ── tester review fixes ────────────────────────────────────────────────────────────────────────

def test_spread_property_through_stored_totals():
    """Seeded random kits with fractional and large qtys: what gets STORED adds up to the kit
    price exactly, and no unit price is ever negative."""
    import random

    from backend.services.money import q2
    rnd = random.Random(144)
    for _ in range(5000):
        t = round(rnd.uniform(0, 500), 2)
        ms = [_ln(rnd.choice([0.25, 0.5, 1, 1.5, 2, 2.5, 3, 20]), round(rnd.uniform(0, 200), 2))
              for _ in range(rnd.randint(1, 5))]
        res = kp.spread(t, ms)
        assert round(sum(q2(q2(m.qty * u) + a) for m, u, a in res), 2) == t, (t, [(m.qty, u, a) for m, u, a in res])
        assert all(u >= 0 for _m, u, _a in res)


async def _custom_kit(client, h, parts, price):
    loc = await k.loc(client, h)
    comps = []
    for name, std, qty, kind in parts:
        rid = (await k.part(client, h, loc, name, sell=std, cost=0, qty=100)) if kind == "product" \
            else await k.service(client, h, name, std)
        comps.append({"ref_type": kind, "ref_id": rid, "qty": qty})
    b = await k.kit(client, h, comps, name="QA Kit", price_mode="fixed", price=price, print_mode="one_line")
    oid, cid = await k.job(client, h, loc)
    gid = (await k.add_kit(client, h, oid, b["id"]))["bundle_group_id"]
    return oid, cid, gid


async def _kit_total(client, h, oid, gid):
    return round(sum(ln["total"] for ln in await k.lines(client, h, oid) if ln["bundle_group_id"] == gid), 2)


@pytest.mark.asyncio
async def test_fractional_labour_and_cheap_part_kits_are_exact(client, tenant):
    oid, _c, gid = await _custom_kit(client, tenant, [("Screen", 120, 1, "product"), ("Labour hour", 25, 1.5, "service")], 49.99)
    assert await _kit_total(client, tenant, oid, gid) == 49.99
    oid, _c, gid = await _custom_kit(client, tenant, [("Filter", 10, 20, "product"), ("Label", 0.05, 1, "product")], 99.99)
    assert await _kit_total(client, tenant, oid, gid) == 99.99
    assert all(ln["unit_price"] >= 0 for ln in await k.lines(client, tenant, oid))


@pytest.mark.asyncio
async def test_last_cent_survives_other_edits(client, tenant):
    oid, _c, gid = await _custom_kit(client, tenant, [("Screw", 1, 3, "product"), ("Washer", 1, 3, "product")], 10)
    for ln in await k.lines(client, tenant, oid):
        r = await client.patch(f"/api/orders/{oid}/lines/{ln['id']}", headers=tenant, json={"cost_price": 0.02})
        assert r.status_code == 200, r.text
    assert await _kit_total(client, tenant, oid, gid) == 10


@pytest.mark.asyncio
async def test_issued_invoice_keeps_how_the_kit_prints(client, tenant, db):
    oid, cid, gid = await _custom_kit(client, tenant, [("Screen", 120, 1, "product"), ("Fit", 30, 1, "service")], 149.99)
    iid = await k.invoice(client, tenant, oid, cid)
    from backend.models import Invoice
    inv = (await db.execute(select(Invoice).where(Invoice.id == iid))).scalar_one()
    inv.status = "sent"
    await db.commit()
    before = await k.invoice_lines(client, tenant, iid)
    r = await client.patch(f"/api/orders/{oid}/kits/{gid}", headers=tenant, json={"print_mode": "itemized"})
    assert r.status_code == 409 and "itemised copy" in r.text
    assert await k.invoice_lines(client, tenant, iid) == before


@pytest.mark.asyncio
async def test_whole_job_invoice_freezes_the_kit_price(client, tenant):
    oid, cid, gid = await _custom_kit(client, tenant, [("Screen", 120, 1, "product"), ("Fit", 30, 1, "service")], 149.99)
    await k.invoice(client, tenant, oid, cid)
    r = await client.patch(f"/api/orders/{oid}/kits/{gid}", headers=tenant, json={"price": 99, "reason": "goodwill"})
    assert r.status_code == 409
    ln = (await k.lines(client, tenant, oid))[0]
    assert (await client.delete(f"/api/orders/{oid}/lines/{ln['id']}", headers=tenant)).status_code == 409
