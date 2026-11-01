"""Migration taxbf01 (owner-approved): existing shops whose default tax wasn't charged get it as
their job tax stack. Shops that chose a real tax are untouched; existing jobs aren't recomputed;
re-running changes nothing; downgrade puts the old stacks back."""
import pytest
from sqlalchemy import update

from backend.db.database import AsyncSessionLocal, engine
from backend.models import Tax
from backend.services import tax_backfill
from backend.tests.test_qa_b_tax_default import _line_tax, _shop, _stack, _taxes


async def _run(fn):
    async with engine.begin() as conn:
        return await conn.run_sync(lambda c: fn(c, log=lambda _m: None))


async def _set_flags(default_id, other_ids):
    async with AsyncSessionLocal() as db:
        await db.execute(update(Tax).where(Tax.id == default_id).values(is_default=True))
        if other_ids:
            await db.execute(update(Tax).where(Tax.id.in_(other_ids)).values(is_default=False))
        await db.commit()


@pytest.mark.asyncio
async def test_backfill_three_shops(client):
    # A: legacy shop with a 5% default and no job stack (charges nothing today)
    a = await _shop(client, "bf-a@qa.dev", region="ca", country="CA")
    await client.put("/api/settings/tax-rules", headers=a, json={"doc_family": "tickets_sales", "tax_ids": []})
    gst_a = (await _taxes(client, a))["GST"]
    assert await _line_tax(client, a) == (0.0, 100.0)            # an existing job, before
    cid = (await client.post("/api/clients", json={"name": "Old", "phone": "+1555"}, headers=a)).json()["id"]
    old = (await client.post("/api/orders", json={"client_id": cid}, headers=a)).json()["id"]
    await client.post(f"/api/orders/{old}/lines", headers=a, json={
        "line_type": "labour", "description": "Old job", "qty": 1, "unit_price": 100})

    # B: seeded 0% sales tax in the stack, 8.25% default flagged on another tax
    b = await _shop(client, "bf-b@qa.dev", region="us", country="US")
    tx = (await client.post("/api/catalog/taxes", headers=b, json={"name": "TX", "rate": 8.25})).json()
    seeded_b = (await _taxes(client, b))["Sales tax"]
    await _set_flags(tx["id"], [seeded_b["id"]])
    assert await _stack(client, b) == [seeded_b["id"]]

    # C: the shop chose a 13% stack (its GST still carries the old default flag)
    c = await _shop(client, "bf-c@qa.dev", region="ca", country="CA")
    hst = (await client.post("/api/catalog/taxes", headers=c, json={"name": "HST", "rate": 13})).json()
    await client.put("/api/settings/tax-rules", headers=c, json={"doc_family": "tickets_sales", "tax_ids": [hst["id"]]})

    changed = await _run(tax_backfill.backfill)
    names = sorted(n for _o, n in changed)
    assert names == ["GST", "TX"]

    assert await _stack(client, a) == [gst_a["id"]]
    assert await _stack(client, b) == [tx["id"]]
    assert await _stack(client, c) == [hst["id"]]                # untouched
    assert await _line_tax(client, a) == (5.0, 105.0)            # new jobs charge it
    o = (await client.get(f"/api/orders/{old}", headers=a)).json()
    assert o["lines"][0]["tax_rate"] == 0 and o["total"] == 100  # existing job not recomputed

    assert await _run(tax_backfill.backfill) == []               # idempotent

    restored = await _run(tax_backfill.restore)
    assert restored == 2
    assert await _stack(client, a) == []
    assert await _stack(client, b) == [seeded_b["id"]]
    assert await _stack(client, c) == [hst["id"]]


@pytest.mark.asyncio
async def test_restore_keeps_a_stack_the_owner_changed_since(client):
    a = await _shop(client, "bf-d@qa.dev", region="ca", country="CA")
    await client.put("/api/settings/tax-rules", headers=a, json={"doc_family": "tickets_sales", "tax_ids": []})
    await _run(tax_backfill.backfill)
    pst = (await client.post("/api/catalog/taxes", headers=a, json={"name": "PST", "rate": 7})).json()
    await client.put("/api/settings/tax-rules", headers=a, json={"doc_family": "tickets_sales", "tax_ids": [pst["id"]]})
    await _run(tax_backfill.restore)
    assert await _stack(client, a) == [pst["id"]]
