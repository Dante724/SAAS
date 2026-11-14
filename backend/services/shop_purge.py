"""Hard purge of an offboarded shop (owner decision 9).

Offboarding (services/offboarding) exports a shop and switches it off; it deletes nothing. This
deletes it for good, and only from /admin (routers/admin.py, owner role only). Nothing calls it on
a schedule, and a test keeps it that way.

Rules:
- the shop must be offboarded, at least ``purge_after_days()`` days ago (setting
  SHOP_PURGE_AFTER_DAYS, default 30, never more than 90 and never less than 1);
- the admin types the shop's exact name;
- ``preview`` (the dry run) returns the rows per table and the files it would delete, changing
  nothing; ``purge`` deletes them in one transaction and writes one action-log row.

What is deleted: every row of every table with ``org_id`` = the shop, and the rows with no org_id
of their own that hang off them (password-reset tokens and revoked sign-ins, through the shop's
staff), children first (``Base.metadata.sorted_tables`` reversed: the FK graph is acyclic apart from
self-references, which one DELETE per table handles, and warehouses.default_bin_id, which is
ON DELETE SET NULL and its bins go first). Plus the shop's media objects (local or S3), through
``media.erase``, which keeps an object another shop's blob still points at.

What is kept, and why: the platform's ``admin_action_log`` is append-only (a trigger refuses UPDATE
and DELETE, and each row is hash-chained), and its rows reference ``organisations.id``. Deleting the
shop's ``organisations`` row would need those rows deleted or their org_id set to NULL, both refused
by the trigger (and either would break the chain). So the ``organisations`` row stays as a scrubbed
tombstone: name "Purged shop", every nullable column NULL (contact details, address, logo, domains,
Stripe ids, notes), every other setting back to its default; only the id, the internal account id,
created_at, offboarded_at, plan and stage are kept. The purge's own action-log row (action
``purge_shop``) is the "purged at" marker, so no schema change is needed.

Isolation: before deleting anything, every foreign key in the schema that points at a row about to
be deleted is checked. If a row that is NOT being deleted (another shop's, or a platform row) still
points at one, the purge is refused and nothing is deleted: an ON DELETE CASCADE or SET NULL would
otherwise reach into that row, and a NO ACTION key would fail half way.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select

from backend.config import settings

MAX_AFTER_DAYS = 90
MIN_AFTER_DAYS = 1
TOMBSTONE_NAME = "Purged shop"
ACTION = "purge_shop"
# Tables with an org_id the purge keeps (see the module docstring).
KEPT_TABLES = {"admin_action_log"}
# organisations columns the tombstone keeps; every other column is NULL or back to its default.
_TOMBSTONE_KEEPS = {"id", "account_id", "created_at", "offboarded_at", "plan", "sub_status"}


class PurgeRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def purge_after_days() -> int:
    """SHOP_PURGE_AFTER_DAYS bounded to 1..90: a higher value means 90 (the owner's maximum), a
    lower one 1 (never the same day as offboarding)."""
    try:
        n = int(settings.SHOP_PURGE_AFTER_DAYS)
    except (TypeError, ValueError):
        n = 30
    return max(MIN_AFTER_DAYS, min(MAX_AFTER_DAYS, n))


def _tables():
    from backend.db.database import Base
    from backend.models import all_models  # noqa: F401 — every table registered
    return Base.metadata.sorted_tables


def targets(org_id: str) -> list:
    """[(table, WHERE clause for the shop's rows)] in delete order, children first."""
    tables = _tables()
    cond: dict = {}
    for t in tables:
        if "org_id" in t.c and t.name != "organisations" and t.name not in KEPT_TABLES:
            cond[t.name] = t.c.org_id == org_id
    for t in tables:   # parents first, so a chain of org-less tables resolves
        if "org_id" in t.c or t.name == "organisations":
            continue
        links = [fk.parent.in_(select(fk.column).where(cond[fk.column.table.name]))
                 for fk in t.foreign_keys if fk.column.table.name in cond]
        if links:
            cond[t.name] = or_(*links)
    return [(t, cond[t.name]) for t in reversed(tables) if t.name in cond]


async def counts(db, tgts: list) -> dict:
    """{table: rows the purge would delete}, tables with none left out."""
    out = {}
    for t, c in tgts:
        n = (await db.execute(select(func.count()).select_from(t).where(c))).scalar()
        if n:
            out[t.name] = n
    return out


async def cross_shop_links(db, org_id: str, tgts: list, doomed_tables) -> dict:
    """{referencing table: rows} for rows that are NOT being purged but point at a row that is.
    Only parents with rows to delete (`doomed_tables`) are looked at."""
    cond = {t.name: c for t, c in tgts}
    found: dict = {}
    for t in _tables():
        for fk in t.foreign_keys:
            parent = fk.column.table
            if parent.name not in cond or parent.name not in doomed_tables:
                continue
            doomed = fk.parent.in_(select(fk.column).where(cond[parent.name]))
            if t.name not in cond:
                kept = doomed
            elif "org_id" in t.c:
                kept = and_(doomed, or_(t.c.org_id != org_id, t.c.org_id.is_(None)))
            else:
                continue   # an org-less row linked into the shop is the shop's by definition
            n = (await db.execute(select(func.count()).select_from(t).where(kept))).scalar()
            if n:
                found[t.name] = found.get(t.name, 0) + n
    return found


async def _media(db, org_id: str) -> list:
    """Transient MediaBlob copies (id, backend, key), one per stored object of the shop."""
    from backend.models import MediaBlob
    rows = (await db.execute(select(MediaBlob.id, MediaBlob.backend, MediaBlob.storage_key).where(
        MediaBlob.org_id == org_id, MediaBlob.storage_key.isnot(None),
        MediaBlob.backend.in_(("local", "s3"))))).all()
    seen, out = set(), []
    for bid, backend, key in rows:
        if key not in seen:
            seen.add(key)
            out.append(MediaBlob(id=bid, backend=backend, storage_key=key))
    return out


async def _shared_keys(db, org_id: str, keys) -> set:
    from backend.models import MediaBlob
    if not keys:
        return set()
    return set((await db.execute(select(MediaBlob.storage_key).where(
        MediaBlob.storage_key.in_(list(keys)), or_(MediaBlob.org_id != org_id, MediaBlob.org_id.is_(None)))))
        .scalars())


async def purged_at(db, org_id: str):
    from backend.models import AdminActionLog
    return (await db.execute(select(func.min(AdminActionLog.at)).where(
        AdminActionLog.org_id == org_id, AdminActionLog.action == ACTION,
        AdminActionLog.outcome == "allowed"))).scalar()


@dataclass
class Status:
    eligible: bool
    why_not: str | None
    after_days: int
    eligible_from: datetime | None
    purged_at: datetime | None


async def status(db, org, now: datetime | None = None) -> Status:
    """Can this shop be purged now, and if not, why (no row counting)."""
    days = purge_after_days()
    done = await purged_at(db, org.id)
    if done:
        return Status(False, "This shop has already been purged", days, None, done)
    if org.offboarded_at is None:
        return Status(False, "Only an offboarded shop can be purged. This shop hasn't been offboarded.",
                      days, None, None)
    since = org.offboarded_at.replace(tzinfo=None)
    ready = since + timedelta(days=days)
    if (now or datetime.utcnow()) < ready:
        return Status(False, f"A shop can be purged {days} days after it was offboarded. "
                             f"This one can be purged from {ready.date().isoformat()}.", days, ready, None)
    return Status(True, None, days, ready, None)


async def preview(db, org) -> dict:
    """The dry run: what a purge would delete, per table, changing nothing."""
    st = await status(db, org)
    tgts = targets(org.id)
    found = await counts(db, tgts)
    blobs = await _media(db, org.id)
    shared = await _shared_keys(db, org.id, [b.storage_key for b in blobs])
    why_not = st.why_not
    if st.eligible:
        links = await cross_shop_links(db, org.id, tgts, found)
        if links:
            why_not = _links_message(links)
    return {"eligible": st.eligible and why_not is None, "why_not": why_not, "after_days": st.after_days,
            "offboarded_at": _iso(org.offboarded_at), "eligible_from": _iso(st.eligible_from),
            "purged_at": _iso(st.purged_at), "confirm_name": org.name, "counts": found,
            "total": sum(found.values()), "media_files": len([b for b in blobs if b.storage_key not in shared])}


def _links_message(links: dict) -> str:
    return ("Other records still point at this shop's data (" + ", ".join(sorted(links)) +
            "), so nothing can be deleted. Ask engineering to look at them first.")


def _iso(d):
    return d.isoformat() if d else None


def _scrub(org) -> None:
    from sqlalchemy import inspect
    for col in inspect(type(org)).columns:
        key = col.key
        if key in _TOMBSTONE_KEEPS or col.primary_key:
            continue
        if col.nullable:
            setattr(org, key, None)
        elif col.default is not None and getattr(col.default, "is_scalar", False):
            setattr(org, key, col.default.arg)
    org.name = TOMBSTONE_NAME
    org.active = False


async def purge(db, org, *, admin, confirm_name: str, reason: str, ip=None) -> dict:
    """Delete the shop's rows and media, scrub its organisations row, log it. The caller has
    checked the admin's permission and commits. Raises PurgeRefused (nothing changed)."""
    from backend.services import admin_log, media
    st = await status(db, org)
    if not st.eligible:
        raise PurgeRefused(409, st.why_not)
    if confirm_name != org.name:
        raise PurgeRefused(422, "Type the shop's name exactly as shown to confirm")
    tgts = targets(org.id)
    links = await cross_shop_links(db, org.id, tgts, await counts(db, tgts))
    if links:
        raise PurgeRefused(409, _links_message(links))
    blobs = await _media(db, org.id)
    before = {"name": org.name, "account_id": org.account_id, "offboarded_at": _iso(org.offboarded_at)}
    deleted = {}
    for t, c in tgts:
        res = await db.execute(delete(t).where(c))
        if res.rowcount:
            deleted[t.name] = res.rowcount
    _scrub(org)
    await db.flush()
    leftover = [t.name for t, c in tgts
                if (await db.execute(select(func.count()).select_from(t).where(c))).scalar()]
    if leftover:   # belt and braces: a row written mid-purge; the caller rolls everything back
        raise RuntimeError(f"rows of the purged shop remain in {leftover}")
    # The rows are gone (not yet committed), so media.erase's "does a surviving blob still use this
    # object?" check sees other shops' blobs only. Files go last, after every DELETE succeeded; if
    # the commit then failed, the rows would still be there and a second purge would finish the job.
    shared = await _shared_keys(db, org.id, [b.storage_key for b in blobs])
    files = len([b for b in blobs if b.storage_key not in shared])
    await media.erase(db, blobs)
    after = {"counts": deleted, "total": sum(deleted.values()), "media_files": files, "after_days": st.after_days}
    # The shop's own audit trail was just deleted with it, so no shop-trail row (shop_trail=False).
    await admin_log.write(db, admin=admin, action=ACTION, org_id=org.id, target=before["account_id"],
                          before=before, after=after, reason=reason, ip=ip, shop_trail=False)
    return {"status": "purged", **after}

