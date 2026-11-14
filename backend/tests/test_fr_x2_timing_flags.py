"""Final review QA-6: cycle-time stats are aggregated in SQL over a trailing window.

/orders/timing/flags runs on every work-order list or board load. compute_dwell_stats pulled
every status-log row in the shop's history into Python (1.8 s and ~200 MB at 400k logs), and the
flags route then ran get_order_timing per order: ensure_seeded plus a statuses select each, so 2
open orders cost 13 queries and 12 cost 43. Now the dwell per log row comes from LEAD() and the
buckets (count, mean, median, p80) from GROUP BY / ROW_NUMBER() in the database, over the last
DWELL_WINDOW_DAYS; the statuses are read once per request; the query count is flat.
"""
import random
import uuid
from collections import defaultdict
from datetime import timedelta
from statistics import mean, median

import pytest
from sqlalchemy import event, select

from backend.services.dates import now_utc

pytestmark = pytest.mark.asyncio


def _old_percentile(vals, p):
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    return s[max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))]


def _python_stats(seqs, type_of):
    """The pre-QA-6 pure-Python aggregation, verbatim."""
    typed, anytype = defaultdict(list), defaultdict(list)
    for oid, seq in seqs.items():
        for i in range(len(seq) - 1):
            mins = (seq[i + 1][1] - seq[i][1]).total_seconds() / 60.0
            if mins < 0:
                continue
            typed[(seq[i][0], type_of[oid])].append(mins)
            anytype[seq[i][0]].append(mins)
    out = {}
    for k, vals in list(typed.items()) + [((s, None), v) for s, v in anytype.items()]:
        out[k] = {"n": len(vals), "avg": mean(vals), "p50": median(vals), "p80": _old_percentile(vals, 0.8)}
    return out


async def _org(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]


async def _plant(db, org_id, n_orders, *, start, type_ids=(None,), seed=7, statuses=("new", "diag", "in_repair")):
    """Orders with a random walk through `statuses`, logs starting at `start`."""
    from backend.models import Location, Order, OrderStatusLog
    rnd = random.Random(seed)
    loc = (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    seqs, type_of = {}, {}
    for _ in range(n_orders):
        tid = rnd.choice(type_ids)
        o = Order(org_id=org_id, order_number=f"T-{uuid.uuid4().hex[:6]}", location_id=loc, complaint="x",
                  status=statuses[-1], type_id=tid, is_estimate=False)
        db.add(o)
        await db.flush()
        t = start + timedelta(minutes=rnd.randint(0, 600))
        seq = []
        for s in statuses:
            db.add(OrderStatusLog(order_id=o.id, to_status=s, changed_at=t))
            seq.append((s, t))
            t += timedelta(minutes=rnd.randint(1, 900), seconds=rnd.randint(0, 59))
        seqs[o.id], type_of[o.id] = seq, tid
    await db.commit()
    return seqs, type_of


async def test_sql_stats_match_the_python_aggregation(client, tenant, db):
    from backend.models import WorkOrderType as OrderType
    from backend.services.cycle_time import compute_dwell_stats
    org = await _org(client, tenant)
    types = []
    for name in ("Phone", "Laptop"):
        t = OrderType(org_id=org, name=name)
        db.add(t)
        await db.flush()
        types.append(t.id)
    await db.commit()
    seqs, type_of = await _plant(db, org, 23, start=now_utc() - timedelta(days=3), type_ids=(None, *types))
    want = _python_stats(seqs, type_of)
    got = await compute_dwell_stats(db, org, is_estimate=False)
    assert set(got) == set(want)
    for k, w in want.items():
        g = got[k]
        assert g["n"] == w["n"], k
        for f in ("avg", "p50", "p80"):
            assert abs(g[f] - w[f]) < 0.01, (k, f, g[f], w[f])


async def test_stats_cover_only_the_trailing_window(client, tenant, db):
    from backend.services.cycle_time import DWELL_WINDOW_DAYS, compute_dwell_stats
    org = await _org(client, tenant)
    await _plant(db, org, 4, start=now_utc() - timedelta(days=DWELL_WINDOW_DAYS + 30), seed=1)
    recent, type_of = await _plant(db, org, 3, start=now_utc() - timedelta(days=2), seed=2)
    got = await compute_dwell_stats(db, org, is_estimate=False)
    assert got[("new", None)]["n"] == 3                 # was 7: years of history every list load
    assert got == {k: v for k, v in got.items()}      # plain dict of buckets
    want = _python_stats(recent, type_of)
    assert abs(got[("diag", None)]["avg"] - want[("diag", None)]["avg"]) < 0.01


class _Counter:
    def __init__(self, engine):
        self.engine, self.n = engine, 0

    def _hit(self, *a, **k):
        self.n += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)


async def _open_orders(client, tenant, n):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    return [(await client.post("/api/orders", json={"location_id": loc, "complaint": "z"}, headers=tenant)).json()["id"]
            for _ in range(n)]


async def test_flags_query_count_does_not_grow_with_the_page(client, tenant, db):
    org = await _org(client, tenant)
    await _plant(db, org, 6, start=now_utc() - timedelta(days=2))
    few = await _open_orders(client, tenant, 2)
    many = few + await _open_orders(client, tenant, 10)

    async def run(ids):
        with _Counter(db.bind.sync_engine) as c:
            r = await client.get(f"/api/orders/timing/flags?ids={','.join(ids)}", headers=tenant)
            assert r.status_code == 200, r.text
        return c.n

    await run(few)                                     # warm-up (status seeding, auth caches)
    n_few, n_many = await run(few), await run(many)
    assert n_many == n_few, (n_few, n_many)            # was 13 → 43
    assert n_many <= 12, n_many


async def test_flags_still_flag_a_stalled_order(client, tenant, db):
    """Same behaviour as before: 5 jobs sat ~30 min in "new"; one has sat 3 h → stalled."""
    from backend.models import Order
    org = await _org(client, tenant)
    await _plant(db, org, 6, start=now_utc() - timedelta(days=2), statuses=("new", "in_repair"))
    stuck = (await _open_orders(client, tenant, 1))[0]
    o = (await db.execute(select(Order).where(Order.id == stuck))).scalar_one()
    o.status_entered_at = now_utc() - timedelta(days=3)
    await db.commit()
    r = (await client.get(f"/api/orders/timing/flags?ids={stuck}", headers=tenant)).json()
    assert r["items"][stuck]["stalled"] is True
    one = (await client.get(f"/api/orders/{stuck}/timing", headers=tenant)).json()
    assert one["stalled"] is True and one["sample_n"] == 6
