"""Admin panel sprint 1: the action log, roles (permission, scope, field levels,
limits, approvals), shop assignment and account actions, and account IDs."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from backend.db.database import AsyncSessionLocal
from backend.models import AdminActionLog, Employee, Organisation, PlatformAdmin
from backend.services import account_id as acct
from backend.services import admin_log
from backend.services.admin_access import effective_discount
from backend.tests.admin_helpers import admin_headers, login, seed_admin


async def _shop(client, name=None, *, status=None, country=None):
    email = f"o-{uuid.uuid4().hex[:8]}@shop.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": name or f"Shop {email[:6]}", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    oid = r.json()["org_id"]
    if status or country:
        async with AsyncSessionLocal() as db:
            org = await db.get(Organisation, oid)
            if status:
                org.sub_status = status
            if country:
                org.country = country
            await db.commit()
    return oid, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _admin_id(email):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(PlatformAdmin.id).where(PlatformAdmin.email == email))).scalar_one()


async def _assign(oid, admin_email):
    aid = await _admin_id(admin_email)
    async with AsyncSessionLocal() as db:
        (await db.get(Organisation, oid)).assigned_admin_id = aid
        await db.commit()


# ── action log ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_actions_are_logged_with_before_after_and_reason_and_the_log_is_append_only(client):
    H, _ = await admin_headers(client)
    oid, _ = await _shop(client)
    r = await client.post(f"/api/admin/tenants/{oid}/discount", json={"pct": 20, "reason": "Pilot shop"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "applied"
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AdminActionLog).where(AdminActionLog.org_id == oid,
                                                             AdminActionLog.action == "discount"))).scalar_one()
        assert row.before["pct"] == 0 and row.after["pct"] == 20 and row.reason == "Pilot shop" and row.admin_email
        assert await admin_log.verify_chain(db)
        rid = row.id
        with pytest.raises(DBAPIError):
            await db.execute(text("UPDATE admin_action_log SET reason='edited' WHERE id=:i"), {"i": rid})
        await db.rollback()
        with pytest.raises(DBAPIError):
            await db.execute(text("DELETE FROM admin_action_log WHERE id=:i"), {"i": rid})
        await db.rollback()


@pytest.mark.asyncio
async def test_a_refused_action_is_logged_and_changes_nothing(client):
    H, email = await admin_headers(client, role="support")
    oid, _ = await _shop(client)
    r = await client.post(f"/api/admin/tenants/{oid}/discount", json={"pct": 5, "reason": "try"}, headers=H)
    assert r.status_code == 403
    async with AsyncSessionLocal() as db:
        assert (await db.get(Organisation, oid)).discount_pct in (0, None)
        refused = (await db.execute(select(AdminActionLog).where(AdminActionLog.admin_email == email,
                                                                 AdminActionLog.outcome == "refused"))).scalars().all()
        assert any(x.action == "give_discount" for x in refused)


# ── scope and field levels ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sales_sees_only_trials_and_no_billing(client):
    H, _ = await admin_headers(client, role="sales")
    trial, _ = await _shop(client, "Trial One")
    paying, _ = await _shop(client, "Paying One", status="active")
    lst = (await client.get("/api/admin/tenants?limit=200", headers=H)).json()["items"]
    ids = {t["id"] for t in lst}
    assert trial in ids and paying not in ids
    assert (await client.get(f"/api/admin/tenants/{paying}", headers=H)).status_code == 404   # looks missing
    d = (await client.get(f"/api/admin/tenants/{trial}", headers=H)).json()
    assert "discount_pct" not in d and "employees_detail" not in d
    assert "owner_email" in d                         # sales may contact trials


@pytest.mark.asyncio
async def test_account_manager_sees_billing_only_for_their_own_book(client):
    H, email = await admin_headers(client, role="sales_am")
    mine, _ = await _shop(client, "Mine", status="active")
    other, _ = await _shop(client, "Other", status="active")
    await _assign(mine, email)
    a = (await client.get(f"/api/admin/tenants/{mine}", headers=H)).json()
    b = (await client.get(f"/api/admin/tenants/{other}", headers=H)).json()
    assert "discount_pct" in a and "owner_email" in a
    assert "discount_pct" not in b and "owner_email" not in b
    assert "mrr" not in (await client.get("/api/admin/stats", headers=H)).json()


@pytest.mark.asyncio
async def test_own_book_scope_hides_other_shops(client):
    H, email = await admin_headers(client, role="account_manager")
    mine, _ = await _shop(client, status="active")
    other, _ = await _shop(client, status="active")
    await _assign(mine, email)
    ids = {t["id"] for t in (await client.get("/api/admin/tenants?limit=200", headers=H)).json()["items"]}
    assert ids == {mine}
    for url in (f"/api/admin/tenants/{other}",):
        assert (await client.get(url, headers=H)).status_code == 404
    r = await client.post(f"/api/admin/tenants/{other}/notes", json={"body": "hi"}, headers=H)
    assert r.status_code == 404


# ── limits and approvals ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_over_the_cap_becomes_a_request_the_owner_approves(client):
    OWN, _ = await admin_headers(client)
    S, sales_email = await admin_headers(client, role="sales_am")
    oid, _ = await _shop(client, status="active")
    await _assign(oid, sales_email)
    until = (date.today() + timedelta(days=60)).isoformat()
    ok = await client.post(f"/api/admin/tenants/{oid}/discount", json={"pct": 15, "until": until, "reason": "Retention"}, headers=S)
    assert ok.json()["status"] == "applied"
    big = await client.post(f"/api/admin/tenants/{oid}/discount", json={"pct": 16, "until": until, "reason": "Retention"}, headers=S)
    assert big.json()["status"] == "requested"
    async with AsyncSessionLocal() as db:
        assert float((await db.get(Organisation, oid)).discount_pct) == 15     # unchanged until approved
    long = await client.post(f"/api/admin/tenants/{oid}/discount", json={"pct": 10, "reason": "No end"}, headers=S)
    assert long.json()["status"] == "requested"                               # sales discounts need an end date
    req = big.json()["request_id"]
    assert (await client.post(f"/api/admin/approvals/{req}/decide", json={"decision": "approve"}, headers=S)).status_code == 403
    d = await client.post(f"/api/admin/approvals/{req}/decide", json={"decision": "approve"}, headers=OWN)
    assert d.status_code == 200 and d.json()["status"] == "approved"
    async with AsyncSessionLocal() as db:
        assert float((await db.get(Organisation, oid)).discount_pct) == 16
    again = await client.post(f"/api/admin/approvals/{req}/decide", json={"decision": "approve"}, headers=OWN)
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_plan_changes_from_account_management_need_the_owner(client):
    OWN, _ = await admin_headers(client)
    S, sales_email = await admin_headers(client, role="sales_am")
    oid, _ = await _shop(client, status="active")
    await _assign(oid, sales_email)
    r = await client.post(f"/api/admin/tenants/{oid}/plan", json={"plan": "pro", "reason": "Wants a third location"}, headers=S)
    assert r.json()["status"] == "requested"
    assert (await client.post(f"/api/admin/tenants/{oid}/plan", json={"plan": "gold", "reason": "x x"}, headers=OWN)).status_code == 422
    ok = await client.post(f"/api/admin/tenants/{oid}/plan", json={"plan": "shop", "reason": "Upgrade"}, headers=OWN)
    assert ok.json()["status"] == "applied"


@pytest.mark.asyncio
async def test_trial_extension_limit(client):
    S, _ = await admin_headers(client, role="sales")
    oid, _ = await _shop(client)
    assert (await client.post(f"/api/admin/tenants/{oid}/extend-trial", json={"days": 7, "reason": "Busy week"}, headers=S)).json()["status"] == "applied"
    assert (await client.post(f"/api/admin/tenants/{oid}/extend-trial", json={"days": 8, "reason": "Busy week"}, headers=S)).json()["status"] == "requested"


def test_an_ended_discount_stops_counting():
    org = Organisation(name="x", discount_pct=20, discount_until=date.today() - timedelta(days=1))
    assert effective_discount(org) == 0
    org.discount_until = date.today()
    assert effective_discount(org) == 20


# ── account actions ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_suspend_shows_the_message_to_the_shop_and_unsuspend_restores(client):
    OWN, _ = await admin_headers(client)
    oid, shop = await _shop(client)
    r = await client.post(f"/api/admin/tenants/{oid}/suspend",
                          json={"reason": "Unpaid", "message": "Payment overdue. Call Tekly on 080 1234."}, headers=OWN)
    assert r.status_code == 200
    blocked = await client.get("/api/auth/me", headers=shop)
    assert blocked.status_code in (401, 403)
    async with AsyncSessionLocal() as db:
        assert (await db.get(Organisation, oid)).suspend_message.startswith("Payment overdue")
    assert (await client.post(f"/api/admin/tenants/{oid}/unsuspend", json={"reason": "Paid"}, headers=OWN)).status_code == 200


@pytest.mark.asyncio
async def test_notes_and_calls_go_on_the_timeline(client):
    OWN, _ = await admin_headers(client)
    oid, _ = await _shop(client)
    await client.post(f"/api/admin/tenants/{oid}/notes", json={"body": "Called, wants a demo", "kind": "call"}, headers=OWN)
    d = (await client.get(f"/api/admin/tenants/{oid}", headers=OWN)).json()
    assert d["notes"][0]["body"] == "Called, wants a demo" and d["last_contacted_at"]
    assert any(h["action"] == "note" for h in d["history"])


@pytest.mark.asyncio
async def test_leaving_admin_must_hand_over_their_book_and_last_owner_stays(client):
    OWN, owner_email = await admin_headers(client)
    S, sales_email = await admin_headers(client, role="sales_am")
    oid, _ = await _shop(client)
    await _assign(oid, sales_email)
    sid, oidr = await _admin_id(sales_email), await _admin_id(owner_email)
    r = await client.post(f"/api/admin/admins/{sid}/deactivate", json={"reason": "Left"}, headers=OWN)
    assert r.status_code == 409
    r = await client.post(f"/api/admin/admins/{sid}/deactivate", json={"reason": "Left", "reassign_to": oidr}, headers=OWN)
    assert r.status_code == 200 and r.json()["shops_moved"] == 1
    assert (await client.get("/api/admin/me", headers=S)).status_code == 401
    async with AsyncSessionLocal() as db:
        assert (await db.get(Organisation, oid)).assigned_admin_id == oidr


@pytest.mark.asyncio
async def test_last_owner_cannot_be_removed_or_demoted(client):
    OWN, owner_email = await admin_headers(client)
    async with AsyncSessionLocal() as db:   # make this the only active owner
        from backend.models import AdminRole
        owner_role = (await db.execute(select(AdminRole.id).where(AdminRole.builtin == "owner"))).scalar_one()
        for a in (await db.execute(select(PlatformAdmin))).scalars():
            if a.email != owner_email and (a.role_id in (None, owner_role)):
                a.active = False
        await db.commit()
    me = await _admin_id(owner_email)
    assert (await client.patch(f"/api/admin/admins/{me}", json={"role": "support", "reason": "test"}, headers=OWN)).status_code == 409
    assert (await client.post(f"/api/admin/admins/{me}/deactivate", json={"reason": "test"}, headers=OWN)).status_code == 409


@pytest.mark.asyncio
async def test_role_change_applies_on_the_next_request(client):
    OWN, _ = await admin_headers(client)
    S, sales_email = await admin_headers(client, role="sales")
    sid = await _admin_id(sales_email)
    assert (await client.patch(f"/api/admin/admins/{sid}", json={"role": "support", "reason": "Moved team"}, headers=OWN)).status_code == 200
    me = await client.get("/api/admin/me", headers=S)          # same session, new rules, no sign-out
    assert me.status_code == 200 and me.json()["role"]["key"] == "support"


@pytest.mark.asyncio
async def test_no_one_can_hand_out_more_access_than_they_have(client):
    OWN, _ = await admin_headers(client)
    roles = (await client.get("/api/admin/roles", headers=OWN)).json()
    # A custom role that can manage admins but not suspend tries to create an owner.
    r = await client.post("/api/admin/roles", json={"name": f"Lead {uuid.uuid4().hex[:4]}", "reason": "team lead",
                                                    "permissions": {"manage_admins": True, "view_shops": True}}, headers=OWN)
    assert r.status_code == 201 and roles["catalog"]["permissions"]
    lead_email = await seed_admin(role=None)
    async with AsyncSessionLocal() as db:
        a = (await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == lead_email))).scalar_one()
        a.role_id = r.json()["id"]
        await db.commit()
    L = await login(client, lead_email)
    bad = await client.post("/api/admin/admins", json={"email": f"x-{uuid.uuid4().hex[:5]}@t.dev", "name": "X",
                                                       "password": "a-strong-password-1", "role": "owner"}, headers=L)
    assert bad.status_code == 403


# ── coupons ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_coupon_codes_follow_paddle_rules_and_duplicates_are_clean_errors(client):
    OWN, _ = await admin_headers(client)
    code = f"LAUNCH{uuid.uuid4().hex[:4].upper()}"
    assert (await client.post("/api/admin/coupons", json={"code": "BAD-CODE", "percent": 10}, headers=OWN)).status_code == 422
    assert (await client.post("/api/admin/coupons", json={"code": code, "percent": 10}, headers=OWN)).status_code == 201
    assert (await client.post("/api/admin/coupons", json={"code": code, "percent": 10}, headers=OWN)).status_code == 400
    S, _ = await admin_headers(client, role="sales")
    big = await client.post("/api/admin/coupons", json={"code": code + "X", "percent": 50,
                                                        "expires_at": (date.today() + timedelta(days=30)).isoformat()}, headers=S)
    assert big.status_code == 403


# ── account IDs ──────────────────────────────────────────────────────────────
def test_account_ids_are_well_formed_and_catch_typos():
    ids = {acct.new_account_id() for _ in range(2000)}
    assert all(acct.normalize(i) == i for i in ids)
    a = next(iter(ids))
    assert acct.normalize(a) == a and acct.normalize(a.lower().replace("-", " ")) == a
    body = a.replace("-", "")
    swapped = body[:3] + body[4] + body[3] + body[5:]
    if swapped != body:
        assert acct.normalize(swapped) is None
    typo = body[:4] + ("A" if body[4] != "A" else "B") + body[5:]
    assert acct.normalize(typo) is None
    assert not any(c in "01OIL" for c in "".join(ids).replace("TK-", "").replace("-", ""))


@pytest.mark.asyncio
async def test_every_shop_gets_a_unique_account_id_that_finds_it(client):
    OWN, _ = await admin_headers(client)
    oid, shop = await _shop(client, "Findable Mobiles")
    async with AsyncSessionLocal() as db:
        aid = (await db.get(Organisation, oid)).account_id
    assert acct.normalize(aid) == aid
    hit = (await client.get(f"/api/admin/tenants?q={aid.lower().replace('-', '')}", headers=OWN)).json()["items"]
    assert [t["id"] for t in hit] == [oid]
    company = await client.get("/api/staff/company", headers=shop)
    assert company.status_code == 200 and company.json()["account_id"] == aid


@pytest.mark.asyncio
async def test_a_clashing_account_id_is_redrawn(client, monkeypatch):
    first, _ = await _shop(client)
    async with AsyncSessionLocal() as db:
        taken = (await db.get(Organisation, first)).account_id
    fresh = acct.new_account_id()
    seq = iter([taken, taken, fresh])
    monkeypatch.setattr(acct, "new_account_id", lambda: next(seq))
    second, _ = await _shop(client)
    async with AsyncSessionLocal() as db:
        assert (await db.get(Organisation, second)).account_id == fresh


# ── review fixes ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_combined_role_sees_trials_and_its_own_paying_shops_only(client):
    S, email = await admin_headers(client, role="sales_am")
    trial, _ = await _shop(client)
    mine, _ = await _shop(client, status="active")
    theirs, _ = await _shop(client, status="active")
    await _assign(mine, email)
    ids = {t["id"] for t in (await client.get("/api/admin/tenants?limit=200", headers=S)).json()["items"]}
    assert {trial, mine} <= ids and theirs not in ids
    assert (await client.post(f"/api/admin/tenants/{theirs}/extend-trial", json={"days": 3, "reason": "try it"}, headers=S)).status_code == 404


@pytest.mark.asyncio
async def test_search_never_confirms_a_hidden_email(client):
    S, _ = await admin_headers(client, role="sales_am")
    oid, _ = await _shop(client)
    async with AsyncSessionLocal() as db:
        org = await db.get(Organisation, oid)
        org.company_email = f"secret-{oid[:6]}@shop.dev"
        await db.commit()
    hits = (await client.get(f"/api/admin/tenants?q=secret-{oid[:6]}", headers=S)).json()["items"]
    assert hits == []                     # not their shop, so its email isn't searchable for them
    OWN, _ = await admin_headers(client)
    assert [t["id"] for t in (await client.get(f"/api/admin/tenants?q=secret-{oid[:6]}", headers=OWN)).json()["items"]] == [oid]


@pytest.mark.asyncio
async def test_a_coupon_does_not_get_round_the_discount_limit(client):
    OWN, _ = await admin_headers(client)
    S, email = await admin_headers(client, role="sales_am")
    oid, _ = await _shop(client, status="active")
    await _assign(oid, email)
    code = f"BIG{uuid.uuid4().hex[:5].upper()}"
    await client.post("/api/admin/coupons", json={"code": code, "percent": 60}, headers=OWN)
    r = await client.post(f"/api/admin/tenants/{oid}/apply-coupon", json={"code": code}, headers=S)
    assert r.json()["status"] == "requested"


@pytest.mark.asyncio
async def test_trial_extension_cap_counts_every_extension(client):
    S, _ = await admin_headers(client, role="sales")
    oid, _ = await _shop(client)
    assert (await client.post(f"/api/admin/tenants/{oid}/extend-trial", json={"days": 5, "reason": "busy"}, headers=S)).json()["status"] == "applied"
    assert (await client.post(f"/api/admin/tenants/{oid}/extend-trial", json={"days": 5, "reason": "busy"}, headers=S)).json()["status"] == "requested"


@pytest.mark.asyncio
async def test_suspend_message_shows_at_sign_in_and_unsuspend_restores_the_old_stage(client):
    OWN, _ = await admin_headers(client)
    email = f"o-{uuid.uuid4().hex[:8]}@shop.dev"
    r = await client.post("/api/auth/register", json={"org_name": "Paying Co", "name": "O", "email": email, "password": "pw-test-12345"})
    oid = r.json()["org_id"]
    async with AsyncSessionLocal() as db:
        (await db.get(Organisation, oid)).sub_status = "active"
        await db.commit()
    assert (await client.post(f"/api/admin/tenants/{oid}/unsuspend", json={"reason": "oops"}, headers=OWN)).status_code == 409
    await client.post(f"/api/admin/tenants/{oid}/suspend", json={"reason": "Unpaid", "message": "Call Tekly on 080 1234"}, headers=OWN)
    login = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    assert login.status_code == 403 and "Call Tekly on 080 1234" in login.text
    assert (await client.patch(f"/api/admin/tenants/{oid}", json={"sub_status": "active", "reason": "x x"}, headers=OWN)).status_code == 409
    assert (await client.post(f"/api/admin/tenants/{oid}/unsuspend", json={"reason": "Paid"}, headers=OWN)).status_code == 200
    async with AsyncSessionLocal() as db:
        assert (await db.get(Organisation, oid)).sub_status == "active"


@pytest.mark.asyncio
async def test_a_setup_ticket_cannot_replace_an_enrolled_secret(client):
    email = await seed_admin()
    r = (await client.post("/api/admin/login", json={"email": email, "password": "portal-pass-123456"})).json()
    from backend.services import mfa
    s = (await client.post("/api/admin/mfa/setup", json={"ticket": r["ticket"]})).json()
    ok = await client.post("/api/admin/mfa/setup/confirm", json={"ticket": r["ticket"], "code": mfa.totp(s["secret"])})
    assert ok.status_code == 200
    assert (await client.post("/api/admin/mfa/setup", json={"ticket": r["ticket"]})).status_code == 401


async def _custom_admin(client, owner_hdr, perms, **spec):
    r = await client.post("/api/admin/roles", json={"name": f"Lead {uuid.uuid4().hex[:4]}", "reason": "team lead",
                                                    "permissions": {p: True for p in perms}, **spec}, headers=owner_hdr)
    assert r.status_code == 201, r.text
    email = await seed_admin(role=None)
    async with AsyncSessionLocal() as db:
        a = (await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == email))).scalar_one()
        a.role_id = r.json()["id"]
        await db.commit()
    return await login(client, email)


@pytest.mark.asyncio
async def test_a_role_editor_cannot_widen_fields_scope_or_limits(client):
    OWN, _ = await admin_headers(client)
    L = await _custom_admin(client, OWN, ["edit_roles", "view_shops", "give_discount"],
                            field_levels={"billing": "own"}, limits={"max_discount_pct": 10})
    wider = await client.post("/api/admin/roles", json={"name": f"W {uuid.uuid4().hex[:4]}", "reason": "try",
                                                        "permissions": {"view_shops": True}, "field_levels": {"billing": "shown"}}, headers=L)
    assert wider.status_code == 403
    more = await client.post("/api/admin/roles", json={"name": f"M {uuid.uuid4().hex[:4]}", "reason": "try",
                                                       "permissions": {"give_discount": True}, "limits": {"max_discount_pct": 50}}, headers=L)
    assert more.status_code == 403


@pytest.mark.asyncio
async def test_only_an_owner_can_change_or_remove_an_owner(client):
    OWN, owner_email = await admin_headers(client)
    L = await _custom_admin(client, OWN, ["manage_admins", "view_shops"])
    oid = await _admin_id(owner_email)
    assert (await client.patch(f"/api/admin/admins/{oid}", json={"role": "support", "reason": "coup"}, headers=L)).status_code == 403
    assert (await client.post(f"/api/admin/admins/{oid}/deactivate", json={"reason": "coup"}, headers=L)).status_code == 403


@pytest.mark.asyncio
async def test_trials_ending_soon_includes_new_trials(client):
    OWN, _ = await admin_headers(client)
    oid, _ = await _shop(client)
    async with AsyncSessionLocal() as db:
        org = await db.get(Organisation, oid)
        org.sub_start, org.sub_end = date.today() - timedelta(days=7), date.today() + timedelta(days=3)
        await db.commit()
    ids = {t["id"] for t in (await client.get("/api/admin/tenants?ending_days=7&limit=200", headers=OWN)).json()["items"]}
    assert oid in ids


def test_account_id_typed_without_the_prefix():
    for _ in range(300):
        a = acct.new_account_id()
        assert acct.normalize(a[3:].replace("-", "")) == a
