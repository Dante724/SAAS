"""Audit 29 Oct 2026, M3 + M4.

M3: restoring a voided document leg (sale, refund, return) put the money back in the till while
    the document stayed void. Restore must refuse the same legs that delete refuses.
M4: two identical delete / restore / cancel requests arriving together both passed the status
    check and both applied (till reversed twice, stock given back twice). The status change is
    now a conditional UPDATE, so the second request finds the row already changed.

The race tests slow the request down right after it reads the row, so both requests read the
old status before either writes — the window two real requests would share.
"""
import asyncio

import pytest
from sqlalchemy import select

from backend.models import StockLevel, Transaction


def _slow(monkeypatch, module, name):
    """Wrap an async helper so it pauses after returning — both racers read before either writes."""
    orig = getattr(module, name)

    async def slowed(*a, **kw):
        out = await orig(*a, **kw)
        await asyncio.sleep(0.2)
        return out

    monkeypatch.setattr(module, name, slowed)


async def _till(client, h):
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=h)
    return (await client.get("/api/cash/accounts", headers=h)).json()["items"][0]["id"]


async def _balance(client, h, aid):
    return next(a for a in (await client.get("/api/cash/accounts", headers=h)).json()["items"]
                if a["id"] == aid)["balance"]


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _part(client, h, qty=10, name="Widget"):
    loc = await _loc(client, h)
    p = (await client.post("/api/inventory/parts", json={
        "name": name, "sku": f"{name}-1", "cost_price": 5, "sell_price": 10}, headers=h)).json()
    r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", json={
        "location_id": loc, "qty": qty, "movement_type": "receive"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return p, loc


async def _on_hand(db, part_id, loc):
    db.expire_all()
    sl = (await db.execute(select(StockLevel).where(
        StockLevel.part_id == part_id, StockLevel.location_id == loc))).scalars().first()
    return (sl.qty_available or 0) if sl else 0


# ── M3 ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("ref_type", ["sale", "invoice", "order", "refund", "client_return", "purchase_return"])
async def test_restore_refuses_document_legs(client, tenant, db, ref_type):
    aid = await _till(client, tenant)
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    # a voided document leg, as the document's own void leaves it
    t = Transaction(org_id=me["org_id"], account_id=aid, txn_type="income", amount=50,
                    ref_type=ref_type, ref_id="doc-1", status="deleted")
    db.add(t)
    await db.commit()
    before = await _balance(client, tenant, aid)

    r = await client.post(f"/api/cash/transactions/{t.id}/restore", headers=tenant)
    assert r.status_code == 409, r.text
    assert await _balance(client, tenant, aid) == before
    await db.refresh(t)
    assert t.status == "deleted"


# ── M4: cash ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_delete_reverses_once(client, tenant, monkeypatch):
    from backend.routers import cash
    aid = await _till(client, tenant)
    await client.post("/api/cash/transactions", json={
        "account_id": aid, "txn_type": "income", "amount": 100}, headers=tenant)
    tid = (await client.get(f"/api/cash/transactions?account_id={aid}", headers=tenant)).json()["items"][0]["id"]

    _slow(monkeypatch, cash, "_get_txn")
    a, b = await asyncio.gather(
        client.delete(f"/api/cash/transactions/{tid}", headers=tenant),
        client.delete(f"/api/cash/transactions/{tid}", headers=tenant))
    assert sorted([a.status_code, b.status_code]) == [200, 409], (a.text, b.text)
    assert await _balance(client, tenant, aid) == 0


@pytest.mark.asyncio
async def test_concurrent_restore_applies_once(client, tenant, monkeypatch):
    from backend.routers import cash
    aid = await _till(client, tenant)
    await client.post("/api/cash/transactions", json={
        "account_id": aid, "txn_type": "income", "amount": 100}, headers=tenant)
    tid = (await client.get(f"/api/cash/transactions?account_id={aid}", headers=tenant)).json()["items"][0]["id"]
    assert (await client.delete(f"/api/cash/transactions/{tid}", headers=tenant)).status_code == 200

    _slow(monkeypatch, cash, "_get_txn")
    a, b = await asyncio.gather(
        client.post(f"/api/cash/transactions/{tid}/restore", headers=tenant),
        client.post(f"/api/cash/transactions/{tid}/restore", headers=tenant))
    assert sorted([a.status_code, b.status_code]) == [200, 409], (a.text, b.text)
    assert await _balance(client, tenant, aid) == 100


@pytest.mark.asyncio
async def test_concurrent_transfer_delete_reverses_each_leg_once(client, tenant, db, monkeypatch):
    from backend.routers import cash
    a1 = await _till(client, tenant)
    await client.post("/api/cash/accounts", json={"name": "Bank", "payment_option": "card"}, headers=tenant)
    a2 = next(a["id"] for a in (await client.get("/api/cash/accounts", headers=tenant)).json()["items"]
              if a["id"] != a1)
    await client.post("/api/cash/transactions", json={
        "account_id": a1, "txn_type": "income", "amount": 100}, headers=tenant)
    r = await client.post("/api/cash/transfer", json={
        "from_account_id": a1, "to_account_id": a2, "amount": 40}, headers=tenant)
    assert r.status_code == 201, r.text
    leg = (await db.execute(select(Transaction).where(
        Transaction.account_id == a2, Transaction.transfer_pair_id.isnot(None)))).scalars().first()

    _slow(monkeypatch, cash, "_get_txn")
    x, y = await asyncio.gather(
        client.delete(f"/api/cash/transactions/{leg.id}", headers=tenant),
        client.delete(f"/api/cash/transactions/{leg.id}", headers=tenant))
    assert sorted([x.status_code, y.status_code]) == [200, 409], (x.text, y.text)
    assert await _balance(client, tenant, a1) == 100
    assert await _balance(client, tenant, a2) == 0


# ── M4: transfers ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_transfer_cancel_unreserves_once(client, tenant, db, monkeypatch):
    from backend.routers import transfers
    p, loc = await _part(client, tenant, qty=10, name="Glass")
    loc_b = (await client.post("/api/staff/locations", headers=tenant, json={"name": "Branch B"})).json()["id"]
    t = (await client.post("/api/transfers", json={
        "from_location_id": loc, "to_location_id": loc_b,
        "lines": [{"part_id": p["id"], "qty": 3}]}, headers=tenant)).json()
    assert (await client.patch(f"/api/transfers/{t['id']}/status", json={"status": "ready"},
                               headers=tenant)).status_code == 200
    assert await _on_hand(db, p["id"], loc) == 7

    _slow(monkeypatch, transfers, "_lines")
    a, b = await asyncio.gather(
        client.patch(f"/api/transfers/{t['id']}/status", json={"status": "canceled"}, headers=tenant),
        client.patch(f"/api/transfers/{t['id']}/status", json={"status": "canceled"}, headers=tenant))
    assert sorted([a.status_code, b.status_code]) == [200, 409], (a.text, b.text)
    assert await _on_hand(db, p["id"], loc) == 10


@pytest.mark.asyncio
async def test_concurrent_transfer_complete_credits_once(client, tenant, db, monkeypatch):
    from backend.routers import transfers
    p, loc = await _part(client, tenant, qty=10, name="Lens")
    loc_b = (await client.post("/api/staff/locations", headers=tenant, json={"name": "Branch C"})).json()["id"]
    t = (await client.post("/api/transfers", json={
        "from_location_id": loc, "to_location_id": loc_b,
        "lines": [{"part_id": p["id"], "qty": 2}]}, headers=tenant)).json()
    for st in ("ready", "in_transit", "received"):
        assert (await client.patch(f"/api/transfers/{t['id']}/status", json={"status": st},
                                   headers=tenant)).status_code == 200

    _slow(monkeypatch, transfers, "_lines")
    a, b = await asyncio.gather(
        client.patch(f"/api/transfers/{t['id']}/status", json={"status": "completed"}, headers=tenant),
        client.patch(f"/api/transfers/{t['id']}/status", json={"status": "completed"}, headers=tenant))
    assert sorted([a.status_code, b.status_code]) == [200, 409], (a.text, b.text)
    assert await _on_hand(db, p["id"], loc_b) == 2


# ── M4: stock documents ─────────────────────────────────────────────────────

async def _default_wh(client, h):
    whs = (await client.get("/api/settings/warehouses?kind=product", headers=h)).json()
    return next(w for w in whs["items"] if w["is_default"])


@pytest.mark.asyncio
async def test_concurrent_write_off_delete_restores_once(client, tenant, db, monkeypatch):
    from backend.routers import stock_docs
    p, loc = await _part(client, tenant, qty=10, name="Screen")
    w = await _default_wh(client, tenant)
    d = (await client.post("/api/stock-docs/write-offs", json={
        "warehouse_id": w["id"], "lines": [{"part_id": p["id"], "qty": 3}]}, headers=tenant)).json()
    assert await _on_hand(db, p["id"], loc) == 7

    _slow(monkeypatch, stock_docs.wh, "resolve")
    a, b = await asyncio.gather(
        client.delete(f"/api/stock-docs/write-offs/{d['id']}", headers=tenant),
        client.delete(f"/api/stock-docs/write-offs/{d['id']}", headers=tenant))
    assert {a.status_code, b.status_code} == {200}, (a.text, b.text)   # second says "already reverted"
    assert await _on_hand(db, p["id"], loc) == 10


@pytest.mark.asyncio
async def test_concurrent_return_delete_restores_once(client, tenant, db, monkeypatch):
    from backend.routers import stock_docs
    p, loc = await _part(client, tenant, qty=10, name="Board")
    w = await _default_wh(client, tenant)
    r = (await client.post("/api/stock-docs/returns", json={
        "warehouse_id": w["id"], "lines": [{"part_id": p["id"], "qty": 2, "unit_cost": 5}]},
        headers=tenant)).json()
    assert await _on_hand(db, p["id"], loc) == 8

    _slow(monkeypatch, stock_docs.wh, "resolve")
    a, b = await asyncio.gather(
        client.delete(f"/api/stock-docs/returns/{r['id']}", headers=tenant),
        client.delete(f"/api/stock-docs/returns/{r['id']}", headers=tenant))
    assert sorted([a.status_code, b.status_code]) == [200, 404], (a.text, b.text)
    assert await _on_hand(db, p["id"], loc) == 10


@pytest.mark.asyncio
async def test_concurrent_posting_delete_removes_once(client, tenant, db, monkeypatch):
    from backend.routers import stock_docs
    p, loc = await _part(client, tenant, qty=10, name="Chip")
    w = await _default_wh(client, tenant)
    d = (await client.post("/api/stock-docs/postings", json={
        "warehouse_id": w["id"], "lines": [{"part_id": p["id"], "qty": 4, "unit_cost": 5}]},
        headers=tenant))
    assert d.status_code == 201, d.text
    assert await _on_hand(db, p["id"], loc) == 14

    _slow(monkeypatch, stock_docs.wh, "resolve")
    a, b = await asyncio.gather(
        client.delete(f"/api/stock-docs/postings/{d.json()['id']}", headers=tenant),
        client.delete(f"/api/stock-docs/postings/{d.json()['id']}", headers=tenant))
    assert sorted([a.status_code, b.status_code]) == [200, 404], (a.text, b.text)
    assert await _on_hand(db, p["id"], loc) == 10

