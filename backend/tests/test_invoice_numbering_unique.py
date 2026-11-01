"""Money: invoice numbers are unique (security phase 3).

Regression for audit finding B9: three numbering schemes (an unlocked sequence, and count+1 in
finance.create_invoice and invoicing.auto_draft_on_close) could hand out the same number.
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models import Invoice, Organisation
from backend.services.numbering import next_number
from backend.tests.test_money_hardening import _order_with_total


@pytest.mark.asyncio
async def test_every_creation_path_shares_one_sequence(client, tenant, db):
    """Alternate the finance "create invoice" path (was count+1) with the cash-payment path (uses
    the sequence, lazily seeded at count+1): on the old code the 4th invoice reused a number."""
    from backend.tests.test_money_hardening import _account
    aid = await _account(client, tenant)
    for i in range(4):
        _cid, oid = await _order_with_total(client, tenant, 10)
        if i % 2 == 0:
            r = await client.post("/api/finance/invoices", headers=tenant, json={"order_id": oid})
        else:
            r = await client.post(f"/api/cash/orders/{oid}/pay", headers=tenant,
                                  json={"method": "cash", "account_id": aid, "amount": 10})
        assert r.status_code in (200, 201), r.text
    org = (await db.execute(select(Organisation))).scalars().first()
    all_numbers = [n for (n,) in (await db.execute(select(Invoice.invoice_number).where(
        Invoice.org_id == org.id))).all()]
    assert len(all_numbers) == 4 and len(set(all_numbers)) == 4, all_numbers


@pytest.mark.asyncio
async def test_sequence_skips_numbers_already_taken(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    first = await next_number(db, org.id, "invoice")
    taken = f"INV-{int(first.split('-')[-1]) + 1:05d}"       # e.g. a legacy count-based number
    db.add(Invoice(org_id=org.id, invoice_number=taken, status="draft", total=0, amount_paid=0))
    await db.commit()
    nxt = await next_number(db, org.id, "invoice")
    assert nxt not in (first, taken)


@pytest.mark.asyncio
async def test_concurrent_sessions_get_distinct_numbers(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    a1 = await next_number(db, org.id, "invoice")                    # A has the sequence cached
    await db.commit()
    other = async_sessionmaker(db.bind, expire_on_commit=False)
    async with other() as b:
        b_num = await next_number(b, org.id, "invoice")
        await b.commit()
    a2 = await next_number(db, org.id, "invoice")                    # stale copy in A
    await db.commit()
    assert len({a1, b_num, a2}) == 3, (a1, b_num, a2)


@pytest.mark.asyncio
async def test_duplicate_invoice_numbers_are_refused_and_the_report_is_clean(client, tenant, db, capsys):
    """uq_invoices_org_number (ls144r4) now refuses a second copy, so new duplicates can't exist.
    The report script stays for databases from before the migration (which skips the constraint
    while legacy duplicates remain): on a clean database it finds none."""
    import os
    import sys

    from sqlalchemy.exc import IntegrityError
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from scripts import find_duplicate_invoice_numbers as report
    org = (await db.execute(select(Organisation))).scalars().first()
    db.add(Invoice(org_id=org.id, invoice_number="INV-DUP-1", status="draft", total=0, amount_paid=0))
    await db.commit()
    db.add(Invoice(org_id=org.id, invoice_number="INV-DUP-1", status="draft", total=0, amount_paid=0))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    assert await report.main() == 0
