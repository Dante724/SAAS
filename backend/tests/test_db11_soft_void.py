"""Audit DB-11: money rows are voided, never deleted.

- Voiding a refund keeps its refund Payment rows. Exactly the rows that refund wrote (linked by
  id, not matched by timestamp) get ``voided_at``/``voided_by_id``; every total and report then
  leaves them out, so the job counts as paid again.
- Another refund on the same job, written in the same second from the same cashbox, is untouched.
- A manual payroll bonus or penalty is reversed by a counter-entry in the append-only
  ``employee_balance_ledger``; the original row stays.
"""
import uuid

import pytest
from sqlalchemy import func, select, update

from backend.models import EmployeeBalanceLedger as L
from backend.models import Invoice, Payment, Refund
from backend.services import reconcile


async def _register(client):
    email = f"sv-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["org_id"], body.get("employee", {}).get("id")


async def _paid_job(client, H, amount=500):
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    till = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                              headers=H)).json()["id"]
    cl = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    r = await client.post("/api/orders", headers=H, json={
        "client_id": cl, "location_id": loc,
        "lines": [{"line_type": "labour", "description": "Fix", "qty": 1, "unit_price": amount}]})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    iid = (await client.post("/api/finance/invoices", json={"order_id": oid}, headers=H)).json()["id"]
    r = await client.post(f"/api/finance/invoices/{iid}/pay", headers=H,
                          json={"method": "cash", "account_id": till, "amount": amount})
    assert r.status_code == 200, r.text
    return oid, iid, till


async def _refund(client, H, oid, till, amount):
    r = await client.post("/api/returns/refunds", json={"account_id": till, "amount": amount, "order_id": oid},
                          headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _all_rows(stmt):
    return stmt.execution_options(include_voided=True)


async def _refund_rows(db, oid, *, voided=None):
    db.expire_all()
    q = select(Payment).where(Payment.order_id == oid, Payment.kind == "refund")
    rows = (await db.execute(_all_rows(q))).scalars().all()
    if voided is None:
        return rows
    return [p for p in rows if bool(p.voided_at) == voided]


@pytest.mark.asyncio
async def test_refund_void_keeps_its_payment_rows_and_marks_them(client, db):
    H, org, _ = await _register(client)
    oid, iid, till = await _paid_job(client, H)
    rid = await _refund(client, H, oid, till, 100)
    assert len(await _refund_rows(db, oid)) == 1
    me = (await client.get("/api/auth/me", headers=H)).json()["id"]

    r = await client.delete(f"/api/returns/refunds/{rid}?reason=mistake", headers=H)
    assert r.status_code == 200, r.text

    rows = await _refund_rows(db, oid)
    assert len(rows) == 1, "the refund Payment must stay on record"
    p = rows[0]
    assert p.voided_at is not None and p.voided_by_id == me
    assert p.refund_id == rid
    # the refund still points at the row it wrote
    assert (await db.execute(select(Refund.payment_id).where(Refund.id == rid))).scalar_one() == p.id
    # totals leave it out: the invoice is paid in full again and nothing drifts
    assert float((await db.execute(select(Invoice.amount_paid).where(Invoice.id == iid))).scalar_one()) == 500
    assert (await db.execute(select(Payment).where(Payment.id == p.id))).scalar_one_or_none() is None, \
        "a plain read never sees a voided payment"
    assert await reconcile.find_mismatches(db, org_id=org) == []
    # the invoice's payment list (a relationship load) leaves the voided row out too
    inv = (await client.get(f"/api/finance/invoices/{iid}", headers=H)).json()
    assert p.id not in [x["id"] for x in inv.get("payments", [])]
    assert [x["kind"] for x in inv.get("payments", [])] == ["payment"]


@pytest.mark.asyncio
async def test_voided_refund_rows_are_left_out_of_sums(client, db):
    H, org, _ = await _register(client)
    oid, _iid, till = await _paid_job(client, H)
    rid = await _refund(client, H, oid, till, 100)
    assert (await client.delete(f"/api/returns/refunds/{rid}", headers=H)).status_code == 200
    db.expire_all()
    refunded = (await db.execute(select(func.coalesce(func.sum(func.abs(Payment.amount)), 0)).where(
        Payment.order_id == oid, Payment.kind == "refund"))).scalar()
    assert float(refunded) == 0
    # the job can be refunded in full again: the voided 100 no longer counts as given back
    await _refund(client, H, oid, till, 500)


@pytest.mark.asyncio
async def test_voiding_one_refund_leaves_a_same_second_sibling_alone(client, db):
    """The old heuristic matched (order, kind, paid_at, account): two refunds written in the same
    second from the same cashbox were both deleted when one was voided."""
    H, org, _ = await _register(client)
    oid, iid, till = await _paid_job(client, H)
    first = await _refund(client, H, oid, till, 30)
    second = await _refund(client, H, oid, till, 20)
    rows = await _refund_rows(db, oid)
    assert len(rows) == 2
    same = rows[0].paid_at
    await db.execute(update(Payment).where(Payment.id.in_([p.id for p in rows])).values(paid_at=same))
    await db.commit()

    assert (await client.delete(f"/api/returns/refunds/{first}", headers=H)).status_code == 200

    live = await _refund_rows(db, oid, voided=False)
    assert [p.refund_id for p in live] == [second]
    assert float(live[0].amount) == pytest.approx(20)
    gone = await _refund_rows(db, oid, voided=True)
    assert [p.refund_id for p in gone] == [first]
    assert float((await db.execute(select(Invoice.amount_paid).where(Invoice.id == iid))).scalar_one()) == 480
    assert await reconcile.find_mismatches(db, org_id=org) == []


@pytest.mark.asyncio
async def test_a_legacy_refund_without_link_voids_only_its_own_row(client, db):
    """Rows written before payments carried refund_id: the refund's own payment_id names one row,
    and an unlinked sibling is matched only among unlinked rows."""
    H, org, _ = await _register(client)
    oid, _iid, till = await _paid_job(client, H)
    first = await _refund(client, H, oid, till, 30)
    second = await _refund(client, H, oid, till, 20)
    rows = await _refund_rows(db, oid)
    await db.execute(update(Payment).where(Payment.id.in_([p.id for p in rows]))
                     .values(paid_at=rows[0].paid_at, refund_id=None))
    await db.commit()
    assert (await client.delete(f"/api/returns/refunds/{first}", headers=H)).status_code == 200
    live = await _refund_rows(db, oid, voided=False)
    assert len(live) == 1 and float(live[0].amount) == pytest.approx(20)
    assert (await db.execute(select(Refund.voided_at).where(Refund.id == second))).scalar_one() is None


# ── payroll ledger ────────────────────────────────────────────────

async def _emp(client, h):
    r = await client.post("/api/staff/employees", json={
        "name": "Bri", "email": f"bri-{uuid.uuid4().hex[:6]}@t.dev", "password": "pw-emp-12345",
        "wage_type": "fixed", "wage_value": 0}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_removing_a_manual_bonus_posts_a_counter_entry(client, tenant, db):
    eid = await _emp(client, tenant)
    b = await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "bonus", "amount": 200, "note": "Great month", "month": "2026-06"},
        headers=tenant)
    assert b.status_code == 201, b.text
    bid = b.json()["id"]
    p = await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "penalty", "amount": 50, "note": "Late", "month": "2026-06"},
        headers=tenant)
    pid = p.json()["id"]

    assert (await client.delete(f"/api/payroll/adjustment/{bid}", headers=tenant)).status_code == 200
    assert (await client.delete(f"/api/payroll/adjustment/{pid}", headers=tenant)).status_code == 200

    db.expire_all()
    rows = (await db.execute(select(L).where(L.employee_id == eid).order_by(L.created_at))).scalars().all()
    by_id = {r.id: r for r in rows}
    assert bid in by_id and pid in by_id, "the original entries stay in the append-only ledger"
    assert len(rows) == 4
    counters = [r for r in rows if r.id not in (bid, pid)]
    assert sorted(float(r.amount) for r in counters) == [-200, 50]
    for c in counters:
        orig = by_id[bid] if float(c.amount) == -200 else by_id[pid]
        assert c.entry_type == orig.entry_type and c.created_at == orig.created_at
    assert float(sum(float(r.amount) for r in rows)) == 0

    row = next(i for i in (await client.get("/api/payroll/calculation?month=2026-06", headers=tenant)).json()["items"]
               if i["employee_id"] == eid)
    assert row["bonuses_total"] == 0 and row["penalties_total"] == 0 and row["computed_total"] == 0
    detail = (await client.get(f"/api/payroll/calculation/detail?employee_id={eid}&month=2026-06",
                               headers=tenant)).json()
    assert detail["bonuses"] == [] and detail["penalties"] == []

    # a removed adjustment (or its counter-entry) can't be removed again
    assert (await client.delete(f"/api/payroll/adjustment/{bid}", headers=tenant)).status_code == 409
    for c in counters:
        assert (await client.delete(f"/api/payroll/adjustment/{c.id}", headers=tenant)).status_code in (400, 409)


@pytest.mark.asyncio
async def test_own_earnings_leave_out_reversed_adjustments(client, tenant, db):
    from datetime import date

    from backend.services.payroll_views import adjustments
    eid = await _emp(client, tenant)
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    b = (await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "bonus", "amount": 80, "month": "2026-06"}, headers=tenant)).json()
    await client.post("/api/payroll/adjustment", json={
        "employee_id": eid, "kind": "bonus", "amount": 15, "month": "2026-06"}, headers=tenant)
    assert (await client.delete(f"/api/payroll/adjustment/{b['id']}", headers=tenant)).status_code == 200
    adj = await adjustments(db, org, eid, date(2026, 6, 1), date(2026, 6, 30))
    assert [x["amount"] for x in adj["bonuses"]] == [15]


# ── accounting sync: a voided payment already pushed to QuickBooks ──

async def _synced_refund(client, db, monkeypatch, *, connected=True):
    from backend.models import IntegrationMapping, QboConnection
    from backend.services import qbo
    H, org, _ = await _register(client)
    oid, _iid, till = await _paid_job(client, H)
    rid = await _refund(client, H, oid, till, 100)
    pay = (await _refund_rows(db, oid))[0]
    monkeypatch.setattr(qbo, "is_configured", lambda: True)
    db.add(QboConnection(org_id=org, realm_id="r1", environment="sandbox", connected=connected,
                         access_token="enc"))
    db.add(IntegrationMapping(org_id=org, integration="qbo", entity_type="payment",
                              local_id=pay.id, remote_id="QB-PAY-7"))
    await db.commit()

    async def _no_refresh(db_, conn):
        return None
    monkeypatch.setattr(qbo, "_ensure_fresh", _no_refresh)
    return H, org, rid, pay.id


async def _sync_logs(db, local_id):
    import asyncio

    from backend.core.jobs import job_runner
    from backend.models import IntegrationSyncLog
    await asyncio.gather(*list(job_runner._tasks))     # the QBO void runs after the response
    db.expire_all()
    return (await db.execute(select(IntegrationSyncLog).where(
        IntegrationSyncLog.local_id == local_id))).scalars().all()


@pytest.mark.asyncio
async def test_voiding_a_synced_refund_voids_it_in_quickbooks(client, db, monkeypatch):
    from backend.services import qbo
    H, org, rid, pid = await _synced_refund(client, db, monkeypatch)
    voided = []

    async def fake_void(conn, remote_id):
        voided.append(remote_id)
    monkeypatch.setattr(qbo, "_void_remote_payment", fake_void)
    assert (await client.delete(f"/api/returns/refunds/{rid}", headers=H)).status_code == 200
    logs = await _sync_logs(db, pid)
    assert voided == ["QB-PAY-7"]
    assert [(lg.status, lg.remote_id) for lg in logs] == [("voided", "QB-PAY-7")]


@pytest.mark.asyncio
async def test_a_failed_or_unconnected_remote_void_is_logged_for_a_person(client, db, monkeypatch):
    from backend.services import qbo
    H, org, rid, pid = await _synced_refund(client, db, monkeypatch, connected=False)
    assert (await client.delete(f"/api/returns/refunds/{rid}", headers=H)).status_code == 200
    logs = await _sync_logs(db, pid)
    assert len(logs) == 1 and logs[0].status == "failed"
    assert "QB-PAY-7" in logs[0].error and "void" in logs[0].error.lower()


@pytest.mark.asyncio
async def test_a_voided_payment_is_not_pushed_again(client, db, monkeypatch):
    from backend.services import qbo
    H, org, rid, pid = await _synced_refund(client, db, monkeypatch, connected=False)
    assert (await client.delete(f"/api/returns/refunds/{rid}", headers=H)).status_code == 200
    assert await qbo._build_payload(db, org, "payment", pid) is None


@pytest.mark.asyncio
async def test_the_quickbooks_void_reads_the_sync_token_then_sends_a_sparse_void(monkeypatch):
    import json

    import httpx

    from backend.models import QboConnection
    from backend.services import qbo
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"Payment": {"Id": "QB-PAY-7", "SyncToken": "3"}})
        return httpx.Response(200, json={"Payment": {"Id": "QB-PAY-7", "SyncToken": "4"}})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(qbo, "_dec", lambda v: "tok")
    await qbo._void_remote_payment(QboConnection(realm_id="r1", environment="sandbox", access_token="x"), "QB-PAY-7")
    get, post = seen
    assert get.url.path == "/v3/company/r1/payment/QB-PAY-7"
    assert post.url.path == "/v3/company/r1/payment"
    assert dict(post.url.params) == {"operation": "update", "include": "void"}
    assert json.loads(post.content) == {"Id": "QB-PAY-7", "SyncToken": "3", "sparse": True}
    assert post.headers["Authorization"] == "Bearer tok"
