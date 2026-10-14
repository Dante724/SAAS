"""One-off, idempotent production backfills.

`init_db()` runs these automatically in dev/SQLite but returns early in production (prod owns its
schema via Alembic), so two data backfills otherwise never reach a production database:

  1. Supplier→Contact unification (build 144am) — give every orphan Supplier (client_id IS NULL)
     a backing Contact marked is_supplier, so it carries a ledger and shows in Contacts/reports.
  2. Default client notification rules (build 144ah) — seed the starter order rules
     (received / status-changed / completed) for any org that has none.

Both are idempotent — safe to run repeatedly. Run once after deploying to an existing prod DB:

    python -m scripts.prod_backfills

Returns a summary of what it changed.
"""
import asyncio


async def backfill_supplier_contacts(session) -> int:
    """Link every orphan supplier to a Contact. Returns how many were linked."""
    from sqlalchemy import or_, select
    from backend.models import Client, Supplier
    orphans = (await session.execute(
        select(Supplier).where(Supplier.client_id.is_(None)))).scalars().all()
    for s in orphans:
        terms = []
        if s.phone:
            terms.append(Client.phone == s.phone)
        if s.email:
            terms.append(Client.email == s.email)
        contact = None
        if terms:
            contact = (await session.execute(select(Client).where(
                Client.org_id == s.org_id, or_(*terms)))).scalars().first()
        if not contact:
            contact = Client(org_id=s.org_id, name=s.name, phone=s.phone,
                             email=s.email, is_supplier=True)
            session.add(contact)
            await session.flush()
        elif not contact.is_supplier:
            contact.is_supplier = True
        s.client_id = contact.id
    if orphans:
        await session.commit()
    return len(orphans)


async def backfill_notification_rules(session) -> int:
    """Seed starter client notification rules for orgs with none. Returns rules created."""
    from sqlalchemy import select
    from backend.models import Organisation
    from backend.services.notification_rules import seed_default_rules
    made = 0
    for oid in (await session.execute(select(Organisation.id))).scalars().all():
        made += await seed_default_rules(session, oid)
    if made:
        await session.commit()
    return made


async def main() -> None:
    from backend.db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        suppliers = await backfill_supplier_contacts(session)
    async with AsyncSessionLocal() as session:
        rules = await backfill_notification_rules(session)
    print(f"[prod-backfills] suppliers linked: {suppliers}; notification rules seeded: {rules}")


if __name__ == "__main__":
    asyncio.run(main())
