"""144t — "see only my assigned orders" scoping + job-attributed timer.

The scoping filter is enforced in the query (scope_orders) on every order read
path (list, board, timing) and on detail via can_view_order. These tests pin the
query-builder rule directly (pure, no monkeypatching so nothing leaks between
tests) and prove the timer attributes time to a work order for the chip label.
"""
from sqlalchemy import select
from sqlalchemy.dialects import sqlite

from backend.models import Employee, Order, Role
from backend.services import scoping


def _sql(q) -> str:
    return str(q.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))


def test_scope_orders_own_filters_by_assignee():
    me = Employee(id="e1", org_id="org1", name="Tech", role_id="r1")
    own = Role(id="r1", org_id="org1", name="Tech", order_scope="own")
    allrole = Role(id="r2", org_id="org1", name="Mgr", order_scope="all")

    base = select(Order).where(Order.org_id == "org1")
    # own => WHERE now restricts to my id (assigned to me, or unowned). The employee
    # id only appears in the SQL when the assignee filter is actually added.
    assert "'e1'" in _sql(scoping.scope_orders(base, own, me))
    # all => untouched, no assignee restriction added
    assert "'e1'" not in _sql(scoping.scope_orders(base, allrole, me))
    # no role => untouched (default-allow, existing installs unaffected)
    assert "'e1'" not in _sql(scoping.scope_orders(base, None, me))


async def test_timer_attributes_to_work_order_and_labels(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "T", "phone": "+15550999"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "x"})).json()["id"]

    r = await client.post("/api/timeclock/start?switch=true", json={"order_id": oid, "category": "job"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    started = r.json()
    assert started["order_id"] == oid
    assert started["order_number"]                 # chip shows a real label, not "Active"

    act = (await client.get("/api/timeclock/active", headers=tenant)).json()["active"]
    assert act and act["order_id"] == oid and act["order_number"]

    await client.post("/api/timeclock/stop", headers=tenant)
