"""Look up a row by id inside the caller's org, or answer with a clear 4xx.

Writes that take an id from the request used to hand it straight to the database: an unknown
id then failed on the foreign key (500), and another shop's id was stored silently. These
helpers turn both into a 404/422.

Errors come back as `{"detail": {"message", "hint", ...}}`: `message` is a plain sentence the app
shows as it is (the frontend's readableError reads `detail.message`); `hint` is for whoever wrote
the API call (field names, ids), and any extra keys (an id to open) are for the UI.
"""
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def problem(status: int, message: str, hint: Optional[str] = None, **extra) -> HTTPException:
    """An HTTPException whose detail is {message, hint?, **extra}."""
    detail = {"message": message, **({"hint": hint} if hint else {}), **extra}
    return HTTPException(status, detail)


def _gone(label: str) -> str:
    return f"That {label} doesn't exist any more. Pick another."


async def get_owned_or_404(db: AsyncSession, model, obj_id: Optional[str], org_id: str,
                           label: str, *, status: int = 404, extra=()):
    """The `model` row with this id in `org_id`, or a 404 saying the `label` (e.g. "category")
    is gone. Another shop's id reads exactly like an unknown one."""
    row = None
    if obj_id:
        row = (await db.execute(select(model).where(
            model.id == obj_id, model.org_id == org_id, *extra))).scalar_one_or_none()
    if row is None:
        raise problem(status, _gone(label), f"No {label} with id {obj_id!r} in this shop")
    return row


async def require_owned_ids(db: AsyncSession, model, ids: Iterable[str], org_id: str,
                            label: str, *, status: int = 422) -> None:
    """Every id in `ids` must be a `model` row in `org_id` — else one 4xx; the ids go in the hint."""
    want = {i for i in ids if i}
    if not want:
        return
    have = set((await db.execute(select(model.id).where(
        model.id.in_(want), model.org_id == org_id))).scalars().all())
    bad = sorted(want - have)
    if bad:
        raise problem(status, f"One of the chosen {label}s doesn't exist any more. Pick again.",
                      f"Unknown {label} id(s): {', '.join(bad)}", unknown_ids=bad)


async def require_location(db: AsyncSession, org_id: str, location_id: Optional[str]):
    """The org's Location for `location_id`. A warehouse id sent where a location id belongs is
    the common mix-up (both are listed in Settings), so that case's hint names the id to send."""
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
            raise problem(422, "Pick a location here, not a warehouse.",
                          f"location_id {location_id!r} is the warehouse '{w.name}'"
                          + (f"; its location_id is {w.location_id!r}" if w.location_id
                             else "; it is company-wide, so send one of your location ids"),
                          location_id=w.location_id)
    raise problem(404, _gone("location"), f"No location with id {location_id!r} in this shop")


async def require_unique_name(db: AsyncSession, model, org_id: str, name: Optional[str], label: str,
                              *, exclude_id: Optional[str] = None, current_name: Optional[str] = None,
                              extra=()) -> None:
    """409 when another `model` row in this org already has this name (case and surrounding
    spaces ignored). `extra` narrows the set that must be unique — siblings, a record type, live rows.

    On an edit pass `current_name`: an unchanged name is never checked, so a shop that already
    has two rows with one name (from before this check, or made by the app itself) can still
    save them. This is an application check, not a database constraint — two saves at the same
    moment can both pass."""
    from sqlalchemy import func
    n = (name or "").strip().lower()
    if not n or (current_name is not None and n == current_name.strip().lower()):
        return
    q = select(model.id).where(model.org_id == org_id, func.lower(func.trim(model.name)) == n, *extra)
    if exclude_id:
        q = q.where(model.id != exclude_id)
    other = (await db.execute(q.limit(1))).scalar_one_or_none()
    if other is not None:
        raise problem(409, f"There's already a {label} called '{name.strip()}'. Choose another name.",
                      existing_id=other)
