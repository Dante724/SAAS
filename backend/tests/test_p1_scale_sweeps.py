"""P1 audit 2026-11-12: PERF-17 (integrity sweep) and PERF-18 (retention) run shop by shop, in
batches, and say when they fail.

- PERF-17: the nightly integrity sweep ran four platform-wide aggregates as single statements under
  the 15 s statement timeout; at scale it would be cancelled every night and stop reporting drift.
- PERF-18: retention purged every shop in one transaction with one unbatched DELETE per kind, and
  swallowed errors without a log line.
"""
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import event, func, select, update

from backend.models import Account, Call, Organisation
from backend.services import reconcile, retention


async def _shop(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "Shop", "name": "O", "email": f"s-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=H)).json()["id"]
    return r.json()["org_id"], acc


class _Sql:
    def __init__(self, engine):
        self.engine, self.sql = engine, []

    def _hit(self, conn, cur, stmt, params, ctx, many):
        self.sql.append(stmt)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)


async def test_integrity_sweep_runs_shop_by_shop(client, db, monkeypatch):
    a, _ = await _shop(client)
    b, b_till = await _shop(client)
    await db.execute(update(Account).where(Account.id == b_till).values(balance=999))
    await db.commit()
    seen = []
    real = reconcile.find_mismatches

    async def spy(db_, *, org_id=None, checks=reconcile.CHECKS):
        seen.append(org_id)
        return await real(db_, org_id=org_id, checks=checks)
    monkeypatch.setattr(reconcile, "find_mismatches", spy)
    found = await reconcile.sweep(db)
    assert None not in seen, "a platform-wide pass"
    assert {a, b} <= set(seen)
    assert [(m.org_id, m.check) for m in found] == [(b, "account")]


async def test_integrity_sweep_reads_only_one_shops_ledgers_per_statement(client, db):
    a, _ = await _shop(client)
    await _shop(client)
    with _Sql(db.bind.sync_engine) as c:
        await reconcile.find_mismatches(db, org_id=a)
    stock = [s for s in c.sql if "stock_movements" in s]
    assert stock
    # both branches of the stock union are cut to the shop's locations before they are summed
    assert all(s.count("locations.org_id") >= 2 for s in stock), stock[0]
    # the invoice check sums only the shop's invoices' payments
    inv = [s for s in c.sql if "FROM payments" in s]
    assert inv and all("invoices.org_id" in s.split("GROUP BY")[0] for s in inv), inv[0]


async def test_integrity_sweep_logs_a_shop_it_could_not_check_and_goes_on(client, db, monkeypatch, caplog):
    a, a_till = await _shop(client)
    b, b_till = await _shop(client)
    await db.execute(update(Account).where(Account.id.in_([a_till, b_till])).values(balance=5))
    await db.commit()
    real = reconcile.find_mismatches

    async def flaky(db_, *, org_id=None, checks=reconcile.CHECKS):
        if org_id == a:
            from sqlalchemy import text
            await db_.execute(text("SELECT * FROM no_such_table"))
        return await real(db_, org_id=org_id, checks=checks)
    monkeypatch.setattr(reconcile, "find_mismatches", flaky)
    with caplog.at_level(logging.ERROR, logger="tekly.reconcile"):
        found = await reconcile.sweep(db)
    assert [(m.org_id, m.check) for m in found] == [(b, "account")]
    msgs = [r.getMessage() for r in caplog.records if r.name == "tekly.reconcile"]
    assert any("could not check" in m and a in m for m in msgs), msgs
    assert reconcile.LAST_FAILED == 1


async def test_retention_deletes_in_batches_and_commits_per_shop(client, db, monkeypatch):
    org, _ = await _shop(client)
    old = datetime.utcnow() - timedelta(days=400)
    db.add_all([Call(org_id=org, created_at=old) for _ in range(5)] + [Call(org_id=org)])
    (await db.get(Organisation, org)).retention_days = {"calls": 30}
    await db.commit()
    monkeypatch.setattr(retention, "BATCH", 2)
    with _Sql(db.bind.sync_engine) as c:
        done = await retention.purge_all(db)
    assert done["calls"] == 5
    deletes = [s for s in c.sql if s.lstrip().upper().startswith("DELETE FROM CALLS")]
    assert len(deletes) >= 3, deletes          # 2 + 2 + 1, not one statement for everything
    assert all("LIMIT" in s.upper() for s in deletes)
    left = (await db.execute(select(func.count()).select_from(Call).where(Call.org_id == org))).scalar()
    assert left == 1


async def test_retention_logs_a_shop_that_failed(client, db, monkeypatch, caplog):
    a, _ = await _shop(client)
    b, _ = await _shop(client)
    old = datetime.utcnow() - timedelta(days=400)
    for oid in (a, b):
        db.add(Call(org_id=oid, created_at=old))
        (await db.get(Organisation, oid)).retention_days = {"calls": 30}
    await db.commit()
    real = retention.purge_org

    async def flaky(db_, org, now=None):
        if org.id == a:
            from sqlalchemy import text
            await db_.execute(text("SELECT * FROM no_such_table"))
        return await real(db_, org, now)
    monkeypatch.setattr(retention, "purge_org", flaky)
    with caplog.at_level(logging.ERROR, logger="tekly.retention"):
        done = await retention.purge_all(db)
    assert done["calls"] == 1 and done.get("failed_shops") == 1
    assert any(a in r.getMessage() for r in caplog.records if r.name == "tekly.retention")
    db.expire_all()
    left = {r for r in (await db.execute(select(Call.org_id))).scalars().all()}
    assert left == {a}
