"""144jr — Owner P&L: revenue/COGS/OpEx math, draft exclusion, org isolation;
plus RegionPack resolver sanity."""
import uuid
from datetime import datetime

from backend.models import Invoice, InvoiceLine, OrderLine, Transaction
from backend.services import regions
from backend.services.finance_pnl import compute_pnl


async def _org(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]


def test_regionpack_resolves_us_eu_latam():
    us = regions.pack_for("US")
    assert us["currency_code"] == "USD" and us["tax"]["mode"] == "imposed" and us["einvoice"]["standard"] == "none"
    de = regions.pack_for("DE")
    assert de["currency_code"] == "EUR" and de["tax"]["name"] == "VAT" and de["einvoice"]["network"] == "peppol"
    assert "datev" in de["accounting_exports"]
    mx = regions.pack_for("MX")
    assert mx["region"] == "latam" and mx["einvoice"]["standard"] == "cfdi" and mx["dunning_channel"] == "whatsapp"
    br = regions.pack_for("BR")
    assert br["currency_code"] == "BRL" and br["einvoice"]["standard"] == "nfe"


async def test_pnl_math_and_isolation(client, tenant, db):
    a = await _org(client, tenant)
    r = await client.post("/api/auth/register", json={
        "org_name": "Shop B", "name": "B", "email": f"b-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    b = (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()["org_id"]

    # org A: one issued invoice in range, net rev = 1000-100 = 900, tax 90
    inv = Invoice(id=uuid.uuid4().hex, org_id=a, invoice_number="A1", status="paid",
                  subtotal=1000, discount=100, tax=90, total=990, issued_at=datetime(2026, 9, 10))
    db.add(inv)
    # a draft invoice must be excluded
    db.add(Invoice(id=uuid.uuid4().hex, org_id=a, invoice_number="A2", status="draft",
                   subtotal=500, discount=0, tax=0, total=500, issued_at=datetime(2026, 9, 12)))
    # product line → order line cost 150 × qty 2 = 300 COGS. The parent Order + the expense's
    # Account must really exist now that FKs are enforced (orphan rows used to slip through).
    from backend.models import Account as _Account
    from backend.models import Location as _Location
    from backend.models import Order as _Order
    db.add(_Location(id="loc1", org_id=a, name="Main"))
    await db.flush()
    db.add(_Order(id="o1", org_id=a, order_number="O1", location_id="loc1"))
    db.add(_Account(id="acc1", org_id=a, name="Till"))
    await db.flush()
    ol = OrderLine(id=uuid.uuid4().hex, order_id="o1", line_type="product", kind="product", description="Part X", qty=2, unit_price=500, cost_price=150, total=1000)
    db.add(ol)
    db.add(InvoiceLine(id=uuid.uuid4().hex, invoice_id=inv.id, order_line_id=ol.id, kind="product",
                       qty=2, unit_price=500, total=1000))
    # expense 200 in range
    db.add(Transaction(id=uuid.uuid4().hex, org_id=a, account_id="acc1", txn_type="expense",
                       amount=-200, status="active", actual_date=datetime(2026, 9, 15)))
    # org B invoice must NOT leak into A's P&L
    db.add(Invoice(id=uuid.uuid4().hex, org_id=b, invoice_number="B1", status="paid",
                   subtotal=9999, discount=0, tax=0, total=9999, issued_at=datetime(2026, 9, 11)))
    await db.commit()

    p = await compute_pnl(db, a, "2026-09-01", "2026-09-30")
    assert p["revenue"] == 900.0, p
    assert p["tax_collected"] == 90.0
    assert p["cogs"] == 300.0
    assert p["gross_profit"] == 600.0
    assert p["opex"] == 200.0
    assert p["net_profit"] == 400.0
    assert p["gross_margin_pct"] == 66.7 and p["net_margin_pct"] == 44.4
    assert p["invoice_count"] == 1          # draft excluded

    # isolation: B sees only its own
    pb = await compute_pnl(db, b, "2026-09-01", "2026-09-30")
    assert pb["revenue"] == 9999.0 and pb["cogs"] == 0.0


async def test_pnl_api(client, tenant):
    r = await client.get("/api/finance/pnl?date_from=2026-09-01&date_to=2026-09-30", headers=tenant)
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"revenue", "cogs", "gross_profit", "opex", "net_profit", "net_margin_pct"}
    rp = await client.get("/api/regions/pack", headers=tenant)
    assert rp.status_code == 200 and "currency_code" in rp.json()
