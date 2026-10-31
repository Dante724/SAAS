"""Look up a row by id inside the caller's org, or answer with a clear 4xx.

Writes that take an id from the request used to hand it straight to the database: an unknown
id then failed on the foreign key (500), and another shop's id was stored silently. These
helpers turn both into a 404/422 with a message that says what was wrong.
"""
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_owned_or_404(db: AsyncSession, model, obj_id: Optional[str], org_id: str,
                           label: str, *, status: int = 404, extra=()):
    """The `model` row with this id in `org_id`, or HTTPException(status, "<label> not found")."""
    row = None
    if obj_id:
        row = (await db.execute(select(model).where(
            model.id == obj_id, model.org_id == org_id, *extra))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status, f"{label} not found")
    return row


async def require_owned_ids(db: AsyncSession, model, ids: Iterable[str], org_id: str,
                            label: str, *, status: int = 422) -> None:
    """Every id in `ids` must be a `model` row in `org_id` — else one 4xx naming the bad ids."""
    want = {i for i in ids if i}
    if not want:
        return
    have = set((await db.execute(select(model.id).where(
        model.id.in_(want), model.org_id == org_id))).scalars().all())
    bad = sorted(want - have)
    if bad:
        raise HTTPException(status, f"Unknown {label}: {', '.join(bad)}")


async def require_location(db: AsyncSession, org_id: str, location_id: Optional[str]):
    """The org's Location for `location_id`. A warehouse id sent where a location id belongs is
    the common mix-up (both are listed in Settings), so that case names the right id to send."""
    from backend.models import Location, Warehouse
    loc = None
    if location_id:
        loc = (await db.execute(select(Location).where(
            Location.id == location_id, Location.org_id == org_id))).scalar_one_or_none()
    if loc is not None:
        return loc
    if location_id:
        w = (await db.execute(select(Warehouse).where(
            Warehouse.id == location_id, Warehouse.org_id == org_id))).scalar_one_or_none()
        if w is not None:
            hint = (f" — send its location_id ({w.location_id})" if w.location_id
                    else " — this is a company-wide warehouse; send a location id")
            raise HTTPException(422, f"location_id is a warehouse id ('{w.name}'){hint}")
    raise HTTPException(404, "Location not found")


async def require_unique_name(db: AsyncSession, model, org_id: str, name: Optional[str], label: str,
                              *, exclude_id: Optional[str] = None, extra=()) -> None:
    """409 when another `model` row in this org already has this name (case and surrounding
    spaces ignored). `extra` narrows the set that must be unique — siblings, a record type, live rows."""
    from sqlalchemy import func
    n = (name or "").strip().lower()
    if not n:
        return
    q = select(model.id).where(model.org_id == org_id, func.lower(func.trim(model.name)) == n, *extra)
    if exclude_id:
        q = q.where(model.id != exclude_id)
    if (await db.execute(q.limit(1))).scalar_one_or_none() is not None:
        raise HTTPException(409, f"A {label} named '{name.strip()}' already exists")
