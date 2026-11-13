"""Re-review inventory N6: the refund cap on an invoice's amount_paid was computed from the in-memory
value, which another refund on a job sharing the invoice may have lowered meanwhile; the atomic
decrement then went below zero and tripped ck_invoices_amount_paid_nonneg (a 500). The invoice row
is now locked and re-read before the cap is taken."""
from sqlalchemy import select, update

from backend.models import Client, Invoice, Order, Payment
from backend.services import invoice_money
from backend.tests.test_batch1_safety import _register


async def test_refund_cap_reads_the_invoice_row_under_lock(client, db):
    H, org, _ = await _register(client)
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    c = Client(org_id=org, name="Ravi")
    db.add(c)
    await db.flush()
    o = Order(org_id=org, client_id=c.id, location_id=loc, order_number=f"WO-T{org[:4]}", total=50)
    db.add(o)
    await db.flush()
    inv = Invoice(org_id=org, order_id=o.id, client_id=c.id, invoice_number="INV-CAP", subtotal=50, total=50,
                  amount_paid=50, status="paid")
    db.add(inv)
    await db.flush()
    db.add(Payment(org_id=org, order_id=o.id, invoice_id=inv.id, client_id=c.id, kind="payment", method="card",
                   amount=50, provider_ref="ch_cap"))
    await db.commit()
    inv = (await db.execute(select(Invoice).where(Invoice.id == inv.id))).scalar_one()
    assert float(inv.amount_paid) == 50
    # another job's refund on the same invoice committed meanwhile; this session still holds 50
    await db.execute(update(Invoice).where(Invoice.id == inv.id).values(amount_paid=10)
                     .execution_options(synchronize_session=False))
    iid = inv.id
    await invoice_money.reverse_charge(db, o, "ch_cap", None, 30, 0.0, method="card")
    await db.commit()
    db.expire_all()
    assert float((await db.execute(select(Invoice.amount_paid).where(Invoice.id == iid))).scalar_one()) == 0
