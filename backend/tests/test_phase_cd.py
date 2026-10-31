"""Builds 103–108 — bulk accrual, unified inbox, VoIP AI, multi-account email,
e-commerce sync, and approval requests."""
import pytest
from sqlalchemy import select

from backend.models import (
    ApprovalRequest,
    Call,
    EcommerceOrderMap,
    Order,
    Role,
)


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _wh_headers(client, H):
    """Webhook posts are token-gated as of build 110."""
    sec = (await client.get("/api/settings/webhook-secret", headers=H)).json()["webhook_secret"]
    return {"X-Webhook-Token": sec}


# ══ Build 103: bulk accrual ══════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_bulk_accrual_runs_every_employee_once(client, tenant):
    r = await client.post("/api/payroll/run-all", json={
        "period_start": "2020-01-01", "period_end": "2020-01-31"}, headers=tenant)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["accrued"] >= 1
    assert body["total"] == body["accrued"] + body["skipped"] + \
        sum(1 for x in body["results"] if x["status"] == "error")

    # running again skips everyone already accrued — no double-pay
    again = (await client.post("/api/payroll/run-all", json={
        "period_start": "2020-01-01", "period_end": "2020-01-31"}, headers=tenant)).json()
    assert again["accrued"] == 0 and again["skipped"] >= 1


@pytest.mark.asyncio
async def test_bulk_accrual_blocks_current_month(client, tenant):
    from backend.services.dates import today_in
    today = today_in("Asia/Kolkata")   # the test shop's day: "current month" is the shop's
    r = await client.post("/api/payroll/run-all", json={
        "period_start": today.replace(day=1).isoformat(),
        "period_end": today.isoformat()}, headers=tenant)
    assert r.status_code == 400


# ══ Build 104: unified inbox (C4: inbound now lands in Chats; the old URL forwards) ═══
async def _chat_threads(client, H, **params):
    return (await client.get("/api/chats/threads", params=params, headers=H)).json()


@pytest.mark.asyncio
async def test_inbound_threads_by_handle(client, tenant, db):
    me = await _me(client, tenant)
    org = me["org_id"]
    # two messages from the same handle land in one thread — via the new URL and the old one
    for url, text in ((f"/api/chats/webhook/{org}", "hello"), (f"/api/inbox/webhook/{org}", "are you open?")):
        r = await client.post(url, json={"channel": "whatsapp", "handle": "+15551000", "body": text},
                              headers=await _wh_headers(client, tenant))
        assert r.status_code == 201, r.text
        assert set(r.json()) >= {"thread_id", "message_id"}
    threads = await _chat_threads(client, tenant)
    mine = [t for t in threads["items"] if t["contact_phone"] == "+15551000"]
    assert len(mine) == 1 and mine[0]["status"] == "new"
    assert threads["counts"]["new"] >= 1

    # a different handle is a different thread
    await client.post(f"/api/chats/webhook/{org}", json={
        "channel": "whatsapp", "handle": "+15552000", "body": "hi"},
        headers=await _wh_headers(client, tenant))
    threads2 = await _chat_threads(client, tenant)
    assert len({t["id"] for t in threads2["items"]}) >= 2


@pytest.mark.asyncio
async def test_generic_webhook_dedupes_on_message_id(client, tenant):
    org = (await _me(client, tenant))["org_id"]
    body = {"channel": "sms", "handle": "+15553000", "body": "quote please", "external_message_id": "ext-1"}
    first = (await client.post(f"/api/chats/webhook/{org}", json=body,
                               headers=await _wh_headers(client, tenant))).json()
    again = (await client.post(f"/api/chats/webhook/{org}", json=body,
                               headers=await _wh_headers(client, tenant))).json()
    assert again["message_id"] == first["message_id"] and again["duplicate"] is True
    msgs = (await client.get(f"/api/chats/threads/{first['thread_id']}/messages", headers=tenant)).json()["items"]
    assert len(msgs) == 1


@pytest.mark.asyncio
async def test_generic_webhook_rejects_unknown_channel(client, tenant):
    org = (await _me(client, tenant))["org_id"]
    r = await client.post(f"/api/chats/webhook/{org}", json={"channel": "pigeon", "handle": "x", "body": "hi"},
                          headers=await _wh_headers(client, tenant))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_retired_inbox_thread_endpoints_are_gone(client, tenant):
    assert (await client.get("/api/inbox/threads", headers=tenant)).status_code == 404
    assert (await client.post("/api/inbox/threads", json={"channel": "sms", "handle": "1"},
                              headers=tenant)).status_code in (404, 405)


# ══ Build 105: VoIP + AI ═════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_call_ai_attach_and_insights(client, tenant, db):
    me = await _me(client, tenant)
    call = Call(org_id=me["org_id"], provider="test", direction="inbound",
                from_number="+15550101", to_number="+1999", status="completed")
    db.add(call)
    await db.commit()
    await db.refresh(call)

    r = await client.post(f"/api/comms/calls/{call.id}/ai", json={
        "transcript": "Customer asked about repair status",
        "summary": "Status enquiry", "sentiment": "neutral"}, headers=tenant)
    assert r.status_code == 200, r.text

    bad = await client.post(f"/api/comms/calls/{call.id}/ai", json={
        "sentiment": "furious"}, headers=tenant)
    assert bad.status_code == 422

    ins = (await client.get("/api/comms/calls/insights?sentiment=neutral",
                            headers=tenant)).json()
    assert any(i["id"] == call.id for i in ins["items"])


# ══ Build 106: multi-account email ═══════════════════════════════════════════
@pytest.mark.asyncio
async def test_email_accounts_and_sender_permission(client, tenant):
    await _me(client, tenant)  # ensure org exists
    # account restricted to a different employee → current user can't send
    a = await client.post("/api/settings/email-accounts", json={
        "address": "shop@x.dev", "provider": "gmail", "is_default": True,
        "allowed_employee_ids": ["someone-else"]}, headers=tenant)
    assert a.status_code == 201, a.text

    lst = (await client.get("/api/settings/email-accounts", headers=tenant)).json()
    acc = next(x for x in lst["items"] if x["address"] == "shop@x.dev")
    assert acc["can_send"] is False          # not in the allow-list

    # an unrestricted account is sendable by anyone
    await client.post("/api/settings/email-accounts", json={
        "address": "open@x.dev", "allowed_employee_ids": []}, headers=tenant)
    lst2 = (await client.get("/api/settings/email-accounts", headers=tenant)).json()
    openacc = next(x for x in lst2["items"] if x["address"] == "open@x.dev")
    assert openacc["can_send"] is True


# ══ Build 107: e-commerce sync ═══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_ecommerce_sync_is_idempotent(client, tenant, db):
    integ = await client.post("/api/ecommerce/integrations", json={
        "platform": "shopify", "store_url": "https://x.myshopify.com"}, headers=tenant)
    assert integ.status_code == 201, integ.text
    iid = integ.json()["id"]

    first = await client.post(f"/api/ecommerce/integrations/{iid}/sync-order", json={
        "external_order_id": "SHOP-1001", "client_name": "Web Buyer",
        "client_phone": "+15559000"}, headers=tenant)
    assert first.status_code == 200 and first.json()["created"] is True
    oid = first.json()["order_id"]

    # re-syncing the same external order updates, never duplicates
    second = await client.post(f"/api/ecommerce/integrations/{iid}/sync-order", json={
        "external_order_id": "SHOP-1001", "external_status": "shipped"}, headers=tenant)
    assert second.json()["created"] is False and second.json()["updated"] is True

    maps = (await db.execute(select(EcommerceOrderMap).where(
        EcommerceOrderMap.external_order_id == "SHOP-1001"))).scalars().all()
    assert len(maps) == 1 and maps[0].order_id == oid


@pytest.mark.asyncio
async def test_ecommerce_rejects_unknown_platform(client, tenant):
    r = await client.post("/api/ecommerce/integrations", json={
        "platform": "myspace"}, headers=tenant)
    assert r.status_code == 422


# ══ Build 108: approvals ═════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_refund_within_ceiling_auto_approves(client, tenant, db):
    me = await _me(client, tenant)
    role = Role(org_id=me["org_id"], name="Cashier", refund_ceiling=50)
    db.add(role)
    await db.flush()
    # attach the role to the current employee
    from backend.models import Employee
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    emp.role_id = role.id
    await db.commit()

    small = await client.post("/api/approvals", json={
        "kind": "refund", "amount": 30, "reason": "faulty part"}, headers=tenant)
    assert small.status_code == 201
    assert small.json()["auto_approved"] is True and small.json()["status"] == "approved"

    big = await client.post("/api/approvals", json={
        "kind": "refund", "amount": 500, "reason": "whole job"}, headers=tenant)
    assert big.json()["auto_approved"] is False and big.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_cannot_decide_own_approval(client, tenant):
    big = (await client.post("/api/approvals", json={
        "kind": "discount", "amount": 999, "reason": "big"}, headers=tenant)).json()
    r = await client.post(f"/api/approvals/{big['id']}/decide", json={
        "decision": "approve"}, headers=tenant)
    assert r.status_code == 403           # requester can't self-approve


@pytest.mark.asyncio
async def test_approval_decision_by_manager(client, tenant, db):
    from backend.models import Employee
    from backend.services.auth import hash_password
    me = await _me(client, tenant)

    req = (await client.post("/api/approvals", json={
        "kind": "refund", "amount": 800, "reason": "x"}, headers=tenant)).json()

    # A manager holds a role: someone with no role has no rights unless they own the shop.
    from backend.models import Role
    from backend.services.permissions import new_role_permissions
    mgr_role = Role(org_id=me["org_id"], name="Manager", permissions=new_role_permissions("Manager"),
                    perms_explicit=True)
    db.add(mgr_role)
    await db.flush()
    other = Employee(org_id=me["org_id"], name="Mgr", email="mgr@x.dev", role_id=mgr_role.id,
                     password_hash=hash_password("pw-123456789"), active=True)
    db.add(other)
    await db.commit()
    tok = (await client.post("/api/auth/token", data={
        "username": "mgr@x.dev", "password": "pw-123456789"})).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    ok = await client.post(f"/api/approvals/{req['id']}/decide", json={
        "decision": "approve"}, headers=H)
    assert ok.status_code == 200 and ok.json()["status"] == "approved"

    row = (await db.execute(select(ApprovalRequest).where(
        ApprovalRequest.id == req["id"]))).scalar_one()
    assert row.decided_by_id == other.id
