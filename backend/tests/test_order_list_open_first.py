"""GET /api/orders?open_first=true: open jobs (not closed: closed_at unset) before closed ones,
each group in the usual newest-first order. Quick add's job link asks for it, so typing a client
who has years of closed jobs still offers the job on the bench first. Without the flag the list
keeps its newest-first order."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import Order
from backend.tests.test_order_list_search import _client, _loc, _order, _shop


@pytest.fixture
async def jobs(client, db):
    h = await _shop(client)
    loc = await _loc(client, h)
    jane = await _client(client, h, name="Jane Kim", phone="(415) 555-0134")
    made = [await _order(client, h, loc, jane) for _ in range(4)]   # WO-00001 .. WO-00004
    base = datetime(2026, 1, 1)
    for i, o in enumerate(made):
        row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
        row.created_at = base + timedelta(days=i)      # a strict newest-first order: 4, 3, 2, 1
        if i in (1, 3):                                # WO-00002 and the newest, WO-00004, closed
            row.closed_at = base + timedelta(days=10)
            row.status = "done"
    await db.commit()
    return h, [o["order_number"] for o in made]


async def _numbers(client, h, **params):
    r = await client.get("/api/orders", params=params, headers=h)
    assert r.status_code == 200, r.text
    return [o["order_number"] for o in r.json()["items"]], r.json()["total"]


@pytest.mark.asyncio
async def test_default_order_is_unchanged(client, jobs):
    h, n = jobs
    assert (await _numbers(client, h))[0] == [n[3], n[2], n[1], n[0]]
    assert (await _numbers(client, h, open_first="false"))[0] == [n[3], n[2], n[1], n[0]]


@pytest.mark.asyncio
async def test_open_first_puts_open_jobs_first(client, jobs):
    h, n = jobs
    got, total = await _numbers(client, h, open_first="true")
    assert got == [n[2], n[0], n[3], n[1]]
    assert total == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("box", ["q", "search"])
async def test_open_first_with_the_search_box(client, jobs, box):
    h, n = jobs
    for term in ("Jane", "555-0134"):
        got, _ = await _numbers(client, h, **{box: term, "open_first": "true", "limit": 2})
        assert got == [n[2], n[0]], term          # the two open jobs fill the first page
        got, _ = await _numbers(client, h, **{box: term, "limit": 2})
        assert got == [n[3], n[2]], term          # without the flag: newest first, closed or not


@pytest.mark.asyncio
async def test_open_first_pages_cleanly(client, jobs):
    h, n = jobs
    pages = [(await _numbers(client, h, open_first="true", limit=1, skip=i))[0] for i in range(4)]
    assert [p[0] for p in pages] == [n[2], n[0], n[3], n[1]]
