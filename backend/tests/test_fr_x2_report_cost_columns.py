"""Final review SEC-04: report cost and profit columns follow the viewer's permissions.

Report rows are plain lists, so the cost-redaction middleware (which strips fields by name) never
saw them, and the XLSX/CSV exports skip the middleware altogether. A role with can_view_reports but
without can_view_purchase_price / can_see_profit saw unit cost, stock value and profit in
dead_stock, stock_levels, assortment, sold_products, provided_services, technicians (and the
purchase / reorder / expiry estimates). Every registered report, on screen and in both exports,
now drops those columns server-side.
"""
import csv
import io
import re
import uuid

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

# Everything a reports user may hold EXCEPT the two money-sensitive rights.
NO_COST = {"can_view_reports": True, "can_view_finance": True, "can_view_cashflow": True,
           "can_view_balance": True, "can_export_sales": True, "can_view_orders": True,
           "can_view_inventory": True, "can_view_purchase_price": False, "can_see_profit": False}
ONLY_PROFIT = {**NO_COST, "can_see_profit": True}

SENSITIVE_HEADER = re.compile(r"cost|profit|markup|margin|tied-up|value at risk|^value$", re.I)
# The seeded costs, and what a viewer could compute from them (stock value, gross profit).
COST_TOKENS = ("37.13", "74.26", "41.17", "23.19", "57.83", "76.81")


async def _login(client, db, org_id, perms):
    from backend.models import Employee, Role
    from backend.services.auth import hash_password
    role = Role(org_id=org_id, name=f"R {uuid.uuid4().hex[:4]}", permissions=perms, perms_explicit=True)
    db.add(role)
    await db.flush()
    email = f"nc-{uuid.uuid4().hex[:6]}@x.dev"
    db.add(Employee(org_id=org_id, name="No cost", email=email, password_hash=hash_password("pw-123456789"),
                    active=True, role_id=role.id))
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-123456789"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def _seed(client, tenant, db):
    from backend.models import Order, Part
    from backend.routers import reports
    from backend.tests import kit_helpers as K
    reports._dash_cache.clear()
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = await K.loc(client, tenant)
    idle = await K.part(client, tenant, loc, "Idle screen", 99, 37.13, qty=2)       # dead / stock reports
    sold = await K.part(client, tenant, loc, "Sold battery", 99, 41.17, qty=5)
    p = (await db.execute(select(Part).where(Part.id == idle))).scalar_one()
    p.min_stock = 5                                                                   # purchase list / forecast
    await db.commit()
    svc = (await client.post("/api/catalog/services", headers=tenant,
                             json={"name": "Board fix", "price": 100, "cost_price": 23.19})).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=tenant)).json()["id"]
    for body in ({"line_type": "part", "kind": "product", "ref_id": sold, "description": "Sold battery",
                  "qty": 1, "unit_price": 99},
                 {"line_type": "labour", "kind": "service", "ref_id": svc, "description": "Board fix", "cost_price": 23.19,
                  "qty": 1, "unit_price": 100}):
        r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json=body)
        assert r.status_code in (200, 201), r.text
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.assigned_to_id = me["id"]
    await db.commit()
    sts = (await client.get("/api/statuses", headers=tenant)).json()
    closed = next(s["key"] for s in (sts.get("items", sts) if isinstance(sts, dict) else sts)
                  if s.get("status_group") == "closed")
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": closed}, headers=tenant)
    assert r.status_code == 200, r.text
    return me["org_id"]


def _cells(cols, rows):
    return " | ".join(str(c) for c in cols) + "\n" + "\n".join(" | ".join(str(v) for v in r) for r in rows)


async def test_owner_still_sees_the_cost_columns(client, tenant, db):
    await _seed(client, tenant, db)
    seen = ""
    for key in ("dead_stock", "stock_levels", "assortment", "sold_products", "provided_services", "technicians"):
        r = (await client.get(f"/api/reports/run/{key}", headers=tenant)).json()
        assert any(SENSITIVE_HEADER.search(c) for c in r["columns"]), key
        seen += _cells(r["columns"], r["rows"])
    for tok in ("37.13", "74.26", "41.17", "23.19"):
        assert tok in seen, tok                     # the seed really reaches the reports


async def test_every_report_hides_cost_and_profit_from_a_role_without_them(client, tenant, db):
    from backend.routers.reports import REPORTS
    org = await _seed(client, tenant, db)
    H = await _login(client, db, org, NO_COST)
    ran = 0
    for key in REPORTS:
        r = await client.get(f"/api/reports/run/{key}", headers=H)
        if r.status_code == 403:                    # gated as a whole (payroll, wo_profit, …)
            continue
        assert r.status_code == 200, (key, r.text)
        ran += 1
        body = r.json()
        bad = [c for c in body["columns"] if SENSITIVE_HEADER.search(c)]
        assert not bad, (key, bad)
        assert all(len(row) == len(body["columns"]) for row in body["rows"]), key
        text = _cells(body["columns"], body["rows"])
        for tok in COST_TOKENS:
            assert tok not in text, (key, tok)
        # CSV export: same columns, no cost figures
        c = await client.get(f"/api/reports/export-csv/{key}", headers=H)
        assert c.status_code == 200, (key, c.text)
        head = next(csv.reader(io.StringIO(c.text)))
        assert head == body["columns"], key
        for tok in COST_TOKENS:
            assert tok not in c.text, (key, "csv", tok)
        # XLSX export
        import openpyxl
        x = await client.get(f"/api/reports/export/{key}", headers=H)
        assert x.status_code == 200, (key, x.text)
        ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
        vals = [list(v) for v in ws.iter_rows(values_only=True)]
        assert [v for v in vals[3] if v is not None] == body["columns"], key
        flat = " ".join(str(v) for row in vals[4:] for v in row)
        for tok in COST_TOKENS:
            assert tok not in flat, (key, "xlsx", tok)
    assert ran >= 15                                 # most reports really ran for this role


async def test_profit_without_purchase_price_shows_profit_but_no_cost_or_markup(client, tenant, db):
    org = await _seed(client, tenant, db)
    H = await _login(client, db, org, ONLY_PROFIT)
    r = (await client.get("/api/reports/run/sold_products", headers=H)).json()
    assert r["columns"] == ["Product", "Qty sold", "Revenue", "Gross profit"]
    assert r["rows"][0][3] == 57.83
    t = (await client.get("/api/reports/run/technicians", headers=H)).json()
    assert "Profit" in t["columns"]
