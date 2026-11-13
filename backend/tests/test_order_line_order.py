"""A job's lines come back in the order they were added, every time (re-review of #231).

The lines had no ORDER BY and no column to order by. dbi01's (org_id, id) unique index made
SQLite read them in id (random UUID) order, so test_double_submit's "bill the first line" picked
the 30 line half the time (10 of 20 runs under -n 4; 0 of 20 on parity, where the planner happened
to read them in insertion order). The double-bill guard itself never failed: no line was ever on
two invoices. These pin both down.
"""
from sqlalchemy import select

from backend.tests import kit_helpers as K


async def _job(client, h):
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=h)).json()["id"]
    return (await client.post("/api/orders", json={"client_id": cid}, headers=h)).json()["id"]


async def test_lines_come_back_in_the_order_they_were_added(client, tenant):
    oid = await _job(client, tenant)
    names = [f"Line {i}" for i in range(8)]
    for n in names:
        r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
            "line_type": "labour", "description": n, "qty": 1, "unit_price": 10})
        assert r.status_code == 201, r.text
    for _ in range(3):          # 8 random ids: 1 in 40320 to pass by luck
        assert [li["description"] for li in await K.lines(client, tenant, oid)] == names


async def test_a_line_is_never_billed_on_two_invoices(client, tenant, db):
    """The double-bill guard (invoice_money.lock_order + billed_on) is an application check under the
    job's row lock, not a unique index, and dbi01 dropped no unique index but the ledger key it
    replaced: billing the same line again is a 409 and the line sits on exactly one invoice."""
    from backend.models import InvoiceLine
    from backend.tests.test_dbi_migrations import migration
    dropped_unique = [n for n, _t, _c, uq in migration("dbi01_tenant_keys_and_indexes").DROPPED_INDEXES if uq]
    assert dropped_unique == ["ix_employee_balance_ledger_idempotency_key"]      # replaced per shop
    oid = await _job(client, tenant)
    for n, price in (("Fix", 100), ("Second", 30)):
        await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
            "line_type": "labour", "description": n, "qty": 1, "unit_price": price})
    fix = next(li["id"] for li in await K.lines(client, tenant, oid) if li["description"] == "Fix")
    first = await client.post(f"/api/cash/orders/{oid}/invoices", headers=tenant, json={"line_ids": [fix]})
    assert first.status_code == 201, first.text
    again = await client.post(f"/api/cash/orders/{oid}/invoices", headers=tenant, json={"line_ids": [fix]})
    assert again.status_code == 409, again.text
    rest = await client.post(f"/api/cash/orders/{oid}/invoices", headers=tenant, json={"line_ids": []})
    assert rest.status_code == 201 and rest.json()["total"] == 30, rest.text
    on = (await db.execute(select(InvoiceLine.invoice_id).where(InvoiceLine.order_line_id == fix))).scalars().all()
    assert on == [first.json()["invoice_id"]]
