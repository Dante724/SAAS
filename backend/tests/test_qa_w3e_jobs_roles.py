"""QA wave 3, section E (5-shop re-run on Postgres, 1 Nov 2026): jobs, roles, auth, receipts.

High: a delivered, paid job could be edited by the Specialist (technician) template, which then
owed money again. The template no longer has "Edit the closed ticket"; untouched existing
Specialist roles lose it through migration ce144w3; a closed job refuses edits without it.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.services import permissions as P
from backend.tests import kit_helpers as K

PART = {"line_type": "part", "kind": "product", "description": "Part", "unit_price": 20}


def _reg(**kw):
    return {"org_name": "Shop", "name": "Owner", "email": f"o-{uuid.uuid4().hex[:8]}@test.dev",
            "password": "pw-test-12345", "accept_terms": True, **kw}


async def _owner(client, **kw):
    r = await client.post("/api/auth/register", json=_reg(**kw))
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _person(client, H, role_id, name="Pat", password="pw-test-12345"):
    email = f"{name.lower()}-{uuid.uuid4().hex[:6]}@test.dev"
    r = await client.post("/api/staff/employees", json={"name": name, "email": email, "password": password,
                                                         "role_id": role_id}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": password})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, email, r.json()["id"]


async def _template_role(client, H, template, name=None):
    r = await client.post("/api/settings/rbac/roles", json={"name": name or template, "template": template}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _till(client, h):
    r = await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _delivered_paid_job(client, h):
    loc = await K.loc(client, h)
    oid, cid = await K.job(client, h, loc)
    await K.add_line(client, h, oid, **{**PART, "unit_price": 100})
    till = await _till(client, h)
    total = (await K.order(client, h, oid))["total"]
    r = await client.post(f"/api/cash/orders/{oid}/pay", json={"method": "cash", "account_id": till,
                                                               "amount": total}, headers=h)
    assert r.status_code in (200, 201), r.text
    for st in ("done", "delivered"):
        r = await client.patch(f"/api/orders/{oid}/status", json={"status": st}, headers=h)
        assert r.status_code == 200, r.text
    return oid, total


# ── High: a closed job is locked for the technician template ───────────────────────────────────
def test_specialist_template_cant_edit_closed_jobs():
    assert P.new_role_permissions("Specialist")["can_edit_closed_ticket"] is False
    assert P.new_role_permissions("Manager")["can_edit_closed_ticket"] is True
    assert P.new_role_permissions("Full access")["can_edit_closed_ticket"] is True


@pytest.mark.asyncio
async def test_specialist_cant_add_a_line_or_reopen_a_delivered_paid_job(client):
    H = await _owner(client)
    oid, total = await _delivered_paid_job(client, H)
    T, _, _ = await _person(client, H, await _template_role(client, H, "Specialist"))
    r = await client.post(f"/api/orders/{oid}/lines", json={**PART, "unit_price": 10}, headers=T)
    assert r.status_code == 403, r.text
    line = (await K.lines(client, H, oid))[0]
    r = await client.patch(f"/api/orders/{oid}/lines/{line['id']}", json={"qty": 2}, headers=T)
    assert r.status_code == 403, r.text
    r = await client.delete(f"/api/orders/{oid}/lines/{line['id']}", headers=T)
    assert r.status_code == 403, r.text
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "in_repair"}, headers=T)
    assert r.status_code == 403, r.text
    o = await K.order(client, H, oid)
    assert o["total"] == total and o["status"] == "delivered"
    # the owner (and anyone granted the permission) still can
    r = await client.post(f"/api/orders/{oid}/lines", json={**PART, "unit_price": 10}, headers=H)
    assert r.status_code == 201, r.text


def _load_migration():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ce144w3", "backend/migrations/versions/ce144w3_specialist_closed_jobs.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    return mig


def test_migration_takes_closed_edit_off_untouched_specialist_roles_only(tmp_path, caplog):
    import json as _json
    import logging

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    mig = _load_migration()
    old_tech = {**P.new_role_permissions("Specialist"), "can_edit_closed_ticket": True}   # the template until now
    custom = {**old_tech, "can_view_reports": True}                 # the owner changed this one
    partner = {**P.new_role_permissions("Partner"), "can_edit_closed_ticket": True}
    manager = P.new_role_permissions("Manager")
    admin = P.full_access_permissions()
    eng = sa.create_engine(f"sqlite:///{tmp_path}/r.db")

    def run(fn):
        with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            fn()

    def perms():
        with eng.begin() as conn:
            return {i: _json.loads(p) for i, p in conn.execute(sa.text("SELECT id, permissions FROM roles"))}

    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE roles (id TEXT PRIMARY KEY, org_id TEXT, name TEXT, "
                             "permissions JSON, perms_explicit BOOLEAN)"))
        conn.execute(sa.text("CREATE TABLE role_permission_backups (role_id TEXT, migration VARCHAR(40), "
                             "org_id TEXT, permissions JSON, created_at DATETIME, PRIMARY KEY (role_id, migration))"))
        for rid, name, p in (("tech", "Bench tech", old_tech), ("custom", "Senior tech", custom),
                             ("partner", "Partner", partner), ("manager", "Desk", manager), ("admin", "admin", admin)):
            conn.execute(sa.text("INSERT INTO roles VALUES (:i, 'o1', :n, :p, 1)"), {"i": rid, "n": name, "p": _json.dumps(p)})
    before = perms()
    with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
        run(mig.upgrade)
    after = perms()
    assert after["tech"] == {**old_tech, "can_edit_closed_ticket": False}
    assert {k: after[k] for k in ("custom", "partner", "manager", "admin")} == \
        {k: before[k] for k in ("custom", "partner", "manager", "admin")}
    assert [r.getMessage() for r in caplog.records if "ce144w3" in r.getMessage()] == [
        "ce144w3: Specialist role can no longer edit closed jobs (org o1, role 'Bench tech')"]
    run(mig.upgrade)                                                     # idempotent
    assert perms() == after
    run(mig.downgrade)                                                   # restores exactly
    assert perms() == before


def test_migration_matches_what_the_current_template_saves():
    mig = _load_migration()
    assert mig.is_untouched_specialist({**P.new_role_permissions("Specialist"), "can_edit_closed_ticket": True})
    assert not mig.is_untouched_specialist(P.new_role_permissions("Specialist"))      # already off
    assert not mig.is_untouched_specialist({**P.new_role_permissions("Partner"), "can_edit_closed_ticket": True})


# ── Medium: job lines ───────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["unknown_product", "client_as_product", "unknown_service", "service_as_product"])
async def test_job_line_refuses_an_id_that_isnt_this_shops_catalog_item(client, tenant, which):
    loc = await K.loc(client, tenant)
    oid, cid = await K.job(client, tenant, loc)
    sid = await K.service(client, tenant, "Labour", 40)
    ref, kind = {"unknown_product": (str(uuid.uuid4()), "product"), "client_as_product": (cid, "product"),
                 "unknown_service": (str(uuid.uuid4()), "service"), "service_as_product": (sid, "product")}[which]
    body = {**PART, "kind": kind, "line_type": "part" if kind == "product" else "labour", "ref_id": ref}
    r = await client.post(f"/api/orders/{oid}/lines", json=body, headers=tenant)
    assert r.status_code in (404, 422), r.text
    assert await K.lines(client, tenant, oid) == []


@pytest.mark.asyncio
async def test_new_job_refuses_an_unknown_product_id(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    r = await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "location_id": await K.loc(client, tenant), "complaint": "x",
        "lines": [{**PART, "ref_id": str(uuid.uuid4())}]})
    assert r.status_code in (404, 422), r.text


@pytest.mark.asyncio
async def test_add_line_merges_only_identical_lines(client, tenant):
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "ALPS stick", 20, 5, qty=10)
    oid, _ = await K.job(client, tenant, loc)
    base = {**PART, "ref_id": pid, "description": "ALPS stick L"}
    await K.add_line(client, tenant, oid, **base)
    again = await K.add_line(client, tenant, oid, **base)               # a double-click: same line
    assert again.get("merged") is True
    await K.add_line(client, tenant, oid, **{**base, "description": "ALPS stick R"})
    await K.add_line(client, tenant, oid, **{**base, "unit_price": 25})
    lines = sorted(((ln["description"], ln["unit_price"], ln["qty"]) for ln in await K.lines(client, tenant, oid)))
    assert lines == [("ALPS stick L", 20, 2), ("ALPS stick L", 25, 1), ("ALPS stick R", 20, 1)]
    assert await K.stock(client, tenant, pid) == 6                      # every unit taken once


# ── Medium: roles, device limit, sign-up tax, receipts ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_staff_roles_endpoint_applies_the_template(client):
    H = await _owner(client)
    r = await client.post("/api/staff/roles", json={"name": "Tech", "template": "Specialist"}, headers=H)
    assert r.status_code == 201, r.text
    role = next(x for x in (await client.get("/api/settings/rbac/roles", headers=H)).json()["items"]
                if x["id"] == r.json()["id"]) if isinstance((await client.get("/api/settings/rbac/roles", headers=H)).json(), dict) \
        else next(x for x in (await client.get("/api/settings/rbac/roles", headers=H)).json() if x["id"] == r.json()["id"])
    assert role["permissions"] == P.new_role_permissions("Specialist")
    bad = await client.post("/api/staff/roles", json={"name": "X", "template": "Nope"}, headers=H)
    assert bad.status_code == 422, bad.text


@pytest.mark.asyncio
async def test_device_limit_token_works_once(client):
    H = await _owner(client)
    assert (await client.put("/api/staff/company", json={"max_sessions_per_login": 2}, headers=H)).status_code == 200
    rid = await _template_role(client, H, "Manager", "Desk")
    _, email, _ = await _person(client, H, rid)                          # device 1

    async def login():
        return await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    assert (await login()).status_code == 200                           # device 2
    hit = await login()                                                 # device 3: at the limit
    assert hit.status_code == 409, hit.text
    detail = hit.json()["detail"]
    tok, sid = detail["limit_token"], detail["sessions"][0]["id"]
    ok = await client.post("/api/auth/session-limit/continue", json={"limit_token": tok, "session_id": sid})
    assert ok.status_code == 200, ok.text
    sid2 = next(s["id"] for s in (await client.get("/api/auth/sessions", headers={
        "Authorization": f"Bearer {ok.json()['access_token']}"})).json()["items"] if not s["this_device"])
    again = await client.post("/api/auth/session-limit/continue", json={"limit_token": tok, "session_id": sid2})
    assert again.status_code == 401, again.text                         # replayed: refused, nobody signed out


async def _taxes(client, H):
    t = (await client.get("/api/catalog/taxes", headers=H)).json()
    return t.get("items", t) if isinstance(t, dict) else t


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,name,rate", [
    ({"province": "ON"}, "HST", 13.0), ({"province": "NS"}, "HST", 14.0), ({"province": "NB"}, "HST", 15.0),
    ({"province": "BC"}, "GST", 5.0), ({"province": "qc"}, "GST", 5.0),
    ({}, "HST", 13.0),                                                   # no province: the Toronto default
    ({"timezone": "America/Vancouver"}, "GST", 5.0), ({"timezone": "America/Halifax"}, "HST", 14.0),
])
async def test_canadian_signup_picks_the_provinces_tax(client, extra, name, rate):
    H = await _owner(client, country="CA", **extra)
    default = [t for t in await _taxes(client, H) if t["is_default"]]
    assert [(t["name"], t["rate"], t["mode"]) for t in default] == [(name, rate, "imposed")]


@pytest.mark.asyncio
async def test_signup_refuses_an_unknown_province(client):
    r = await client.post("/api/auth/register", json=_reg(country="CA", province="ZZ"))
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_signup_options_list_canadian_provinces(client):
    o = (await client.get("/api/auth/signup-options")).json()
    on = next(p for p in o["provinces"]["CA"] if p["code"] == "ON")
    assert on["tax_name"] == "HST" and on["tax_rate"] == 13.0


@pytest.mark.asyncio
@pytest.mark.parametrize("country,label,tax_name,rate", [("AU", "ABN", "GST", 10), ("DE", "VAT no.", "VAT", 19),
                                                          ("GB", "VAT no.", "VAT", 20)])
async def test_till_receipt_shows_the_included_tax_and_the_shops_tax_id(client, country, label, tax_name, rate):
    H = await _owner(client, country=country)
    r = await client.post("/api/settings/legal-entities", json={"name": "Shop Pty", "vat_no": "51 824 753 556",
                                                                "is_default": True}, headers=H)
    assert r.status_code == 201, r.text
    sale = await client.post("/api/sales", json={"lines": [
        {"kind": "service", "description": "Battery fit", "qty": 1, "unit_price": 110}]}, headers=H)
    assert sale.status_code == 201, sale.text
    rc = (await client.get(f"/api/sales/{sale.json()['id']}/receipt", headers=H)).json()
    inc = round(110 - 110 / (1 + rate / 100), 2)
    assert rc["tax_lines"] == [{"name": tax_name, "rate": rate, "amount": inc, "included": True}]
    assert rc["shop_tax_id"] == {"label": label, "value": "51 824 753 556"}
    assert f"Includes {tax_name} {rate}%" in rc["text"] and "51 824 753 556" in rc["text"] and label in rc["text"]
    assert rc["total"] == 110


@pytest.mark.asyncio
async def test_receipt_without_a_tax_id_prints_none(client):
    H = await _owner(client, country="AU")
    sale = await client.post("/api/sales", json={"lines": [
        {"kind": "service", "description": "Fit", "qty": 1, "unit_price": 11}]}, headers=H)
    rc = (await client.get(f"/api/sales/{sale.json()['id']}/receipt", headers=H)).json()
    assert rc["shop_tax_id"] is None and "ABN" not in rc["text"]


# ── Low ─────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_technician_reads_only_what_jobs_need(client):
    H = await _owner(client)
    T, _, me = await _person(client, H, await _template_role(client, H, "Specialist"), name="Tech")
    await client.post("/api/settings/email-accounts", json={"address": "desk@shop.dev", "display_name": "Desk",
                                                            "allowed_employee_ids": ["someone-else"]}, headers=H)
    team = (await client.get("/api/staff/employees", headers=T)).json()
    owner = next(e for e in team if e["id"] != me)
    assert owner["name"] and owner["email"] is None
    assert owner["hired_at"] is None and owner["weekly_hours"] is None
    co = (await client.get("/api/staff/company", headers=T)).json()
    assert co["name"] and co["currency_code"]
    assert co["account_id"] is None and co["max_sessions_per_login"] is None and co["company_note"] is None
    accts = (await client.get("/api/settings/email-accounts", headers=T)).json()["items"]
    assert accts == []                                                  # none they can send from
    assert (await client.get("/api/staff/company", headers=H)).json()["account_id"]


@pytest.mark.asyncio
async def test_sense_settings_show_staff_only_what_turns_features_on(client):
    H = await _owner(client)
    T, _, _ = await _person(client, H, await _template_role(client, H, "Manager", "Desk"))
    s = (await client.get("/api/sense/settings", headers=T)).json()
    assert set(s) == {"enabled", "features"} and "watchdog" not in s["features"]
    assert {"brief", "watchdog", "agents", "learning"} <= set((await client.get("/api/sense/settings", headers=H)).json())


@pytest.mark.asyncio
async def test_reservation_max_days_cant_be_negative(client, tenant):
    r = await client.put("/api/staff/inventory-settings", json={"reservation_max_days": -5}, headers=tenant)
    assert r.status_code == 422, r.text
    assert (await client.put("/api/staff/inventory-settings", json={"reservation_max_days": 0},
                             headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_recommendation_decide_counts_each_choice_once(client, tenant, db):
    from backend.models import Order
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    ids = []
    for t in ("Case", "Glass", "Battery"):
        r = await client.post(f"/api/orders/{oid}/recommendations", headers=tenant,
                              json={"title": t, "kind": "custom", "unit_price": 10})
        ids.append(r.json()["id"])
        await client.post(f"/api/orders/{oid}/recommendations/{r.json()['id']}/publish", headers=tenant)
    token = (await K.track(client, db, oid))
    r = await client.post(f"/api/public/track/{token}/recommendations/decide",
                          json={"approve_ids": [ids[0]], "later_ids": [ids[1]], "decline_ids": [ids[2]]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert (b["approved"], b["saved_for_later"], b["declined"]) == (1, 1, 1)


@pytest.mark.asyncio
async def test_scan_finds_a_sold_imei_and_says_it_was_sold(client, tenant):
    from backend.tests.test_qa_f2_stock import _phone
    p, _, sns = await _phone(client, tenant)
    r = await client.post("/api/sales", json={"lines": [{"kind": "product", "ref_id": p["id"], "description": "iPhone",
                                                         "qty": 1, "unit_price": 400, "serials": [sns[0]]}]}, headers=tenant)
    assert r.status_code == 201, r.text
    hit = (await client.post("/api/scan/resolve", json={"code": sns[0]}, headers=tenant)).json()
    assert hit["matched"] and hit["id"] == p["id"] and hit["serial_status"] == "sold"


@pytest.mark.asyncio
async def test_stock_receipt_warns_on_a_bad_imei_check_digit(client, tenant):
    from backend.tests.test_inventory_bins_passport_stocktake import _product
    loc = await K.loc(client, tenant)
    p = await _product(client, tenant, name="Pixel", serialized=True)
    bad, good = "490154203237519", "490154203237518"                    # last digit off / correct
    r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", headers=tenant, json={
        "location_id": loc, "qty": 1, "movement_type": "receive", "serials": [bad]})
    assert r.status_code == 200, r.text
    assert [w["serial"] for w in r.json()["warnings"]] == [bad]
    r = await client.post("/api/inventory/postings", headers=tenant, json={
        "location_id": loc, "lines": [{"part_id": p["id"], "qty": 1, "purchase_price": 100, "serials": [good]}]})
    assert r.status_code in (200, 201), r.text
    assert r.json()["warnings"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value,active", [("0", False), ("1", True)])
async def test_bulk_set_active_takes_the_value_as_text(client, tenant, value, active):
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Cable", 5, 1, qty=0)
    if active:
        await client.post("/api/inventory/parts/bulk", json={"part_ids": [pid], "action": "set_active", "number": 0},
                          headers=tenant)
    r = await client.post("/api/inventory/parts/bulk", json={"part_ids": [pid], "action": "set_active",
                                                             "value": value}, headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["active"] is active


@pytest.mark.asyncio
async def test_dropped_job_with_nothing_done_isnt_a_write_off(client, tenant):
    loc = await K.loc(client, tenant)
    pid = await K.part(client, tenant, loc, "Screen", 150, 50, qty=3)
    oid, _ = await K.job(client, tenant, loc)
    await K.add_line(client, tenant, oid, **{**PART, "ref_id": pid, "unit_price": 150})
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "cancelled", "reason": "Changed mind"},
                           headers=tenant)
    assert r.status_code == 200, r.text
    o = await K.order(client, tenant, oid)
    assert o["nothing_done"] is True and o["written_off"] == 0
    assert o["balance_due"] == 0 if "balance_due" in o else True
    assert await K.stock(client, tenant, pid) == 3


@pytest.mark.asyncio
async def test_dropped_job_with_work_done_writes_off_what_was_owed(client, tenant):
    oid, _ = await K.job(client, tenant, await K.loc(client, tenant))
    await K.add_line(client, tenant, oid, line_type="labour", kind="custom", description="Diagnosis", unit_price=40)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "cancelled", "reason": "No parts"},
                           headers=tenant)
    assert r.status_code == 200, r.text
    o = await K.order(client, tenant, oid)
    assert o["nothing_done"] is False and o["written_off"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["commission_labour", "commission_parts"])
async def test_employee_commission_percent_isnt_taken_when_it_wouldnt_be_paid(client, tenant, field):
    body = {"name": "Jake", "email": f"j-{uuid.uuid4().hex[:6]}@t.dev", "password": "pw-test-12345", field: 10}
    r = await client.post("/api/staff/employees", json=body, headers=tenant)
    assert r.status_code == 422, r.text
    assert "commission" in r.text.lower()
    ok = await client.post("/api/staff/employees", json={**body, field: 0}, headers=tenant)
    assert ok.status_code == 201, ok.text
    r = await client.put(f"/api/staff/employees/{ok.json()['id']}", json={field: 5}, headers=tenant)
    assert r.status_code == 422, r.text
