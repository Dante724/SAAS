"""Final-review QA-5: the memberships list.

- It ran about 13 queries per row (1,314 for 100 members); it is now a fixed number of queries.
- Every GET marked periods paid for the whole shop and committed (a write on a read). Payment is
  now synced where the money comes in (invoice_money), with the hourly sweep as the fallback, and
  the GETs only read.
- Batching changed nothing in the output: a list row matches the same member's detail view.
"""
import pytest
from sqlalchemy import event, select, update

from backend.models import Invoice, Membership, MembershipPeriod
from backend.tests.test_memberships import (
    _add,
    _client_and_boat,
    _close,
    _order,
    _plan,
    _register,
    _sell,
    _service,
)


class _Counter:
    def __init__(self, engine):
        self.engine, self.n, self.writes = engine, 0, []

    def _hit(self, conn, cursor, statement, *a, **k):
        self.n += 1
        if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) and "membership" in statement:
            self.writes.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)


async def _members(client, H, plan_id, n, start=0):
    for i in range(start, start + n):
        cid, boat = await _client_and_boat(client, H, name=f"Member {i}", phone=f"55501{i:05d}")
        await _sell(client, H, plan_id, cid, boat, billing="monthly" if i % 2 else "yearly")


@pytest.mark.asyncio
async def test_memberships_list_query_count_is_flat(client, db):
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    wash = await _service(client, H, "Wash", 30)
    plan = await _plan(client, H, oil, items=[
        {"kind": "service", "service_id": oil, "qty": 2, "rollover": True},
        {"kind": "service", "service_id": wash, "qty": 4, "window_start": "01-01", "window_end": "12-31"},
        {"kind": "discount", "pct": 10, "applies_to": "labour"}])

    async def run(path="/api/memberships"):
        with _Counter(db.bind.sync_engine) as c:
            r = await client.get(path, headers=H)
            assert r.status_code == 200, r.text
        return c.n, len(r.json()["items"])

    await _members(client, H, plan["id"], 2)
    few, n_few = await run()
    await _members(client, H, plan["id"], 8, start=2)
    many, n_many = await run()
    assert (n_few, n_many) == (2, 10)
    assert many == few, (few, many)
    assert few <= 30, few          # 23 today (auth, counts, totals, one batch per row field); was 48 for 2 rows, 184 for 10


@pytest.mark.asyncio
async def test_memberships_reads_never_write(client, db):
    """A period whose invoice was paid behind the list's back stays as it is on GET; nothing on
    the read path updates or commits membership rows."""
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat)
    await db.execute(update(Invoice).where(Invoice.id == m["invoice_id"]).values(status="paid"))
    await db.commit()
    for path in ("/api/memberships", f"/api/memberships/for?client_id={cid}", f"/api/memberships/{m['id']}"):
        with _Counter(db.bind.sync_engine) as c:
            assert (await client.get(path, headers=H)).status_code == 200
        assert c.writes == [], (path, c.writes)
    db.expire_all()
    assert (await db.execute(select(MembershipPeriod.paid_at).where(
        MembershipPeriod.membership_id == m["id"]))).scalar_one() is None


@pytest.mark.asyncio
async def test_paying_the_plan_invoice_marks_the_period_paid_and_unpauses(client, db):
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat, billing="monthly")
    await db.execute(update(Membership).where(Membership.id == m["id"]).values(status="paused"))
    await db.commit()
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=H)).json()["id"]
    r = await client.post(f"/api/finance/invoices/{m['invoice_id']}/pay", headers=H,
                          json={"amount": 45, "method": "cash", "account_id": acc})
    assert r.status_code in (200, 201), r.text
    db.expire_all()                                      # no GET in between: the payment did it
    assert (await db.execute(select(MembershipPeriod.paid_at).where(
        MembershipPeriod.membership_id == m["id"]))).scalar_one() is not None
    assert (await db.execute(select(Membership.status).where(Membership.id == m["id"]))).scalar_one() == "active"


@pytest.mark.asyncio
async def test_paying_the_job_that_sold_the_plan_marks_it_paid(client, db):
    H, _ = await _register(client)
    haul = await _service(client, H, "Haul-out", 300)
    plan = await _plan(client, H, haul, items=[{"kind": "service", "service_id": haul, "qty": 1}])
    cid, boat = await _client_and_boat(client, H)
    oid = await _order(client, H, cid, boat)
    m = await _sell(client, H, plan["id"], cid, boat, order_id=oid)
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=H)).json()["id"]
    r = await client.post(f"/api/cash/orders/{oid}/pay", headers=H,
                          json={"amount": 449, "method": "cash", "account_id": acc})
    assert r.status_code in (200, 201), r.text
    db.expire_all()
    assert (await db.execute(select(MembershipPeriod.paid_at).where(
        MembershipPeriod.membership_id == m["id"]))).scalar_one() is not None


@pytest.mark.asyncio
async def test_list_rows_match_each_members_own_detail(client):
    """Batched list rows carry exactly what the per-member view computes: counts left (with
    roll-over), pending covered lines, window, owed, saved, attention."""
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    wash = await _service(client, H, "Wash", 30)
    plan = await _plan(client, H, oil, items=[
        {"kind": "service", "service_id": oil, "qty": 3, "rollover": True},
        {"kind": "service", "service_id": wash, "qty": 2, "window_start": "01-01", "window_end": "12-31"},
        {"kind": "discount", "pct": 10, "applies_to": "labour"},
        {"kind": "perk", "label": "Priority slot"}])
    boats = []
    for i in range(3):
        cid, boat = await _client_and_boat(client, H, name=f"M{i}", phone=f"55502{i:05d}")
        await _sell(client, H, plan["id"], cid, boat, billing="monthly" if i else "yearly")
        boats.append((cid, boat))
    # member 0 used one oil change (closed job); member 1 has one pending on an open job
    oid = await _order(client, H, *boats[0])
    await _add(client, H, oid, kind="service", ref_id=oil, description="Oil change", unit_price=80)
    await _close(client, H, oid)
    oid = await _order(client, H, *boats[1])
    await _add(client, H, oid, kind="service", ref_id=oil, description="Oil change", unit_price=80)
    await _add(client, H, oid, kind="service", ref_id=wash, description="Wash", unit_price=30)

    rows = (await client.get("/api/memberships", headers=H)).json()["items"]
    assert len(rows) == 3
    lefts = {}
    for row in rows:
        detail = (await client.get(f"/api/memberships/{row['id']}", headers=H)).json()
        for k, v in row.items():
            assert detail[k] == v, (k, v, detail[k])
        lefts[row["client"]] = [i.get("left") for i in row["items"]]
    assert lefts == {"M0": [2, 2, None, None], "M1": [2, 1, None, None], "M2": [3, 2, None, None]}
    card = (await client.get(f"/api/memberships/for?client_id={boats[1][0]}", headers=H)).json()
    assert [i.get("left") for i in card["live"][0]["items"]] == [2, 1, None, None]
