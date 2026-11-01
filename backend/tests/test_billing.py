"""Tekly subscription billing (services/billing.py, routers/billing.py).

- signup starts a 14-day trial with Pro unlocked; when it ends the shop is read-only (reads and
  exports work, writes get 402), never deleted;
- cancelled / unpaid subscriptions are read-only too; a failed renewal gets a grace period;
- one global price list, the website's numbers (website/site.config.js): Solo (2 staff, 1 location,
  150 new jobs a month, core features), Shop (4 staff included, + inventory, labels, clock, reports,
  automatic notifications) and Pro (8 staff included, + POs, payroll, transfers, AI, accounting, SSO,
  API); on Shop and Pro each extra team member is a $6 seat — enforced on the server for
  staff routes, public-API keys and the notification engine, and reported in /api/auth/me;
- custom-priced shops (billing_mode "custom") never reach Stripe; their term end makes them
  read-only; the admin sets mode, plan, term, note and the founding-price lock;
- the Stripe Billing webhook needs a valid signature and applies each event id exactly once,
  ignoring an older event that arrives after a newer one;
- config fails closed when deployed.
Stripe itself is never called: services.billing._stripe is replaced in every test that needs it.
"""
import hashlib
import hmac
import json
import time
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from backend.config import Settings, settings
from backend.models import BillingEvent, Employee, Location, Organisation
from backend.services import billing

WHSEC = "whsec_test_billing"
PRICES = {"STRIPE_BILLING_PRICE_SOLO_MONTH": "price_om", "STRIPE_BILLING_PRICE_SOLO_YEAR": "price_oy",
          "STRIPE_BILLING_PRICE_SHOP_MONTH": "price_sm", "STRIPE_BILLING_PRICE_SHOP_YEAR": "price_sy",
          "STRIPE_BILLING_PRICE_PRO_MONTH": "price_pm", "STRIPE_BILLING_PRICE_PRO_YEAR": "price_py",
          "STRIPE_BILLING_PRICE_SHOP_SEAT_MONTH": "price_seat_m", "STRIPE_BILLING_PRICE_SHOP_SEAT_YEAR": "price_seat_y"}


@pytest.fixture
def stripe_cfg(monkeypatch):
    """Billing configured with test-mode values, and a fake Stripe that records calls."""
    monkeypatch.setattr(settings, "STRIPE_BILLING_SECRET_KEY", "sk_test_billing")
    monkeypatch.setattr(settings, "STRIPE_BILLING_WEBHOOK_SECRET", WHSEC)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.tekly.test")
    for k, v in PRICES.items():
        monkeypatch.setattr(settings, k, v)
    calls = []

    async def fake_stripe(method, path, data=None):
        calls.append((method, path, dict(data or {})))
        if path == "/checkout/sessions":
            return {"id": "cs_test_1", "url": "https://checkout.stripe.test/cs_test_1"}
        if path == "/billing_portal/sessions":
            return {"url": "https://billing.stripe.test/p_1"}
        if path.startswith("/subscriptions/"):
            items = [{"id": "si_1", "price": {"id": data.get("items[0][price]", "price_pm")}}]
            if data.get("items[1][price]"):
                items.append({"id": data.get("items[1][id]", "si_seat"), "price": {"id": data["items[1][price]"]}})
            return {"id": path.rsplit("/", 1)[1], "object": "subscription", "status": "active",
                    "customer": "cus_1", "items": {"data": items}}
        if path == "/subscription_items":
            return {"id": "si_seat_new", "quantity": int(data["quantity"])}
        if path.startswith("/subscription_items/"):
            return {"id": path.rsplit("/", 1)[1]}
        raise AssertionError(path)
    monkeypatch.setattr(billing, "_stripe", fake_stripe)
    return calls


async def _signup(client, plan=None):
    email = f"owner-{uuid.uuid4().hex[:8]}@test.dev"
    body = {"org_name": "Bench Co", "name": "Owner", "email": email, "password": "pw-test-12345"}
    if plan:
        body["plan"] = plan
    r = await client.post("/api/auth/register", json=body)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["org_id"]


async def _set(db, org_id, **fields):
    org = await db.get(Organisation, org_id)
    await db.refresh(org)
    for k, v in fields.items():
        setattr(org, k, v)
    await db.commit()


async def _org(db, org_id):
    db.expire_all()
    return (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()


# ── Trial ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signup_starts_a_14_day_trial_with_pro_unlocked(client, db):
    hdr, org_id = await _signup(client, plan="solo")
    org = await _org(db, org_id)
    assert org.sub_status == "trial" and org.plan == "solo" and org.billing_mode == "stripe"
    assert org.trial_ends_at == date.today() + timedelta(days=14)
    me = (await client.get("/api/auth/me", headers=hdr)).json()["billing"]
    assert me["trial_days_left"] == 14 and me["read_only"] is False
    assert me["effective_plan"] == "pro" and set(me["features"]) == set(billing.FEATURES)
    assert me["limits"] == {"team_members": 8, "locations": None, "jobs_per_month": None}
    assert me["usage"]["team_members"] == 1
    # Pro routes open during the trial
    assert (await client.get("/api/payroll/runs", headers=hdr)).status_code == 200


@pytest.mark.asyncio
async def test_trial_expiry_makes_the_shop_read_only_not_deleted(client, db, stripe_cfg):
    hdr, org_id = await _signup(client)
    made = await client.post("/api/clients", json={"name": "Before"}, headers=hdr)
    assert made.status_code == 201
    await _set(db, org_id, trial_ends_at=date.today())   # the trial's last moment has passed

    me = (await client.get("/api/auth/me", headers=hdr)).json()["billing"]
    assert me["read_only"] is True and me["trial_days_left"] == 0
    blocked = await client.post("/api/clients", json={"name": "After"}, headers=hdr)
    assert blocked.status_code == 402 and blocked.headers.get("X-Billing-Read-Only") == "1"
    assert "trial has ended" in blocked.json()["detail"]
    for path in ("/api/clients", "/api/clients/export/csv"):   # reads and exports still work
        assert (await client.get(path, headers=hdr)).status_code == 200, path
    assert (await client.put(f"/api/clients/{made.json()['id']}", json={"name": "x"},
                             headers=hdr)).status_code == 402
    # ...and it can always pay its way out
    assert (await client.get("/api/billing", headers=hdr)).status_code == 200
    r = await client.post("/api/billing/checkout", json={"plan": "pro", "period": "year"}, headers=hdr)
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.test/")
    org = await _org(db, org_id)
    assert org.name == "Bench Co"   # nothing removed


@pytest.mark.asyncio
@pytest.mark.parametrize("status,read_only", [
    ("active", False), ("past_due", False), ("unpaid", True), ("churned", True),
    ("trial_lost", True), ("mystery", True)])
async def test_subscription_states(db, status, read_only):
    org = Organisation(name="X", sub_status=status, plan="pro", past_due_since=date.today())
    assert billing.state(org).read_only is read_only


def test_past_due_grace_period_then_read_only():
    org = Organisation(name="X", sub_status="past_due", plan="pro")
    org.past_due_since = date.today() - timedelta(days=billing.PAST_DUE_GRACE_DAYS)
    assert not billing.state(org).read_only
    org.past_due_since = date.today() - timedelta(days=billing.PAST_DUE_GRACE_DAYS + 1)
    assert billing.state(org).read_only


# ── Plan gating ──────────────────────────────────────────────────────────────

PRO_ROUTES = [
    ("GET", "/api/inventory/po", None, "purchase_orders"),
    ("POST", "/api/inventory/po", {}, "purchase_orders"),
    ("POST", "/api/orders/x/create-po", {}, "purchase_orders"),
    ("GET", "/api/payroll/runs", None, "payroll"),
    ("GET", "/api/catalog/services/x/commissions", None, "payroll"),
    ("GET", "/api/transfers", None, "multi_location"),
    ("POST", "/api/ai/estimate/draft", {"complaint": "cracked screen"}, "ai_estimates"),
    ("POST", "/api/ai/recognize-items", {}, "ai_estimates"),
    ("GET", "/api/integrations/qbo/status", None, "accounting_sync"),
    ("GET", "/api/integrations/xero/status", None, "accounting_sync"),
    ("GET", "/api/sso/config", None, "sso"),
    ("GET", "/api/api-keys", None, "api_keys"),
]
SHOP_ROUTES = [
    ("GET", "/api/inventory/parts", None, "inventory"),
    ("POST", "/api/inventory/postings/commit", {}, "inventory"),
    ("GET", "/api/stock-docs/write-offs", None, "inventory"),
    ("GET", "/api/stocktake", None, "inventory"),
    ("GET", "/api/suppliers", None, "inventory"),
    ("POST", "/api/settings/warehouses", {"name": "W"}, "inventory"),
    ("POST", "/api/orders/x/reserve-stock", {}, "inventory"),
    ("GET", "/api/print/templates", None, "labels"),
    ("POST", "/api/scan/resolve", {"code": "1"}, "labels"),
    ("GET", "/api/timeclock/active", None, "time_clock"),
    ("POST", "/api/timeclock/start", {}, "time_clock"),
    ("GET", "/api/reports/dashboard", None, "reports"),
    ("GET", "/api/analytics/trends", None, "reports"),
    ("GET", "/api/custom-reports", None, "reports"),
    ("POST", "/api/notifications/rules", {}, "notification_rules"),
    ("GET", "/api/automations", None, "notification_rules"),
]
# Reads that core (Solo) screens make inside otherwise-gated areas.
OPEN_FOR_SOLO = ["/api/reports/company-dashboard", "/api/reports/home-feed", "/api/notifications/rules",
                 "/api/settings/warehouses"]


def _refused(r, need, feature):
    return (r.status_code == 403 and r.headers.get("X-Plan-Required") == need
            and billing.FEATURES[feature][0] in r.json()["detail"])


@pytest.mark.asyncio
async def test_shop_is_refused_pro_features_but_has_shop_ones(client, db):
    hdr, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", plan="shop")
    me = (await client.get("/api/auth/me", headers=hdr)).json()["billing"]
    assert set(me["features"]) == {"inventory", "labels", "time_clock", "reports", "notification_rules"}
    assert me["limits"] == {"team_members": 4, "locations": None, "jobs_per_month": None}
    assert me["extra_seat_price"] == 6 and me["extra_seats"] == 0
    for method, path, body, feature in PRO_ROUTES:
        r = await client.request(method, path, json=body, headers=hdr)
        assert _refused(r, "pro", feature), (path, r.status_code, r.text)
    for method, path, body, _ in SHOP_ROUTES:
        r = await client.request(method, path, json=body, headers=hdr)
        assert r.headers.get("X-Plan-Required") is None, path


@pytest.mark.asyncio
async def test_solo_is_refused_shop_features_but_keeps_the_core(client, db):
    hdr, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", plan="solo")
    me = (await client.get("/api/auth/me", headers=hdr)).json()["billing"]
    assert me["features"] == [] and me["limits"]["jobs_per_month"] == 150
    for method, path, body, feature in SHOP_ROUTES:
        r = await client.request(method, path, json=body, headers=hdr)
        assert _refused(r, "shop", feature), (path, r.status_code, r.text)
    for path in OPEN_FOR_SOLO:
        assert (await client.get(path, headers=hdr)).status_code == 200, path
    # core: contacts, jobs, the technician app
    assert (await client.post("/api/clients", json={"name": "Ok"}, headers=hdr)).status_code == 201
    assert (await client.post("/api/orders", json={}, headers=hdr)).status_code in (200, 201)
    assert (await client.get("/api/tech/dashboard", headers=hdr)).status_code == 200


@pytest.mark.asyncio
async def test_pro_gets_everything(client, db):
    hdr, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", plan="pro")
    for method, path, body, _ in PRO_ROUTES + SHOP_ROUTES:
        r = await client.request(method, path, json=body, headers=hdr)
        assert r.headers.get("X-Plan-Required") is None, path


def test_every_gated_route_is_matched():
    for method, path, _, feature in PRO_ROUTES + SHOP_ROUTES:
        assert billing.feature_for_path(path, method) == feature, path
    for path in ("/api/orders/abc", "/api/integrations/email/status", "/api/ai/orders/1/summary",
                 "/api/inventory/parts/p1/stock", "/api/print/invoices/i1", "/api/catalog/products",
                 "/api/catalog/code.svg", "/api/tech/notifications", "/api/notifications/log",
                 *OPEN_FOR_SOLO):
        assert billing.feature_for_path(path, "GET") is None, path
    assert billing.feature_for_path("/api/inventory/parts/p1/stock", "POST") == "inventory"
    # routes added after billing was written (serial units, extra barcodes, bin and serial labels)
    assert billing.feature_for_path("/api/documents/render-bin-labels", "POST") == "labels"
    assert billing.feature_for_path("/api/documents/render-serial-labels", "POST") == "labels"
    assert billing.feature_for_path("/api/catalog/products/p1/barcodes", "POST") == "labels"
    assert billing.feature_for_path("/api/catalog/products/barcodes/generate-missing", "POST") == "labels"
    assert billing.feature_for_path("/api/catalog/products/p1/barcodes", "GET") is None
    assert billing.feature_for_path("/api/catalog/products/p1/serials/generate", "POST") == "inventory"
    assert billing.feature_for_path("/api/serials/u1", "PATCH") == "inventory"
    assert billing.feature_for_path("/api/serials", "GET") is None


def test_legacy_starter_plan_is_shop():
    org = Organisation(name="X", sub_status="active", plan="starter")
    assert billing.state(org).effective_plan == "shop"


@pytest.mark.asyncio
async def test_solo_allows_two_team_members(client, db):
    hdr, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", plan="solo")
    assert (await client.post("/api/staff/employees", json={"name": "T0", "email": f"t0-{org_id[:6]}@x.dev"},
                              headers=hdr)).status_code == 201
    over = await client.post("/api/staff/employees", json={"name": "X", "email": f"x-{org_id[:6]}@x.dev"}, headers=hdr)
    assert over.status_code == 403 and "up to 2" in over.json()["detail"]
    assert over.headers["X-Plan-Required"] == "shop"
    inv = await client.post("/api/staff/invites", json={"email": f"inv-{org_id[:6]}@x.dev"}, headers=hdr)
    assert inv.status_code == 403
    # deactivate one, then reactivating someone else is the only way back to the cap
    emp = (await db.execute(select(Employee).where(Employee.org_id == org_id, Employee.name == "T0"))).scalar_one()
    assert (await client.patch(f"/api/staff/employees/{emp.id}", json={"active": False},
                               headers=hdr)).status_code == 200
    assert (await client.post("/api/staff/employees", json={"name": "T9", "email": f"t9-{org_id[:6]}@x.dev"},
                              headers=hdr)).status_code == 201
    assert (await client.patch(f"/api/staff/employees/{emp.id}", json={"active": True},
                               headers=hdr)).status_code == 403


@pytest.mark.asyncio
async def test_shop_bills_team_members_beyond_four_as_seats(client, db, stripe_cfg):
    hdr, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", plan="shop", billing_period="month",
               stripe_subscription_id="sub_1", stripe_subscription_item_id="si_1")
    for i in range(3):                                   # owner + 3 = the 4 included
        assert (await client.post("/api/staff/employees", json={"name": f"T{i}", "email": f"t{i}-{org_id[:6]}@x.dev"},
                                  headers=hdr)).status_code == 201
    assert not [c for c in stripe_cfg if "subscription_items" in c[1]]   # nothing billed yet
    fifth = await client.post("/api/staff/employees", json={"name": "T8", "email": f"t8-{org_id[:6]}@x.dev"}, headers=hdr)
    assert fifth.status_code == 201                       # allowed, and billed as a seat
    assert stripe_cfg[-1][:2] == ("POST", "/subscription_items")
    assert stripe_cfg[-1][2]["price"] == "price_seat_m" and stripe_cfg[-1][2]["quantity"] == "1"
    assert (await _org(db, org_id)).stripe_seat_item_id == "si_seat_new"
    sixth = await client.post("/api/staff/employees", json={"name": "T9", "email": f"t9-{org_id[:6]}@x.dev"}, headers=hdr)
    assert sixth.status_code == 201 and stripe_cfg[-1][1] == "/subscription_items/si_seat_new"
    assert stripe_cfg[-1][2]["quantity"] == "2"
    me = (await client.get("/api/auth/me", headers=hdr)).json()["billing"]
    assert me["extra_seats"] == 2
    # removing people shrinks the seats, down to deleting the item
    for name in ("T8", "T9"):
        emp = (await db.execute(select(Employee).where(Employee.org_id == org_id, Employee.name == name))).scalar_one()
        assert (await client.delete(f"/api/staff/employees/{emp.id}", headers=hdr)).status_code == 200
    assert stripe_cfg[-1][:2] == ("DELETE", "/subscription_items/si_seat_new")
    assert (await _org(db, org_id)).stripe_seat_item_id is None


@pytest.mark.asyncio
async def test_solo_monthly_job_cap_blocks_new_jobs_only(client, db, monkeypatch):
    monkeypatch.setitem(billing.PLAN_LIMITS["solo"], "jobs_per_month", 2)
    hdr, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", plan="solo")
    ids = [(await client.post("/api/orders", json={}, headers=hdr)).json()["id"] for _ in range(2)]
    third = await client.post("/api/orders", json={}, headers=hdr)
    assert third.status_code == 403 and third.headers["X-Plan-Required"] == "shop"
    assert "2 new jobs a month" in third.json()["detail"]
    est = await client.post("/api/orders", json={"is_estimate": True}, headers=hdr)
    assert est.status_code in (200, 201)                               # estimates aren't capped
    assert (await client.post(f"/api/orders/{est.json()['id']}/convert", headers=hdr)).status_code == 403
    assert (await client.post(f"/api/orders/{ids[0]}/duplicate", json={}, headers=hdr)).status_code == 403
    assert (await client.get(f"/api/orders/{ids[0]}", headers=hdr)).status_code == 200   # existing jobs work
    me = (await client.get("/api/auth/me", headers=hdr)).json()["billing"]
    assert me["usage"]["jobs_per_month"] == 2
    await _set(db, org_id, plan="shop")
    assert (await client.post("/api/orders", json={}, headers=hdr)).status_code in (200, 201)


@pytest.mark.asyncio
@pytest.mark.parametrize("plan", ["shop", "pro"])
async def test_solo_has_one_location_and_shop_and_pro_bill_per_location(client, db, stripe_cfg, plan):
    hdr, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", plan="solo")
    r = await client.post("/api/staff/locations", json={"name": "Second"}, headers=hdr)
    assert r.status_code == 403 and r.headers.get("X-Plan-Required") == "shop"

    await _set(db, org_id, plan=plan, stripe_subscription_id="sub_1", stripe_subscription_item_id="si_1")
    r = await client.post("/api/staff/locations", json={"name": "Second"}, headers=hdr)
    assert r.status_code == 201
    qty = [c for c in stripe_cfg if c[1] == "/subscriptions/sub_1"]
    assert qty and qty[-1][2]["items[0][quantity]"] == "2"
    assert "items[0][price]" not in qty[-1][2]       # quantity only: a locked price is kept
    # transfers between those locations stay Pro
    assert (await client.get("/api/transfers", headers=hdr)).status_code == (200 if plan == "pro" else 403)


@pytest.mark.asyncio
async def test_api_keys_are_pro_and_read_only_when_lapsed(client, db):
    hdr, org_id = await _signup(client)
    key = (await client.post("/api/api-keys", json={"name": "zap"}, headers=hdr)).json()["key"]
    khdr = {"Authorization": f"Bearer {key}"}
    assert (await client.get("/api/v1/contacts", headers=khdr)).status_code == 200
    await _set(db, org_id, sub_status="active", plan="shop")
    assert (await client.get("/api/v1/contacts", headers=khdr)).status_code == 403
    await _set(db, org_id, sub_status="churned", plan="pro")
    assert (await client.get("/api/v1/contacts", headers=khdr)).status_code == 200
    assert billing.state(await _org(db, org_id)).read_only


@pytest.mark.asyncio
async def test_qbo_autosync_stops_below_pro(db, monkeypatch):
    from backend.services import qbo
    org = Organisation(name="Q", sub_status="active", plan="shop")
    db.add(org)
    await db.commit()

    class Conn:
        connected = True
    pushed = []

    async def fake_conn(_db, _org):
        return Conn()

    async def fake_sync(*a):
        pushed.append(a)
    monkeypatch.setattr(qbo, "get_connection", fake_conn)
    monkeypatch.setattr(qbo, "is_configured", lambda: True)
    monkeypatch.setattr(qbo, "sync_entity", fake_sync)
    await qbo.autosync(db, org.id, [("invoice", "i1")])
    assert pushed == []
    org.plan = "pro"
    await db.commit()
    await qbo.autosync(db, org.id, [("invoice", "i1")])
    assert len(pushed) == 1


@pytest.mark.asyncio
async def test_solo_gets_no_automatic_customer_notifications(client, db):
    from backend.services import automation
    from backend.services import notification_rules as nr
    hdr, org_id = await _signup(client)          # signup seeds the default rules
    oid = (await client.post("/api/orders", json={}, headers=hdr)).json()["id"]
    await _set(db, org_id, sub_status="active", plan="solo")
    assert await nr.evaluate(db, org_id=org_id, order_id=oid, event="created") == \
        {"sent": 0, "scheduled": 0, "matched": 0}
    assert await nr.queue_reminders(db, org_id=org_id) == 0
    assert await automation.run(db, org_id, "order_created") == {"matched": 0, "fired": 0}
    await _set(db, org_id, plan="shop")
    assert (await nr.evaluate(db, org_id=org_id, order_id=oid, event="created"))["matched"] >= 1


def test_trial_suggests_the_plan_that_fits():
    assert billing.suggest_plan({"team_members": 2, "locations": 1}) == "solo"
    assert billing.suggest_plan({"team_members": 3, "locations": 1}) == "shop"
    assert billing.suggest_plan({"team_members": 8, "locations": 1}) == "shop"
    # 5+ people without Pro features: Shop plus $6 seats (owner decision), not Pro
    assert billing.suggest_plan({"team_members": 9, "locations": 1}) == "shop"
    assert billing.suggest_plan({"team_members": 30, "locations": 3}) == "shop"
    # relying on a Pro feature: Pro, whatever the size
    assert billing.suggest_plan({"team_members": 9, "locations": 1}, uses_pro=True) == "pro"
    assert billing.suggest_plan({"team_members": 1, "locations": 1}, uses_pro=True) == "pro"
    assert billing.suggest_plan({"team_members": 1, "locations": 2}) == "shop"


def test_price_table_is_usd_with_ten_month_years_and_tiered_pro():
    t = billing.price_table()
    assert t["solo"]["base"] == {"month": 29, "year": 290}
    assert t["shop"]["base"] == {"month": 69, "year": 690}
    assert t["shop"]["extra_location"] == {"month": 49, "year": 490}
    assert t["shop"]["extra_seat"] == {"month": 6, "year": 60}
    assert t["pro"]["base"] == {"month": 129, "year": 1290}
    assert t["pro"]["extra_location"] == {"month": 79, "year": 790}
    assert t["pro"]["extra_seat"] == {"month": 6, "year": 60}


def _site_number(block: str, key: str):
    import re
    m = re.search(rf"{key}: (\d+)", block)
    return int(m.group(1)) if m else None


def test_plan_numbers_match_the_website():
    """website/site.config.js is the published price list; billing must charge the same."""
    import pathlib
    import re
    cfg = (pathlib.Path(__file__).resolve().parents[2] / "website" / "site.config.js").read_text()
    for plan in billing.PLANS:
        block = cfg[cfg.index(f'id: "{plan}"'):]
        block = block[:block.index("features:")]
        def num(k, block=block):
            return _site_number(block, k)
        assert num("monthly") == billing.MONTHLY_PRICE[plan]["month"], plan
        assert num("extraLocation") == billing.MONTHLY_PRICE[plan].get("extra_location"), plan
        assert num("extraSeat") == billing.MONTHLY_PRICE[plan].get("extra_seat"), plan
        members = int(re.search(r'seats: "(\d+) team members', block).group(1))
        assert members == billing.PLAN_LIMITS[plan]["team_members"], plan


# ── Checkout, plan change, portal ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_checkout_session_is_built_for_this_shop(client, db, stripe_cfg):
    hdr, org_id = await _signup(client)
    r = await client.post("/api/billing/checkout", json={"plan": "shop", "period": "month"}, headers=hdr)
    assert r.status_code == 200, r.text
    _, path, data = stripe_cfg[-1]
    assert path == "/checkout/sessions" and data["mode"] == "subscription"
    assert data["line_items[0][price]"] == "price_sm" and data["line_items[0][quantity]"] == "1"
    assert data["client_reference_id"] == org_id == data["subscription_data[metadata][org_id]"]
    assert data["success_url"].startswith("https://app.tekly.test/settings?section=billing")
    assert data["customer_email"].endswith("@test.dev")
    # Stripe Tax with the business's tax ID; local currency comes from Adaptive Pricing (Dashboard)
    assert data["automatic_tax[enabled]"] == "true" and data["tax_id_collection[enabled]"] == "true"
    assert data["billing_address_collection"] == "required" and "customer_update[address]" not in data
    await _set(db, org_id, stripe_customer_id="cus_7")
    await client.post("/api/billing/checkout", json={"plan": "solo", "period": "year"}, headers=hdr)
    data = stripe_cfg[-1][2]
    assert data["customer"] == "cus_7" and data["line_items[0][price]"] == "price_oy"
    assert data["customer_update[address]"] == "auto" and data["customer_update[name]"] == "auto"


@pytest.mark.asyncio
async def test_checkout_rejects_bad_input_and_outgrown_plans(client, db, stripe_cfg):
    hdr, org_id = await _signup(client)
    assert (await client.post("/api/billing/checkout", json={"plan": "starter"}, headers=hdr)).status_code == 422
    for i in range(2):
        db.add(Employee(org_id=org_id, name=f"E{i}", email=f"e{i}-{org_id[:6]}@x.dev", password_hash="x"))
    db.add(Location(org_id=org_id, name="Second"))
    await db.commit()
    r = await client.post("/api/billing/checkout", json={"plan": "solo", "period": "month"}, headers=hdr)
    assert r.status_code == 409 and "team members" in r.json()["detail"] and "location" in r.json()["detail"]
    for plan in ("shop", "pro"):                       # per-location plans bill both locations
        r = await client.post("/api/billing/checkout", json={"plan": plan, "period": "month"}, headers=hdr)
        assert r.status_code == 200 and stripe_cfg[-1][2]["line_items[0][quantity]"] == "2"
        assert "line_items[1][price]" not in stripe_cfg[-1][2]          # 3 people: no extra seats
    await _set(db, org_id, sub_status="active", stripe_subscription_id="sub_1")
    assert (await client.post("/api/billing/checkout", json={"plan": "pro"}, headers=hdr)).status_code == 409


@pytest.mark.asyncio
async def test_checkout_and_plan_changes_carry_shop_seats(client, db, stripe_cfg):
    hdr, org_id = await _signup(client)
    for i in range(5):                                   # 6 people: Shop includes 4, Pro 8
        db.add(Employee(org_id=org_id, name=f"E{i}", email=f"e{i}-{org_id[:6]}@x.dev", password_hash="x"))
    await db.commit()
    await client.post("/api/billing/checkout", json={"plan": "shop", "period": "year"}, headers=hdr)
    data = stripe_cfg[-1][2]
    assert data["line_items[1][price]"] == "price_seat_y" and data["line_items[1][quantity]"] == "2"
    # Shop -> Pro drops the seats (6 fit in Pro's 8); Pro -> Shop adds them back
    await _set(db, org_id, sub_status="active", plan="shop", stripe_subscription_id="sub_1",
               stripe_subscription_item_id="si_1", stripe_seat_item_id="si_seat")
    await client.post("/api/billing/change-plan", json={"plan": "pro", "period": "month"}, headers=hdr)
    data = stripe_cfg[-1][2]
    assert data["items[1][id]"] == "si_seat" and data["items[1][deleted]"] == "true"
    assert (await _org(db, org_id)).stripe_seat_item_id is None
    await client.post("/api/billing/change-plan", json={"plan": "shop", "period": "month"}, headers=hdr)
    data = stripe_cfg[-1][2]
    assert data["items[1][price]"] == "price_seat_m" and data["items[1][quantity]"] == "2" and "items[1][id]" not in data
    assert (await _org(db, org_id)).stripe_seat_item_id == "si_seat"
    # 10 people: Pro bills the 2 beyond its 8 on the same seat item
    for i in range(5, 9):
        db.add(Employee(org_id=org_id, name=f"E{i}", email=f"e{i}-{org_id[:6]}@x.dev", password_hash="x"))
    await db.commit()
    await client.post("/api/billing/change-plan", json={"plan": "pro", "period": "month"}, headers=hdr)
    data = stripe_cfg[-1][2]
    assert data["items[1][id]"] == "si_seat" and data["items[1][quantity]"] == "2"
    assert data["items[1][price]"] == "price_seat_m" and "items[1][deleted]" not in data


@pytest.mark.asyncio
async def test_billing_unconfigured_says_so(client, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_BILLING_SECRET_KEY", "")
    hdr, _ = await _signup(client)
    body = (await client.get("/api/billing", headers=hdr)).json()
    assert body["configured"] is False and [p["id"] for p in body["plans"]] == ["solo", "shop", "pro"]
    r = await client.post("/api/billing/checkout", json={"plan": "pro"}, headers=hdr)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_change_plan_and_portal(client, db, stripe_cfg):
    hdr, org_id = await _signup(client)
    assert (await client.post("/api/billing/portal", headers=hdr)).status_code == 409
    await _set(db, org_id, sub_status="active", plan="shop", stripe_customer_id="cus_1",
               stripe_subscription_id="sub_1", stripe_subscription_item_id="si_1")
    r = await client.post("/api/billing/change-plan", json={"plan": "pro", "period": "year"}, headers=hdr)
    assert r.status_code == 200 and r.json()["plan"] == "pro"
    assert stripe_cfg[-1][2]["items[0][price]"] == "price_py"
    org = await _org(db, org_id)
    assert (org.plan, org.billing_period, org.stripe_price_id) == ("pro", "year", "price_py")
    r = await client.post("/api/billing/portal", headers=hdr)
    assert r.json()["url"] == "https://billing.stripe.test/p_1"
    assert stripe_cfg[-1][2]["customer"] == "cus_1"


# ── Custom pricing (India, large deals) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_custom_shop_never_reaches_stripe(client, db, stripe_cfg):
    hdr, org_id = await _signup(client)
    await _set(db, org_id, billing_mode="custom", sub_status="active", plan="shop", stripe_customer_id="cus_c",
               billing_period_end=date.today() + timedelta(days=30))
    for path, body in (("/api/billing/checkout", {"plan": "pro"}), ("/api/billing/change-plan", {"plan": "pro"}),
                       ("/api/billing/portal", None)):
        r = await client.post(path, json=body, headers=hdr)
        assert r.status_code == 409 and "managed by Tekly" in r.json()["detail"], path
    assert stripe_cfg == []
    b = (await client.get("/api/billing", headers=hdr)).json()
    assert b["billing_mode"] == "custom" and b["limits"]["team_members"] == 4 and not b["read_only"]
    # plan limits and features apply exactly as for Stripe shops
    assert (await client.get("/api/payroll/runs", headers=hdr)).status_code == 403
    assert (await client.get("/api/timeclock/active", headers=hdr)).status_code == 200


@pytest.mark.asyncio
async def test_custom_term_end_makes_the_shop_read_only(client, db):
    hdr, org_id = await _signup(client)
    await _set(db, org_id, billing_mode="custom", sub_status="active", plan="pro",
               billing_period_end=date.today() - timedelta(days=1))
    r = await client.post("/api/clients", json={"name": "Late"}, headers=hdr)
    assert r.status_code == 402 and "term has ended" in r.json()["detail"]
    assert (await client.get("/api/clients", headers=hdr)).status_code == 200


@pytest.mark.asyncio
async def test_admin_sets_custom_billing_and_price_lock(client, db, stripe_cfg):
    from backend.tests.test_build144ce_platform_admin import _admin_hdr
    ahdr = await _admin_hdr(client)
    hdr, org_id = await _signup(client)
    lock = (date.today() + timedelta(days=730)).isoformat()
    r = await client.patch(f"/api/admin/tenants/{org_id}", headers=ahdr, json={
        "billing_mode": "custom", "sub_status": "active",
        "billing_period_end": "2027-03-31", "billing_note": "INR 2,500/month, invoiced quarterly",
        "price_locked_until": lock, "reason": "Custom deal agreed"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/admin/tenants/{org_id}/plan", headers=ahdr,
                          json={"plan": "shop", "reason": "Custom deal agreed"})
    assert r.status_code == 200 and r.json()["status"] == "applied", r.text
    t = (await client.get(f"/api/admin/tenants/{org_id}", headers=ahdr)).json()
    assert (t["billing_mode"], t["plan"], t["billing_period_end"], t["price_locked_until"]) == \
        ("custom", "shop", "2027-03-31", lock)
    assert t["billing_note"].startswith("INR")
    me = (await client.get("/api/auth/me", headers=hdr)).json()["billing"]
    assert me["billing_mode"] == "custom" and me["price_locked_until"] == lock
    assert "billing_note" not in (await client.get("/api/billing", headers=hdr)).json()   # internal
    assert (await client.patch(f"/api/admin/tenants/{org_id}", headers=ahdr,
                               json={"billing_mode": "barter", "reason": "typo"})).status_code == 422
    for plan in ("starter", "trial"):   # a trial is a status, not a plan
        assert (await client.post(f"/api/admin/tenants/{org_id}/plan", headers=ahdr,
                                  json={"plan": plan, "reason": "typo"})).status_code == 422
    # a shop paying through Stripe can't be flipped to custom underneath its subscription
    await _set(db, org_id, billing_mode="stripe", stripe_subscription_id="sub_live")
    r = await client.patch(f"/api/admin/tenants/{org_id}", headers=ahdr,
                           json={"billing_mode": "custom", "reason": "Custom deal agreed"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_only_billing_managers_see_billing(client, db):
    hdr, org_id = await _signup(client)
    from backend.models import Role
    role = Role(org_id=org_id, name="tech", permissions={"can_manage_settings": False}, perms_explicit=True)
    db.add(role)
    await db.flush()
    from backend.services.auth import create_access_token, hash_password
    emp = Employee(org_id=org_id, role_id=role.id, name="Tech", email=f"tech-{org_id[:6]}@x.dev",
                   password_hash=hash_password("pw-test-12345"))
    db.add(emp)
    await db.commit()
    thdr = {"Authorization": f"Bearer {create_access_token({'sub': emp.id, 'org_id': org_id, 'tv': 0})}"}
    assert (await client.get("/api/billing", headers=thdr)).status_code == 403
    assert (await client.post("/api/billing/checkout", json={"plan": "pro"}, headers=thdr)).status_code == 403
    assert "billing" in (await client.get("/api/auth/me", headers=thdr)).json()


# ── Webhook: signatures ──────────────────────────────────────────────────────

def _sign(payload: bytes, secret=WHSEC, ts=None) -> str:
    ts = int(ts if ts is not None else time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _event(etype, obj, eid=None, created=None):
    return {"id": eid or f"evt_{uuid.uuid4().hex[:12]}", "type": etype,
            "created": created or int(time.time()), "data": {"object": obj}}


async def _post(client, event, sig=True, **kw):
    payload = json.dumps(event).encode()
    headers = {"Content-Type": "application/json"}
    if sig:
        headers["Stripe-Signature"] = _sign(payload, **kw)
    return await client.post("/api/billing/webhook", content=payload, headers=headers)


@pytest.mark.asyncio
async def test_webhook_rejects_missing_wrong_and_stale_signatures(client, db, stripe_cfg):
    _, org_id = await _signup(client)
    ev = _event("customer.subscription.deleted", {"object": "subscription", "id": "sub_x",
                                                  "metadata": {"org_id": org_id}})
    assert (await _post(client, ev, sig=False)).status_code == 400
    assert (await _post(client, ev, secret="whsec_wrong")).status_code == 400
    assert (await _post(client, ev, ts=time.time() - 3600)).status_code == 400
    assert (await db.execute(select(func.count()).select_from(BillingEvent))).scalar() == 0
    assert (await _org(db, org_id)).sub_status == "trial"
    ok = await _post(client, ev)
    assert ok.status_code == 200 and (await _org(db, org_id)).sub_status == "churned"


@pytest.mark.asyncio
async def test_webhook_fails_closed_without_a_secret_when_deployed(client, monkeypatch):
    from backend.core import webhook_security
    monkeypatch.setattr(settings, "STRIPE_BILLING_WEBHOOK_SECRET", "")
    monkeypatch.setattr(webhook_security, "unsigned_webhooks_allowed", lambda: False)
    r = await _post(client, _event("invoice.payment_failed", {"customer": "cus_1"}), sig=False)
    assert r.status_code == 400 and "not configured" in r.json()["detail"]


@pytest.mark.asyncio
async def test_webhook_accepts_any_of_several_v1_signatures(client, db, stripe_cfg):
    _, org_id = await _signup(client)
    ev = _event("customer.subscription.deleted", {"object": "subscription", "metadata": {"org_id": org_id}})
    payload = json.dumps(ev).encode()
    good = _sign(payload)
    header = f"{good.split(',')[0]},v1={'0' * 64},{good.split(',')[1]}"
    r = await client.post("/api/billing/webhook", content=payload, headers={"Stripe-Signature": header})
    assert r.status_code == 200


# ── Webhook: effects, idempotency, ordering ──────────────────────────────────

@pytest.mark.asyncio
async def test_checkout_completed_activates_the_shop(client, db, stripe_cfg):
    hdr, org_id = await _signup(client)
    await _set(db, org_id, trial_ends_at=date.today() - timedelta(days=1))   # trial already over
    ev = _event("checkout.session.completed", {
        "object": "checkout.session", "mode": "subscription", "status": "complete",
        "payment_status": "paid", "client_reference_id": org_id, "customer": "cus_9",
        "subscription": "sub_9", "metadata": {"org_id": org_id, "plan": "solo", "period": "year"}})
    assert (await _post(client, ev)).json() == {"ok": True}
    org = await _org(db, org_id)
    assert (org.sub_status, org.plan, org.billing_period) == ("active", "solo", "year")
    assert (org.stripe_customer_id, org.stripe_subscription_id) == ("cus_9", "sub_9")
    assert (await client.post("/api/clients", json={"name": "Writable"}, headers=hdr)).status_code == 201


@pytest.mark.asyncio
async def test_subscription_updated_maps_price_status_and_period_end(client, db, stripe_cfg):
    _, org_id = await _signup(client)
    await _set(db, org_id, stripe_customer_id="cus_2")
    end = int(time.time()) + 30 * 86400
    sub = {"object": "subscription", "id": "sub_2", "customer": "cus_2", "status": "past_due",
           "cancel_at_period_end": True, "current_period_end": end, "metadata": {},
           "items": {"data": [{"id": "si_2", "price": {"id": "price_py"}}]}}
    await _post(client, _event("customer.subscription.updated", sub))
    org = await _org(db, org_id)
    assert (org.sub_status, org.plan, org.billing_period) == ("past_due", "pro", "year")
    assert org.past_due_since == date.today() and org.cancel_at_period_end is True
    assert org.stripe_subscription_item_id == "si_2" and org.billing_period_end is not None
    await _post(client, _event("customer.subscription.updated", {**sub, "status": "active"}))
    org = await _org(db, org_id)
    assert org.sub_status == "active" and org.past_due_since is None


@pytest.mark.asyncio
async def test_webhook_tells_the_seat_item_from_the_plan_item(client, db, stripe_cfg):
    _, org_id = await _signup(client)
    sub = {"object": "subscription", "id": "sub_s", "status": "active", "metadata": {"org_id": org_id},
           "items": {"data": [
               # seat first, and on an old seat price that's no longer in config (kind=seat metadata)
               {"id": "si_seat_9", "price": {"id": "price_seat_legacy", "metadata": {"kind": "seat"}}},
               {"id": "si_plan_9", "price": {"id": "price_sm"}}]}}
    await _post(client, _event("customer.subscription.updated", sub))
    org = await _org(db, org_id)
    assert (org.plan, org.stripe_subscription_item_id, org.stripe_seat_item_id) == ("shop", "si_plan_9", "si_seat_9")
    sub["items"]["data"] = sub["items"]["data"][1:]          # seats removed in the portal or by a downgrade
    await _post(client, _event("customer.subscription.updated", sub))
    assert (await _org(db, org_id)).stripe_seat_item_id is None


@pytest.mark.asyncio
async def test_payment_failed_starts_grace_and_finds_shop_by_customer(client, db, stripe_cfg):
    _, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", stripe_customer_id="cus_3", stripe_subscription_id="sub_3")
    await _post(client, _event("invoice.payment_failed", {"object": "invoice", "customer": "cus_3",
                                                          "subscription": "sub_3"}))
    org = await _org(db, org_id)
    assert org.sub_status == "past_due" and org.past_due_since == date.today()
    assert not billing.state(org).read_only


@pytest.mark.asyncio
async def test_same_event_is_applied_once(client, db, stripe_cfg):
    _, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", stripe_subscription_id="sub_4")
    ev = _event("customer.subscription.deleted", {"object": "subscription", "id": "sub_4",
                                                  "metadata": {"org_id": org_id}}, eid="evt_once")
    assert (await _post(client, ev)).json() == {"ok": True}
    assert (await _org(db, org_id)).sub_status == "churned"
    await _set(db, org_id, sub_status="active")      # e.g. support re-activated the shop
    again = await _post(client, ev)                   # Stripe redelivers the old event
    assert again.status_code == 200 and again.json().get("duplicate") is True
    assert (await _org(db, org_id)).sub_status == "active"
    rows = (await db.execute(select(BillingEvent).where(BillingEvent.id == "evt_once"))).scalars().all()
    assert len(rows) == 1 and rows[0].org_id == org_id


@pytest.mark.asyncio
async def test_event_id_is_unique_in_the_database(db):
    from sqlalchemy.exc import IntegrityError
    db.add(BillingEvent(id="evt_dup", type="invoice.payment_failed"))
    await db.commit()
    db.add(BillingEvent(id="evt_dup", type="invoice.payment_failed"))
    with pytest.raises(IntegrityError):
        await db.commit()


@pytest.mark.asyncio
async def test_an_older_event_cannot_undo_a_newer_one(client, db, stripe_cfg):
    _, org_id = await _signup(client)
    base = {"object": "subscription", "id": "sub_5", "metadata": {"org_id": org_id},
            "items": {"data": [{"id": "si_5", "price": {"id": "price_pm"}}]}}
    now = int(time.time())
    await _post(client, _event("customer.subscription.updated", {**base, "status": "active"}, created=now))
    await _post(client, _event("customer.subscription.updated", {**base, "status": "unpaid"}, created=now - 60))
    assert (await _org(db, org_id)).sub_status == "active"


@pytest.mark.asyncio
async def test_deleting_an_old_subscription_does_not_cancel_the_new_one(client, db, stripe_cfg):
    _, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", stripe_subscription_id="sub_new", stripe_customer_id="cus_6")
    await _post(client, _event("customer.subscription.deleted", {"object": "subscription", "id": "sub_old",
                                                                "customer": "cus_6"}))
    assert (await _org(db, org_id)).sub_status == "active"


@pytest.mark.asyncio
async def test_event_for_another_customer_is_ignored(client, db, stripe_cfg):
    _, org_id = await _signup(client)
    await _set(db, org_id, sub_status="active", stripe_customer_id="cus_mine")
    r = await _post(client, _event("customer.subscription.deleted", {
        "object": "subscription", "id": "sub_z", "customer": "cus_other", "metadata": {"org_id": org_id}}))
    assert r.json()["ignored"] == "customer mismatch"
    assert (await _org(db, org_id)).sub_status == "active"


@pytest.mark.asyncio
async def test_unhandled_and_unmatched_events(client, db, stripe_cfg):
    r = await _post(client, _event("charge.refunded", {}))
    assert r.json()["ignored"] == "charge.refunded"
    r = await _post(client, _event("invoice.payment_failed", {"customer": "cus_nobody"}))
    assert r.json()["unmatched"] is True


# ── Config ───────────────────────────────────────────────────────────────────

def _deployed(env, **kw):
    from cryptography.fernet import Fernet
    base = dict(ENVIRONMENT=env, SECRET_KEY="s" * 48, CORS_ORIGINS="https://app.example.com",
                TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                DATABASE_URL="postgresql+asyncpg://u@db/tekly", PUBLIC_BASE_URL="https://app.example.com",
                SMTP_HOST="smtp.example.com", SMTP_FROM="no-reply@example.com")
    return Settings(_env_file=None, **{**base, **kw})


FULL = dict(STRIPE_BILLING_SECRET_KEY="sk_test_x", STRIPE_BILLING_WEBHOOK_SECRET="whsec_x",
            PUBLIC_BASE_URL="https://app.example.com", **PRICES)


def test_production_requires_billing():
    with pytest.raises(ValueError, match="STRIPE_BILLING_SECRET_KEY"):
        _deployed("production")
    assert _deployed("production", **FULL).billing_prices[("pro", "year")] == "price_py"


@pytest.mark.parametrize("missing", ["STRIPE_BILLING_WEBHOOK_SECRET", "STRIPE_BILLING_PRICE_PRO_YEAR",
                                     "STRIPE_BILLING_PRICE_SHOP_SEAT_MONTH",
                                     "PUBLIC_BASE_URL"])
def test_partial_billing_config_is_refused_when_deployed(missing):
    with pytest.raises(ValueError, match=missing):
        _deployed("staging", **{**FULL, missing: ""})


def test_staging_refuses_a_live_key():
    with pytest.raises(ValueError, match="test-mode"):
        _deployed("staging", **{**FULL, "STRIPE_BILLING_SECRET_KEY": "sk_live_x"})
    assert _deployed("staging").is_deployed   # billing optional on staging


# ── Admin portal sees the lifecycle ─────────────────────────────────────────

def test_admin_segments_follow_billing_states():
    from backend.routers.admin import _segment
    assert _segment(Organisation(sub_status="past_due")) == "paid"
    assert _segment(Organisation(sub_status="unpaid")) == "lost_paid"
    assert _segment(Organisation(sub_status="trial", trial_ends_at=date.today() - timedelta(days=1))) == "lost_trial"


async def test_trial_suggests_pro_only_when_the_shop_uses_a_pro_feature(client, db):
    hdr, org_id = await _signup(client)
    assert await billing.pro_features_in_use(db, org_id) is False
    me = (await client.get("/api/auth/me", headers=hdr)).json()["billing"]
    assert me["suggested_plan"] == "solo"
    # API keys are Pro-only; the trial unlocks them, and using one means the shop needs Pro
    r = await client.post("/api/api-keys", json={"name": "Zapier"}, headers=hdr)
    assert r.status_code == 201, r.text
    assert await billing.pro_features_in_use(db, org_id) is True
    me = (await client.get("/api/auth/me", headers=hdr)).json()["billing"]
    assert me["suggested_plan"] == "pro"
