from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings

# Deterministic constraint names so Alembic batch-mode autogenerate can always
# render diffs (SQLite ALTER emulation requires every constraint to have a name).
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# SQLite needs check_same_thread=False; Postgres/asyncpg must NOT receive it.
_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
_engine_kwargs = dict(echo=False, connect_args=_connect_args)
if not settings.is_sqlite:
    if settings.ENVIRONMENT.lower() == "test":
        # Test suite on Postgres: pytest-asyncio gives every test a fresh event loop and asyncpg
        # connections are bound to the loop that opened them, so a pool would hand a later test a
        # connection from a dead loop ("attached to a different loop"). NullPool opens/closes per
        # checkout. Test-only — dev/staging/production keep the pooled settings below.
        from sqlalchemy.pool import NullPool

        _engine_kwargs["poolclass"] = NullPool
    else:
        # Sensible pool defaults for a pooled server driver (asyncpg).
        _engine_kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800)

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

# Enforce foreign keys on SQLite (dev) so it matches Postgres (prod), which always enforces them —
# otherwise a bad delete/orphan passes silently in dev and only fails in production.
if settings.is_sqlite:
    from sqlalchemy import event as _event

    @_event.listens_for(engine.sync_engine, "connect")
    def _fk_pragma_on(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

from backend.db import tenancy  # noqa: E402,F401 — registers the cross-tenant reference guard


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

def _sqlite_type(coltype) -> str:
    import sqlalchemy as sa
    if isinstance(coltype, sa.Boolean):
        return "BOOLEAN"
    if isinstance(coltype, (sa.Integer, sa.BigInteger, sa.SmallInteger)):
        return "INTEGER"
    if isinstance(coltype, (sa.Float, sa.Numeric)):
        return "REAL"
    if isinstance(coltype, (sa.DateTime, sa.Date, sa.Time)):
        return "DATETIME"
    return "TEXT"


def _sync_columns(conn):
    """Add any model columns that are missing from already-existing tables.

    create_all() makes new tables but never alters existing ones, so when a
    model gains a column an older DB would otherwise raise "no such column".
    This brings the live SQLite schema up to the model without dropping data
    (a tiny stand-in for a migration tool). Only additive — never destructive.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text
    insp = sa_inspect(conn)
    existing_tables = set(insp.get_table_names())
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        have = {c["name"] for c in insp.get_columns(table_name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {_sqlite_type(col.type)}"
            d = col.default
            if d is not None and getattr(d, "is_scalar", False):
                val = d.arg
                if isinstance(val, bool):
                    val = 1 if val else 0
                elif isinstance(val, str):
                    val = "'" + val.replace("'", "''") + "'"
                ddl += f" DEFAULT {val}"
            conn.execute(text(ddl))
            print(f"[auto-migrate] {table_name}: added column {col.name}")


async def init_db():
    """Schema bootstrap.

    Development (SQLite) keeps the convenient create_all + additive column sync so
    contributors and the QA harnesses work with zero setup. Production NEVER runs
    runtime DDL — the schema is owned by Alembic (`alembic upgrade head`). The
    only exception is an explicit RUN_MIGRATIONS_ON_STARTUP escape hatch.
    """
    import logging

    from backend.models import all_models  # noqa: F401 – import triggers registration
    log = logging.getLogger("tekly.db")

    if settings.is_deployed and not settings.RUN_MIGRATIONS_ON_STARTUP:
        log.info("Production mode: skipping runtime DDL; schema is managed by Alembic.")
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.is_sqlite:
            await conn.run_sync(_sync_columns)
        # Build 144do (security): self-heal any pre-existing devices that predate
        # the devices.org_id column (create_all/_sync_columns adds the column but
        # can't backfill data). Idempotent — only touches rows still NULL. In
        # production the equivalent runs inside Alembic migration r8t0v2x4z6b8.
        from sqlalchemy import text as _text
        await conn.execute(_text(
            "UPDATE devices SET org_id = ("
            "  SELECT c.org_id FROM clients c WHERE c.id = devices.client_id"
            ") WHERE client_id IS NOT NULL AND org_id IS NULL"))
        await conn.execute(_text(
            "UPDATE devices SET org_id = ("
            "  SELECT o.org_id FROM orders o WHERE o.device_id = devices.id"
            "  ORDER BY o.created_at DESC LIMIT 1"
            ") WHERE org_id IS NULL"))

    # Build 144am — unify suppliers with contacts. A supplier is the same real-world
    # entity as a Contact, and the app now opens the one contact form (with a
    # Client/Supplier role switch). Suppliers created before build 114 have no backing
    # Client, so they never carried a ledger and were invisible to Contacts/reports.
    # Self-heal: give every orphan supplier a linked Contact marked is_supplier. Idempotent
    # — only touches suppliers with client_id IS NULL. In production this backfill lives in
    # the matching Alembic migration.
    from sqlalchemy import select as _select

    from backend.models import Client as _Client
    from backend.models import Supplier as _Supplier
    async with AsyncSessionLocal() as session:
        orphans = (await session.execute(
            _select(_Supplier).where(_Supplier.client_id.is_(None)))).scalars().all()
        from sqlalchemy import or_ as _or
        for s in orphans:
            contact = None
            # Match only on identifiers actually present — a NULL phone/email would become
            # `IS NULL` and wrongly link the supplier to any contact missing that field.
            terms = []
            if s.phone:
                terms.append(_Client.phone == s.phone)
            if s.email:
                terms.append(_Client.email == s.email)
            if terms:
                contact = (await session.execute(_select(_Client).where(
                    _Client.org_id == s.org_id, _or(*terms)))).scalars().first()
            if not contact:
                contact = _Client(org_id=s.org_id, name=s.name, phone=s.phone,
                                  email=s.email, is_supplier=True)
                session.add(contact)
                await session.flush()
            elif not contact.is_supplier:
                contact.is_supplier = True
            s.client_id = contact.id
        if orphans:
            await session.commit()

    # 144ah — backfill starter client notifications for existing orgs that have none, so a
    # shop created before this build still gets order received/status-changed/completed sends.
    # Idempotent per org (seed_default_rules no-ops when the org already has client rules). In
    # production the equivalent runs inside the matching Alembic migration.
    from backend.models import Organisation as _Org
    from backend.services.notification_rules import seed_default_rules as _seed_rules
    async with AsyncSessionLocal() as session:
        org_ids = (await session.execute(_select(_Org.id))).scalars().all()
        made = 0
        for oid in org_ids:
            made += await _seed_rules(session, oid)
        if made:
            await session.commit()

    # Retire OrderStatus.triggers_notification: turn flagged statuses into equivalent
    # per-status rules (only where no rule already fires) and clear the flag. Runs after the
    # starter-rule seed above so "covered" sees those rules. Idempotent. In production the
    # equivalent runs inside Alembic migration nt144p4.
    from backend.services import legacy_status_notifs as _legacy
    async with AsyncSessionLocal() as session:
        for oid in org_ids:
            await _legacy.convert(session, oid)
        await session.commit()

    # Roles become explicit allow-lists (new permissions start off). Snapshot any legacy role
    # so it grants exactly what it granted before. Idempotent. In production the equivalent
    # runs inside Alembic migration rp144p8.
    from sqlalchemy import select as _sel

    from backend.models import Role as _Role
    from backend.services.permissions import make_explicit as _make_explicit
    async with AsyncSessionLocal() as session:
        changed = [r for r in (await session.execute(_sel(_Role))).scalars().all() if _make_explicit(r)]
        if changed:
            await session.commit()

    # 144an — seed the manual workflow stage for POs created before the field existed, mapping
    # each PO's receipt state to a sensible starting stage. Idempotent (only NULL rows). In
    # production the equivalent runs inside the matching Alembic migration.
    async with engine.begin() as conn:
        from sqlalchemy import text as _t
        await conn.execute(_t(
            "UPDATE purchase_orders SET workflow_status = CASE "
            "WHEN status='received' THEN 'po_received' "
            "WHEN status IN ('cancelled','canceled') THEN 'po_cancelled' "
            "WHEN status='grouped' THEN 'po_closed' "
            "WHEN status='draft' THEN 'po_draft' "
            "ELSE 'po_ordered' END "
            "WHERE workflow_status IS NULL"))
