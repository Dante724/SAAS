"""Launch blockers from docs/LAUNCH_READINESS_2026-10-19.md.

- C1: the new-order drawer sends lines with only `kind` (and catalog `ref_id`); creating an
  order with items used to fail with "Field required" because the API wanted `line_type`.
- C2: a new shop's first page load fires parallel requests that each seeded the status table;
  the duplicates made every later estimate a 500.
- C3: purchase-order numbers were unique across the whole platform, so a second shop's first
  PO collided with the first shop's PO-00001.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import func, select


async def _tenant(client):
    email = f"lb-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "LB Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["org_id"]


# ── C1: order with items from the drawer ─────────────────────────────

@pytest.mark.asyncio
async def test_drawer_payload_without_line_type_creates_lines(client):
    H, _ = await _tenant(client)
    svc = (await client.post("/api/catalog/services", json={"name": "Screen swap", "price": 120},
                             headers=H)).json()
    cid = (await client.post("/api/clients", json={"name": "Neha"}, headers=H)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "complaint": "cracked", "lines": [
        {"description": "Screen swap", "kind": "service", "ref_id": svc["id"], "qty": 1, "unit_price": 120},
        {"description": "Custom line", "kind": "service", "qty": 1, "unit_price": 15},
    ]}, headers=H)
    assert r.status_code == 201, r.text
    lines = r.json()["lines"]
    assert sorted(ln["line_type"] for ln in lines) == ["labour", "labour"]
    assert [ln for ln in lines if ln["description"] == "Screen swap"][0]["ref_id"] == svc["id"]
    assert r.json()["total"] == 135


@pytest.mark.asyncio
async def test_product_kind_becomes_a_part_line(client):
    H, _ = await _tenant(client)
    cid = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "lines": [
        {"description": "Cable", "kind": "product", "qty": 1, "unit_price": 9}]}, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["lines"][0]["line_type"] == "part"


@pytest.mark.asyncio
async def test_explicit_line_type_still_wins(client):
    H, _ = await _tenant(client)
    cid = (await client.post("/api/clients", json={"name": "Asha"}, headers=H)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "lines": [
        {"description": "Call-out", "line_type": "fee", "qty": 1, "unit_price": 20}]}, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["lines"][0]["line_type"] == "fee"


# ── C2: parallel first load must not duplicate statuses ──────────────

@pytest.mark.asyncio
async def test_parallel_first_load_seeds_statuses_once(client, db):
    from backend.models import OrderStatus
    H, org = await _tenant(client)
    rs = await asyncio.gather(*[client.get("/api/statuses", headers=H) for _ in range(10)],
                              *[client.get("/api/statuses?is_estimate=true", headers=H) for _ in range(4)])
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs if r.status_code != 200]
    dupes = (await db.execute(
        select(OrderStatus.record_type, OrderStatus.key, func.count())
        .where(OrderStatus.org_id == org)
        .group_by(OrderStatus.record_type, OrderStatus.key).having(func.count() > 1))).all()
    assert dupes == []
    cid = (await client.post("/api/clients", json={"name": "Est"}, headers=H)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "is_estimate": True}, headers=H)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_new_status_with_a_taken_key_gets_a_free_one(client):
    H, _ = await _tenant(client)
    await client.get("/api/statuses", headers=H)
    a = await client.post("/api/statuses", json={"name": "Waiting", "status_group": "pending", "key": "new"}, headers=H)
    assert a.status_code in (200, 201), a.text
    assert a.json()["key"] == "new_2"


def test_migration_collapses_duplicate_statuses(tmp_path):
    """ls144r1 on a database that already has the duplicates: one row per key is kept and
    transitions that pointed at a removed copy follow the row kept."""
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    path = tmp_path / "dupes.db"
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=f"sqlite+aiosqlite:///{path}")
    env.pop("TEST_DATABASE_URL", None)

    def alembic(target):
        r = subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
                           cwd=repo, env=env, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-3000:]

    alembic("vd144q4")
    con = sqlite3.connect(path)

    def insert(table, **vals):
        # Fill every NOT NULL column without a default, so the test survives schema growth.
        for _, col, typ, notnull, dflt, pk in con.execute(f"PRAGMA table_info({table})").fetchall():
            if notnull and dflt is None and not pk and col not in vals:
                vals[col] = 0 if any(t in typ.upper() for t in ("INT", "BOOL", "NUM", "FLOAT")) else ""
        con.execute(f"INSERT INTO {table} ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                    list(vals.values()))

    for sid, active in (("keep", 1), ("copy1", 1), ("copy2", 0)):
        insert("order_statuses", id=sid, org_id="org1", key="new", name="New", status_group="new",
               record_type="work_order", active=active, sort_order=0)
    insert("order_statuses", id="other", org_id="org1", key="done", name="Done", status_group="closed",
           record_type="work_order", active=1, sort_order=1)
    insert("status_transitions", id="t1", org_id="org1", from_status_id="copy2", to_status_id="other")
    con.commit()
    con.close()

    alembic("head")
    con = sqlite3.connect(path)
    rows = con.execute("SELECT id FROM order_statuses WHERE org_id = 'org1' AND key = 'new'").fetchall()
    assert len(rows) == 1
    kept = rows[0][0]
    assert con.execute("SELECT from_status_id FROM status_transitions WHERE id = 't1'").fetchone()[0] == kept
    with pytest.raises(sqlite3.IntegrityError):
        insert("order_statuses", id="again", org_id="org1", key="new", name="New", status_group="new",
               record_type="work_order", active=1, sort_order=0)
    con.close()


# ── C3: PO numbers per shop ──────────────────────────────────────────

async def _first_po(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    loc = (d if isinstance(d, list) else d["items"])[0]["id"]
    sup = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=H)).json()
    part = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": f"SKU-{uuid.uuid4().hex[:6]}"}, headers=H)).json()
    return await client.post("/api/inventory/po", json={
        "supplier_id": sup["id"], "location_id": loc,
        "lines": [{"part_id": part["id"], "qty_ordered": 5, "unit_cost": 10}]}, headers=H)


@pytest.mark.asyncio
async def test_two_shops_each_get_their_own_first_po_number(client, db):
    from backend.models import PurchaseOrder
    numbers = []
    for _ in range(2):
        H, org = await _tenant(client)
        r = await _first_po(client, H)
        assert r.status_code == 201, r.text
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == r.json()["po_id"]))).scalar_one()
        assert po.org_id == org
        numbers.append(po.po_number)
    assert numbers[0] == numbers[1]          # both shops start at the same first number


# ── C4: no inline script outside the platform-admin page ─────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/tech", "/pay/some-intent", "/offline.html"])
async def test_csp_blocks_inline_script(client, path):
    r = await client.get(path)
    csp = r.headers.get("content-security-policy", "")
    script_src = next(p for p in csp.split(";") if p.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src, (path, csp)


@pytest.mark.asyncio
async def test_admin_page_keeps_its_own_inline_policy(client):
    r = await client.get("/admin")
    assert "script-src 'self' 'unsafe-inline'" in r.headers.get("content-security-policy", "")


@pytest.mark.asyncio
async def test_pay_page_loads_its_script_from_a_file(client):
    page = (await client.get("/pay/some-intent")).text
    assert '<script src="/static/js/pay.js"></script>' in page and "<script>" not in page
    assert (await client.get("/static/js/pay.js")).status_code == 200


# ── C7: pay links can't charge a stale amount or be paid twice ───────

async def _owing_order(client, H, total=100):
    cid = (await client.post("/api/clients", json={"name": "Payer", "phone": "+1"}, headers=H)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "lines": [
        {"description": "Fix", "line_type": "labour", "qty": 1, "unit_price": total}]}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _till(client, H):
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    return [a for a in (await client.get("/api/cash/accounts", headers=H)).json()["items"] if a["name"] == "Till"][0]["id"]


@pytest.mark.asyncio
async def test_pay_link_follows_the_balance_after_a_part_payment(client, db):
    from backend.models import PaymentIntent
    H, _ = await _tenant(client)
    oid = await _owing_order(client, H, 100)
    first = (await client.post(f"/api/orders/{oid}/payment-link", headers=H)).json()
    assert first["amount"] == 100
    again = (await client.post(f"/api/orders/{oid}/payment-link", headers=H)).json()
    assert again["reused"] and again["intent_id"] == first["intent_id"]      # unchanged balance: same link
    r = await client.post(f"/api/cash/orders/{oid}/pay",
                          json={"method": "cash", "account_id": await _till(client, H), "amount": 40}, headers=H)
    assert r.status_code == 200, r.text
    fresh = (await client.post(f"/api/orders/{oid}/payment-link", headers=H)).json()
    assert not fresh["reused"] and fresh["amount"] == 60
    old = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == first["intent_id"]))).scalar_one()
    assert old.status == "canceled"


@pytest.mark.asyncio
async def test_revoking_a_link_closes_it_at_the_provider(client, db, monkeypatch):
    from backend.models import PaymentIntent
    from backend.services import order_money
    H, org = await _tenant(client)
    oid = await _owing_order(client, H)
    closed = []

    async def fake_expire(_db, intent):
        closed.append(intent.id)
        return True
    monkeypatch.setattr(order_money, "_expire_stripe_checkout", fake_expire)
    i = PaymentIntent(org_id=org, order_id=oid, provider="stripe", provider_ref="cs_test_1",
                      amount=100, currency="usd", status="created")
    db.add(i)
    await db.commit()
    r = await client.post(f"/api/payments/intents/{i.id}/revoke", headers=H)
    assert r.status_code == 200, r.text
    assert closed == [i.id]


@pytest.mark.asyncio
async def test_payment_on_a_revoked_link_is_recorded_and_flagged(client, db):
    from backend.models import EventFeedItem, PaymentIntent
    from backend.services import payments as pay
    H, org = await _tenant(client)
    oid = await _owing_order(client, H)
    i = PaymentIntent(org_id=org, order_id=oid, provider="simulated", provider_ref="sim_1",
                      amount=100, currency="usd", status="canceled")
    db.add(i)
    await db.commit()
    await pay.mark_succeeded(db, i)
    assert i.status == "succeeded" and i.payment_id
    notes = (await db.execute(select(EventFeedItem.body).where(
        EventFeedItem.org_id == org, EventFeedItem.entity_id == oid))).scalars().all()
    assert any("cancelled payment link" in (n or "") for n in notes), notes


@pytest.mark.asyncio
async def test_staff_checkout_cannot_ask_for_more_than_is_owed(client):
    H, _ = await _tenant(client)
    oid = await _owing_order(client, H, 100)
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 1000}, headers=H)
    assert r.status_code == 422, r.text
    r = await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 100}, headers=H)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_deployed_shop_without_a_provider_gets_a_clear_error(client, monkeypatch):
    from backend.config import settings
    H, _ = await _tenant(client)
    oid = await _owing_order(client, H)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    r = await client.post(f"/api/orders/{oid}/payment-link", headers=H)
    assert r.status_code == 502
    assert "No payment provider is set up" in r.text


# ── C12: rate limits count API calls per user, not page assets per IP ─

@pytest.mark.asyncio
async def test_page_assets_never_use_up_the_api_budget(client, monkeypatch):
    from backend.services import ratelimit
    H, _ = await _tenant(client)
    calls = []
    real = ratelimit.aenforce

    async def spy(bucket, key, limit, window):
        if bucket == "global":
            calls.append(key)
        return await real(bucket, key, limit, window)
    monkeypatch.setattr(ratelimit, "aenforce", spy)
    for path in ("/tech", "/static/js/core.js", "/offline.html", "/"):
        await client.get(path)
    assert calls == []                                   # nothing outside /api/ is counted
    await client.get("/api/auth/me", headers=H)
    await client.get("/api/auth/me")                     # anonymous
    assert calls[0].startswith("u:") and calls[1].startswith("ip:")   # per user when signed in


@pytest.mark.asyncio
async def test_two_staff_on_one_ip_have_separate_budgets(client):
    from backend.core.observability import _rate_key
    a, _ = await _tenant(client)
    b, _ = await _tenant(client)

    class Req:
        def __init__(self, h):
            self.headers = {"authorization": h["Authorization"]}
            self.client = type("C", (), {"host": "10.0.0.1"})()
    assert _rate_key(Req(a)) != _rate_key(Req(b))


@pytest.mark.asyncio
async def test_signup_is_rate_limited_per_ip(client):
    codes = []
    for i in range(12):
        r = await client.post("/api/auth/register", json={
            "org_name": f"S{i}", "name": "O", "email": f"rl-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
        codes.append(r.status_code)
    assert codes[:10] == [201] * 10 and codes[-1] == 429


@pytest.mark.asyncio
async def test_ai_calls_have_a_per_user_budget_and_input_cap(client):
    H, _ = await _tenant(client)
    r = await client.post("/api/ai/estimate/draft", json={"complaint": "x" * 5000}, headers=H)
    assert r.status_code == 422
    codes = [(await client.post("/api/ai/estimate/draft", json={"complaint": "screen"}, headers=H)).status_code
             for _ in range(21)]
    assert codes[-1] == 429 and 429 not in codes[:19]


# ── C10: sign-up records acceptance of the terms ─────────────────────

@pytest.mark.asyncio
async def test_signup_records_terms_acceptance(client, db):
    from backend.models import AuditLog
    r = await client.post("/api/auth/register", json={
        "org_name": "T Co", "name": "O", "email": f"t-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345",
        "accept_terms": True, "terms_version": "2026-10-19"})
    assert r.status_code == 201, r.text
    row = (await db.execute(select(AuditLog).where(
        AuditLog.org_id == r.json()["org_id"], AuditLog.action == "auth.terms_accepted"))).scalar_one()
    assert (row.diff or {}).get("terms_version") == "2026-10-19"


@pytest.mark.asyncio
async def test_app_pages_stay_out_of_search_engines(client):
    r = await client.get("/robots.txt")
    assert r.status_code == 200 and "Disallow: /" in r.text
    for path in ("/", "/track/abc", "/pay/abc"):
        assert (await client.get(path)).headers.get("x-robots-tag") == "noindex, nofollow"


# ── Auth hardening ───────────────────────────────────────────────────

async def _login(client, email, pw="pw-test-12345"):
    r = await client.post("/api/auth/token", data={"username": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _owner(client):
    email = f"au-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Auth Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()


@pytest.mark.asyncio
async def test_logout_ends_this_session_but_not_other_devices(client):
    email, laptop, _ = await _owner(client)
    phone = await _login(client, email)
    refreshed = {"Authorization": "Bearer " + (await client.post("/api/auth/refresh", headers=laptop)).json()["access_token"]}
    assert (await client.post("/api/auth/logout", headers=laptop)).status_code == 204
    assert (await client.get("/api/auth/me", headers=laptop)).status_code == 401
    assert (await client.get("/api/auth/me", headers=refreshed)).status_code == 401    # same sign-in
    assert (await client.post("/api/auth/refresh", headers=laptop)).status_code == 401
    assert (await client.get("/api/auth/me", headers=phone)).status_code == 200       # other device


@pytest.mark.asyncio
@pytest.mark.parametrize("pw", ["a", "short7!", "changeme123"])
async def test_signup_enforces_the_password_rules(client, pw):
    r = await client.post("/api/auth/register", json={
        "org_name": "P", "name": "O", "email": f"pw-{uuid.uuid4().hex[:8]}@t.dev", "password": pw})
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_email_case_does_not_make_a_second_account(client):
    email, _, _ = await _owner(client)
    r = await client.post("/api/auth/register", json={
        "org_name": "Dup", "name": "O", "email": email.upper(), "password": "pw-test-12345"})
    assert r.status_code == 400
    await _login(client, email.upper())                                     # sign-in ignores case too


@pytest.mark.asyncio
async def test_changing_your_own_email_or_password_needs_the_current_one(client):
    email, H, body = await _owner(client)
    me = body["employee_id"]
    r = await client.patch(f"/api/staff/employees/{me}", json={"email": f"new-{email}"}, headers=H)
    assert r.status_code == 403
    r = await client.patch(f"/api/staff/employees/{me}", json={"email": f"new-{email}", "current_password": "nope"}, headers=H)
    assert r.status_code == 403
    r = await client.patch(f"/api/staff/employees/{me}", json={"email": f"New-{email}", "current_password": "pw-test-12345"}, headers=H)
    assert r.status_code == 200, r.text
    got = (await client.get("/api/auth/me", headers=H)).json()
    assert got["email"] == f"new-{email}".lower() and got["email_verified"] is False
    r = await client.post(f"/api/staff/employees/{me}/password", json={"password": "pw-next-12345"}, headers=H)
    assert r.status_code == 403
    r = await client.post(f"/api/staff/employees/{me}/password",
                          json={"password": "pw-next-12345", "current_password": "pw-test-12345"}, headers=H)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_inviting_someone_from_another_shop_is_a_clear_409(client):
    a_email, _, _ = await _owner(client)
    _, hb, _ = await _owner(client)
    r = await client.post("/api/staff/invites", json={"email": a_email, "name": "X"}, headers=hb)
    assert r.status_code == 409, r.text


# ── Data hardening ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_closing_an_order_and_its_commissions_save_together(client, db, monkeypatch):
    from backend.models import Order
    from backend.services.payroll import CommissionEngine
    H, _ = await _tenant(client)
    oid = await _owing_order(client, H)

    async def boom(self, order):
        raise RuntimeError("payroll engine down")
    monkeypatch.setattr(CommissionEngine, "accrue_for_order_close", boom)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "closed"}, headers=H)
    assert r.status_code == 500
    status = (await db.execute(select(Order.status).where(Order.id == oid))).scalar_one()
    assert status != "closed"              # nothing half-saved: still open, no commissions owed


@pytest.mark.asyncio
async def test_a_new_shops_first_documents_at_once_get_distinct_numbers(client):
    H, _ = await _tenant(client)
    cid = (await client.post("/api/clients", json={"name": "Par"}, headers=H)).json()["id"]
    rs = await asyncio.gather(*[client.post("/api/orders", json={"client_id": cid}, headers=H) for _ in range(6)])
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs if r.status_code != 201]
    numbers = [r.json()["order_number"] for r in rs]
    assert len(set(numbers)) == 6


@pytest.mark.asyncio
async def test_lists_are_paged(client):
    H, _ = await _tenant(client)
    assert "total" in (await client.get("/api/inventory/po?limit=5", headers=H)).json()
    assert "total" in (await client.get("/api/payments/intents?limit=5", headers=H)).json()
    assert (await client.get("/api/orders/archived?limit=5000", headers=H)).status_code == 422
