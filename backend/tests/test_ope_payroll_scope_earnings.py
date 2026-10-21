"""Team E — My earnings (E1), payroll filters + drill-down (E2), payroll scope per role (E3).

- /payroll/me shows only the caller's pay, commissions broken down rule → order → line item,
  and needs can_view_own_payroll (on for every built-in role).
- The calculation and the payroll report narrow by location, role and employee; exports keep it.
- A role's payroll scope (own / all / selected) is enforced on every payroll endpoint.
"""
import csv
import io
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from backend.models import CommissionRule, EmployeeBalanceLedger, Role

MONTH = date.today().strftime("%Y-%m")
PAST = "2026-06"


# ── helpers ───────────────────────────────────────────────────────────────────
async def _register(client):
    email = f"ope-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, me["org_id"], me["id"]


async def _role(client, H, name, template="Manager", **extra):
    r = await client.post("/api/settings/rbac/roles", json={"name": name, "template": template, **extra}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _person(client, H, name, role_id=None, wage=0, location_id=None):
    email = f"p-{uuid.uuid4().hex[:8]}@t.dev"
    body = {"name": name, "email": email, "password": "pw-test-12345",
            "wage_type": "fixed", "wage_value": wage}
    if role_id:
        body["role_id"] = role_id
    if location_id:
        body["location_id"] = location_id
    r = await client.post("/api/staff/employees", json=body, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    assert tok.status_code == 200, tok.text
    return r.json()["id"], {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _order(client, H, **extra):
    body = {"location_id": await _loc(client, H), "complaint": "x", **extra}
    r = await client.post("/api/orders", json=body, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _labour(client, H, oid, price, tech, desc):
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "custom", "description": desc, "qty": 1,
        "unit_price": price, "technician_id": tech}, headers=H)
    assert r.status_code in (200, 201), r.text


async def _rule(db, org_id, **kw):
    r = CommissionRule(org_id=org_id, **kw)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return r


async def _closed(client, H, key):
    r = await client.post("/api/statuses", json={
        "name": key, "key": key, "status_group": "closed", "is_terminal": True}, headers=H)
    assert r.status_code in (200, 201), r.text
    return key


async def _bonus(client, H, emp, amount, month=MONTH, kind="bonus"):
    r = await client.post("/api/payroll/adjustment", json={
        "employee_id": emp, "kind": kind, "amount": amount, "month": month}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── E1: My earnings ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_my_earnings_breaks_commissions_down_by_rule_order_and_line(client, db):
    H, org, _ = await _register(client)
    role = await _role(client, H, "Tech", template="Specialist")
    tech, T = await _person(client, H, "Tina", role["id"], wage=1000)
    other, _ = await _person(client, H, "Omar", role["id"])
    labour = await _rule(db, org, name="Labour 10%", event="labor_performed",
                         calc_type="percent", percent_basis="total", amount=10)
    closing = await _rule(db, org, name="Close bonus", event="order_closed", calc_type="flat", amount=25)
    done = await _closed(client, H, "done-e1")

    o = await _order(client, H, assigned_to_id=tech)
    await _labour(client, H, o["id"], 100, tech, "Screen swap")
    await _labour(client, H, o["id"], 50, tech, "Battery")
    o2 = await _order(client, H, assigned_to_id=other)          # someone else's job
    await _labour(client, H, o2["id"], 500, other, "Board repair")
    for oid in (o["id"], o2["id"]):
        assert (await client.patch(f"/api/orders/{oid}/status", json={"status": done}, headers=H)).status_code == 200
    await _bonus(client, H, tech, 30)
    await _bonus(client, H, tech, 10, kind="penalty")

    r = await client.get("/api/payroll/me", headers=T)
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["employee_id"] == tech
    assert me["commissions_total"] == pytest.approx(40)          # 10 + 5 + 25, nothing of Omar's
    assert me["base_salary"] == pytest.approx(1000)
    assert me["bonuses_total"] == 30 and me["penalties_total"] == 10
    assert me["total"] == pytest.approx(1000 + 40 + 30 - 10)
    assert me["accrual_status"] == "not_accrued"

    by_rule = {g["rule_id"]: g for g in me["commissions"]}
    assert set(by_rule) == {labour.id, closing.id}
    lab = by_rule[labour.id]
    assert lab["rule_name"] == "Labour 10%" and lab["total"] == pytest.approx(15)
    assert [d["number"] for d in lab["documents"]] == [o["order_number"]]
    items = {i["name"]: i for i in lab["documents"][0]["items"]}
    assert items["Screen swap"]["total"] == pytest.approx(10)
    assert items["Battery"]["total"] == pytest.approx(5)
    assert all(i["line_id"] for i in items.values())
    clo = by_rule[closing.id]
    assert clo["documents"][0]["items"][0]["name"] == "Whole order"
    assert clo["total"] == pytest.approx(25)

    # Reopening claws it back; the reversal sits on the same line item, netting to zero.
    assert (await client.patch(f"/api/orders/{o['id']}/status", json={"status": "new"}, headers=H)).status_code == 200
    me = (await client.get("/api/payroll/me", headers=T)).json()
    assert me["commissions_total"] == pytest.approx(0)
    lab = next(g for g in me["commissions"] if g["rule_id"] == labour.id)
    screen = next(i for i in lab["documents"][0]["items"] if i["name"] == "Screen swap")
    assert [e["kind"] for e in screen["entries"]] == ["earned", "reversed"]
    assert screen["total"] == pytest.approx(0)


@pytest.mark.asyncio
async def test_my_earnings_shows_accrual_status_and_period(client, db):
    H, org, _ = await _register(client)
    tech, T = await _person(client, H, "Paz", (await _role(client, H, "T2", template="Specialist"))["id"], wage=900)
    await _bonus(client, H, tech, 100, month=PAST)
    acc = await client.post("/api/payroll/calculation/accrue", json={"employee_id": tech, "month": PAST}, headers=H)
    assert acc.status_code == 201, acc.text
    r = (await client.get(f"/api/payroll/me?from={PAST}-01&to={PAST}-30", headers=T)).json()
    assert r["accrual_status"] == "accrued"
    assert r["accruals"][0]["gross_total"] == pytest.approx(1000)
    assert r["bonuses"][0]["amount"] == 100
    # Half the month earns half the salary.
    half = (await client.get(f"/api/payroll/me?from={PAST}-01&to={PAST}-15", headers=T)).json()
    assert half["base_salary"] == pytest.approx(450)

    assert (await client.get("/api/payroll/me?from=2026-06-10&to=2026-06-01", headers=T)).status_code == 400
    assert (await client.get("/api/payroll/me?from=2024-01-01&to=2026-06-01", headers=T)).status_code == 400
    assert (await client.get("/api/payroll/me?from=nope", headers=T)).status_code in (400, 422)


@pytest.mark.asyncio
async def test_my_earnings_permission_and_pay_history_limit(client, db):
    H, org, _ = await _register(client)
    # every built-in template grants it
    for tpl in ("Full access", "Manager", "Partner", "Specialist"):
        role = await _role(client, H, f"R {tpl}", template=tpl)
        assert role["permissions"]["can_view_own_payroll"] is True
    no = await _role(client, H, "No pay view", template="Specialist", permissions={"can_view_own_payroll": False})
    _, N = await _person(client, H, "Nia", no["id"])
    r = await client.get("/api/payroll/me", headers=N)
    assert r.status_code == 403 and "can_view_own_payroll" in r.text

    lim = await _role(client, H, "Short history", template="Specialist", payroll_visibility_days=10)
    _, L = await _person(client, H, "Lu", lim["id"])
    r = (await client.get("/api/payroll/me?from=2026-01-01&to=2026-01-31", headers=L)).json()
    assert r["visible_from"] and r["total"] == 0 and r["commissions"] == []


def test_migration_grants_own_payroll_to_existing_explicit_roles(tmp_path):
    """ope1 adds can_view_own_payroll to roles that existed before it (explicit roles are
    allow-lists, so they would otherwise start without it) and keeps payroll scope at "all"."""
    import json
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    path = tmp_path / "mig.db"
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=f"sqlite+aiosqlite:///{path}")
    env.pop("TEST_DATABASE_URL", None)

    def alembic(target):
        r = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
                           cwd=repo, env=env, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-2000:]

    alembic("ls144r4")
    con = sqlite3.connect(path)
    con.execute("INSERT INTO roles (id, org_id, name, permissions, perms_explicit, location_scope, contact_scope, "
                "order_scope, warehouse_scope) VALUES ('r1', 'o1', 'tech', ?, 1, 'all', 'all', 'all', 'all')",
                (json.dumps({"can_create_tickets": True}),))
    con.commit()
    con.close()
    alembic("ope1")
    con = sqlite3.connect(path)
    perms, scope = con.execute("SELECT permissions, payroll_scope FROM roles WHERE id = 'r1'").fetchone()
    con.close()
    assert json.loads(perms) == {"can_create_tickets": True, "can_view_own_payroll": True}
    assert scope == "all"


# ── E2: filters and drill-down ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_calculation_and_report_filters_and_export_keep_them(client, db):
    H, org, _ = await _register(client)
    loc2 = (await client.post("/api/staff/locations", json={"name": "Branch 2"}, headers=H)).json()["id"]
    techs = await _role(client, H, "Techs", template="Specialist")
    desk = await _role(client, H, "Desk", template="Specialist")
    a, _ = await _person(client, H, "Ann", techs["id"], wage=100)
    b, _ = await _person(client, H, "Ben", techs["id"], wage=200, location_id=loc2)
    c, _ = await _person(client, H, "Cid", desk["id"], wage=300, location_id=loc2)

    async def names(qs):
        r = await client.get(f"/api/payroll/calculation?month={MONTH}&{qs}", headers=H)
        assert r.status_code == 200, r.text
        return sorted(i["name"] for i in r.json()["items"])

    assert {"Ann", "Ben", "Cid"} <= set(await names(""))
    assert await names(f"location_id={loc2}") == ["Ben", "Cid"]
    assert await names(f"role_id={techs['id']}") == ["Ann", "Ben"]
    assert await names(f"location_id={loc2}&role_id={techs['id']}") == ["Ben"]
    assert await names(f"employee_id={c}") == ["Cid"]

    rng = f"date_from={MONTH}-01&date_to={MONTH}-28"
    rep = (await client.get(f"/api/reports/run/payroll?{rng}&role_id={desk['id']}", headers=H)).json()
    assert [row[0] for row in rep["rows"]] == ["Cid"]
    rep = (await client.get(f"/api/reports/run/payroll?{rng}&location_id={loc2}", headers=H)).json()
    assert sorted(row[0] for row in rep["rows"]) == ["Ben", "Cid"]
    csv_r = await client.get(f"/api/reports/export-csv/payroll?{rng}&employee_id={a}", headers=H)
    assert csv_r.status_code == 200
    rows = list(csv.reader(io.StringIO(csv_r.text)))
    assert [r[0] for r in rows[1:]] == ["Ann"]
    xlsx = await client.get(f"/api/reports/export/payroll?{rng}&role_id={techs['id']}", headers=H)
    assert xlsx.status_code == 200
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(xlsx.content)).active
    assert sorted(r[0] for r in ws.iter_rows(min_row=5, values_only=True)) == ["Ann", "Ben"]


@pytest.mark.asyncio
async def test_detail_drill_down_returns_rule_document_and_item(client, db):
    H, org, owner = await _register(client)
    rule = await _rule(db, org, name="Labour 20%", event="labor_performed",
                       calc_type="percent", percent_basis="total", amount=20)
    done = await _closed(client, H, "done-e2")
    o = await _order(client, H, assigned_to_id=owner)
    await _labour(client, H, o["id"], 200, owner, "Hinge")
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": done}, headers=H)
    d = (await client.get(f"/api/payroll/calculation/detail?employee_id={owner}&month={MONTH}", headers=H)).json()
    g = d["breakdown"][0]
    assert g["rule_id"] == rule.id and g["rule_name"] == "Labour 20%" and g["calc_type"] == "percent"
    doc = g["documents"][0]
    assert doc["doc_type"] == "order" and doc["number"] == o["order_number"]
    assert doc["items"][0]["name"] == "Hinge" and doc["items"][0]["total"] == pytest.approx(40)
    assert d["commissions_total"] == pytest.approx(40)


@pytest.mark.asyncio
async def test_other_orgs_employee_is_404(client, db):
    H, org, _ = await _register(client)
    _, _, other_owner = await _register(client)
    r = await client.get(f"/api/payroll/calculation/detail?employee_id={other_owner}&month={MONTH}", headers=H)
    assert r.status_code == 404
    r = await client.post("/api/payroll/adjustment", json={
        "employee_id": other_owner, "kind": "bonus", "amount": 5}, headers=H)
    assert r.status_code == 404


# ── E3: payroll scope on every endpoint ───────────────────────────────────────
async def _scoped_setup(client, db):
    """Owner, a payroll manager whose scope we vary, and two teammates (in / out of scope)."""
    H, org, owner = await _register(client)
    staff = await _role(client, H, "Staff", template="Specialist")
    inside, _ = await _person(client, H, "Inside", staff["id"], wage=100)
    outside, _ = await _person(client, H, "Outside", staff["id"], wage=100)
    mgr_role = await _role(client, H, "Payroll mgr", template="Manager",
                           payroll_scope="selected", payroll_employee_ids=[inside])
    mgr, M = await _person(client, H, "Mgr", mgr_role["id"], wage=100)
    return H, org, owner, inside, outside, mgr, M, mgr_role


@pytest.mark.asyncio
async def test_scope_selected_filters_lists(client, db):
    H, org, owner, inside, outside, mgr, M, _ = await _scoped_setup(client, db)
    calc = (await client.get(f"/api/payroll/calculation?month={MONTH}", headers=M)).json()
    assert [i["employee_id"] for i in calc["items"]] == [inside]
    assert (await client.get(f"/api/payroll/calculation?month={MONTH}&employee_id={outside}", headers=M)).json()["items"] == []

    # runs: owner accrues both; the manager sees only Inside's
    for e in (inside, outside):
        assert (await client.post("/api/payroll/calculation/accrue", json={"employee_id": e, "month": PAST}, headers=H)).status_code == 201
    runs = (await client.get("/api/payroll/runs", headers=M)).json()["items"]
    assert {r["employee_id"] for r in runs} == {inside}

    # rules: a person-specific rule outside scope is hidden; shop-wide ones stay visible
    await _rule(db, org, name="Out rule", event="labor_performed", calc_type="flat", amount=1, employee_id=outside)
    await _rule(db, org, name="Shop rule", event="labor_performed", calc_type="flat", amount=1)
    names = {r["name"] for r in (await client.get("/api/payroll/rules", headers=M)).json()["items"]}
    assert names == {"Shop rule"}

    # coefficients
    for e in (inside, outside):
        assert (await client.post("/api/payroll/coefficients", json={
            "employee_id": e, "scope_type": "product", "scope_ref": "p1", "coefficient": 2}, headers=H)).status_code == 201
    co = (await client.get("/api/payroll/coefficients", headers=M)).json()["items"]
    assert {c["employee_id"] for c in co} == {inside}

    # reports: rows + exports only cover the scope
    rng = f"date_from={MONTH}-01&date_to={MONTH}-28"
    rep = (await client.get(f"/api/reports/run/payroll?{rng}", headers=M)).json()
    assert [r[0] for r in rep["rows"]] == ["Inside"]
    csv_r = await client.get(f"/api/reports/export-csv/payroll?{rng}", headers=M)
    assert [r[0] for r in list(csv.reader(io.StringIO(csv_r.text)))[1:]] == ["Inside"]
    assert (await client.get(f"/api/reports/run/payroll?{rng}&employee_id={outside}", headers=M)).status_code == 403
    assert (await client.get(f"/api/reports/export/payroll?{rng}&employee_id={outside}", headers=M)).status_code == 403
    assert (await client.get(f"/api/reports/run/labor_cost?{rng}", headers=M)).status_code == 200


@pytest.mark.asyncio
async def test_scope_selected_refuses_out_of_scope_actions(client, db):
    H, org, owner, inside, outside, mgr, M, _ = await _scoped_setup(client, db)
    till = (await client.post("/api/cash/accounts", json={"name": "Till"}, headers=H)).json()["id"]
    run = (await client.post("/api/payroll/calculation/accrue", json={"employee_id": outside, "month": PAST}, headers=H)).json()
    adj = await _bonus(client, H, outside, 5)
    coeff = (await client.post("/api/payroll/coefficients", json={
        "employee_id": outside, "scope_type": "product", "scope_ref": "p1", "coefficient": 2}, headers=H)).json()["id"]
    out_rule = await _rule(db, org, name="Out", event="labor_performed", calc_type="flat", amount=1, employee_id=outside)
    body_rule = {"name": "R", "event": "labor_performed", "calc_type": "flat", "amount": 1}

    denied = [
        ("get", f"/api/payroll/calculation/detail?employee_id={outside}&month={MONTH}", None),
        ("get", f"/api/payroll/balance/{outside}", None),
        ("post", "/api/payroll/adjustment", {"employee_id": outside, "kind": "bonus", "amount": 5}),
        ("delete", f"/api/payroll/adjustment/{adj}", None),
        ("post", "/api/payroll/calculation/accrue", {"employee_id": outside, "month": "2026-05"}),
        ("post", "/api/payroll/run", {"employee_id": outside, "period_start": "2026-04-01", "period_end": "2026-04-30"}),
        ("post", "/api/payroll/run-all", {"period_start": "2026-04-01", "period_end": "2026-04-30", "employee_ids": [outside]}),
        ("post", "/api/payroll/attendance-wages", {"employee_id": outside, "date_from": "2026-04-01", "date_to": "2026-04-30"}),
        ("post", f"/api/payroll/runs/{run['id']}/pay", {"account_id": till}),
        ("post", f"/api/payroll/runs/{run['id']}/void", {"reason": "x"}),
        ("post", "/api/payroll/rules", {**body_rule, "employee_id": outside}),
        ("post", "/api/payroll/rules", body_rule),                     # shop-wide needs scope = all
        ("put", f"/api/payroll/rules/{out_rule.id}", {**body_rule, "employee_id": outside}),
        ("delete", f"/api/payroll/rules/{out_rule.id}", None),
        ("post", "/api/payroll/coefficients", {"employee_id": outside, "scope_type": "product", "scope_ref": "p2", "coefficient": 2}),
        ("delete", f"/api/payroll/coefficients/{coeff}", None),
        ("get", f"/api/print/payroll-statement/{outside}", None),
    ]
    for method, url, body in denied:
        kw = {"headers": M}
        if body is not None:
            kw["json"] = body
        r = await getattr(client, method)(url, **kw)
        assert r.status_code == 403, (method, url, r.status_code, r.text)

    # the same actions on someone inside the scope go through
    assert (await client.get(f"/api/payroll/calculation/detail?employee_id={inside}&month={MONTH}", headers=M)).status_code == 200
    assert (await client.post("/api/payroll/adjustment", json={"employee_id": inside, "kind": "bonus", "amount": 5}, headers=M)).status_code == 201
    assert (await client.post("/api/payroll/rules", json={**body_rule, "employee_id": inside}, headers=M)).status_code == 201
    r = await client.post("/api/payroll/run-all", json={"period_start": "2026-04-01", "period_end": "2026-04-30"}, headers=M)
    assert r.status_code == 201 and [x["employee_id"] for x in r.json()["results"]] == [inside]
    assert (await client.get(f"/api/print/payroll-statement/{inside}", headers=M)).status_code == 200
    # nothing out of scope was touched
    db.expire_all()
    assert (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.id == adj))).scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_scope_own_and_all(client, db):
    H, org, owner, inside, outside, mgr, M, role = await _scoped_setup(client, db)
    base = {k: role[k] for k in ("name", "location_scope", "location_ids")}
    r = await client.put(f"/api/settings/rbac/roles/{role['id']}", json={**base, "payroll_scope": "own"}, headers=H)
    assert r.status_code == 200 and r.json()["payroll_scope"] == "own" and r.json()["payroll_employee_ids"] == []
    calc = (await client.get(f"/api/payroll/calculation?month={MONTH}", headers=M)).json()
    assert [i["employee_id"] for i in calc["items"]] == [mgr]
    assert (await client.get(f"/api/payroll/balance/{inside}", headers=M)).status_code == 403

    r = await client.put(f"/api/settings/rbac/roles/{role['id']}", json={**base, "payroll_scope": "all"}, headers=H)
    assert r.status_code == 200
    calc = (await client.get(f"/api/payroll/calculation?month={MONTH}", headers=M)).json()
    assert {inside, outside, mgr, owner} <= {i["employee_id"] for i in calc["items"]}
    assert (await client.get(f"/api/payroll/balance/{outside}", headers=M)).status_code == 200


@pytest.mark.asyncio
async def test_role_editor_payroll_scope_validation(client, db):
    H, org, owner, inside, outside, mgr, M, role = await _scoped_setup(client, db)
    assert role["payroll_scope"] == "selected" and role["payroll_employee_ids"] == [inside]
    bad = await client.post("/api/settings/rbac/roles", json={"name": "X", "payroll_scope": "team"}, headers=H)
    assert bad.status_code == 422
    # a save that doesn't send the field keeps it (never silently widens)
    r = await client.put(f"/api/settings/rbac/roles/{role['id']}", json={"name": "Payroll mgr 2"}, headers=H)
    assert r.status_code == 200 and r.json()["payroll_scope"] == "selected"
    db.expire_all()
    stored = (await db.execute(select(Role).where(Role.id == role["id"]))).scalar_one()
    assert stored.payroll_employee_ids == [inside]

    # a role editor limited to some people can't hand out a wider payroll scope
    editor_role = await _role(client, H, "Role editor", template="Full access",
                              payroll_scope="selected", payroll_employee_ids=[inside])
    _, E = await _person(client, H, "Ed", editor_role["id"])
    r = await client.post("/api/settings/rbac/roles", json={"name": "Wide", "payroll_scope": "all"}, headers=E)
    assert r.status_code == 403
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Wider", "payroll_scope": "selected", "payroll_employee_ids": [inside, outside]}, headers=E)
    assert r.status_code == 403
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Same", "payroll_scope": "selected", "payroll_employee_ids": [inside]}, headers=E)
    assert r.status_code == 201, r.text
