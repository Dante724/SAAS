"""One way to read and write on-hand stock (ii144a, council 2026-10-25).

A product's stock at a location can sit in several rows: one per bin, plus the unbinned row (and,
for older data, an unbinned row tagged with a warehouse). Code that looked up "the" row for
(product, location) with `scalar_one_or_none()` crashed as soon as a bin transfer created a second
one — so every read and write goes through here instead:

- `totals`  — available / reserved summed over every row at the location (availability checks);
- `add`     — atomic increment of the exact (product, location, bin) row, created when missing;
- `draw`    — guarded decrements: the named bin first, then unbinned rows, then the other bins
              (largest first). No row ever goes below zero; a shortfall raises `NotEnoughStock`
              unless the caller allows it, in which case it lands on the unbinned row;
- `reserve` — reservations are a location-level idea and live on the unbinned row.

Uniqueness is enforced by the database (uq_stock_levels_part_bin, uq_stock_levels_part_loc_unbinned).
Callers commit.
"""
from typing import Optional

from sqlalchemy import func, select, update

from backend.models import StockLevel

_EPS = 1e-9


class NotEnoughStock(Exception):
    def __init__(self, have: float, want: float):
        super().__init__(f"only {have:g} available, {want:g} wanted")
        self.have = have
        self.want = want


async def rows(db, part_id: str, location_id: str, *, lock: bool = False) -> list[StockLevel]:
    """Every row for the product at the location: unbinned first (untagged before tagged),
    then bins by quantity, largest first."""
    # populate_existing: rows this session already holds are refreshed, so a retry after a lost race
    # plans on the current numbers, not the ones read before the other writer.
    q = (select(StockLevel).where(StockLevel.part_id == part_id, StockLevel.location_id == location_id)
         .execution_options(populate_existing=True))
    if lock:
        q = q.with_for_update()
    out = list((await db.execute(q)).scalars().all())
    out.sort(key=lambda r: (r.bin_id is not None, r.warehouse_id is not None, -(r.qty_available or 0), r.id or ""))
    return out


async def totals(db, part_id: str, location_id: str) -> tuple[float, float]:
    """(available, reserved) at the location, over every bin."""
    avail, res = (await db.execute(select(
        func.coalesce(func.sum(StockLevel.qty_available), 0),
        func.coalesce(func.sum(StockLevel.qty_reserved), 0)).where(
        StockLevel.part_id == part_id, StockLevel.location_id == location_id))).one()
    return float(avail or 0), float(res or 0)


async def free(db, part_id: str, location_id: str) -> float:
    avail, res = await totals(db, part_id, location_id)
    return avail - res


async def row_for(db, part_id: str, location_id: str, bin_id: Optional[str] = None, *,
                  create: bool = False, warehouse_id: Optional[str] = None) -> Optional[StockLevel]:
    """The exact row: the bin's, or (bin_id None) the location's main unbinned row."""
    if bin_id:
        q = (select(StockLevel).where(StockLevel.part_id == part_id, StockLevel.bin_id == bin_id)
             .execution_options(populate_existing=True))
        row = (await db.execute(q)).scalars().first()
    else:
        unbinned = [r for r in await rows(db, part_id, location_id) if r.bin_id is None]
        row = next((r for r in unbinned if r.warehouse_id == warehouse_id), None) or \
            (unbinned[0] if unbinned and warehouse_id is None else None)
    if row is None and create:
        from backend.db.database import insert_ignore
        from backend.models import _uuid
        # Insert-or-ignore: two requests receiving the first unit at once must both land on one row.
        await insert_ignore(db, StockLevel, id=_uuid(), part_id=part_id, location_id=location_id,
                            bin_id=bin_id, warehouse_id=warehouse_id, qty_available=0.0, qty_reserved=0.0)
        return await row_for(db, part_id, location_id, bin_id, warehouse_id=warehouse_id)
    return row


async def add(db, part_id: str, location_id: str, qty: float, *, bin_id: Optional[str] = None,
              warehouse_id: Optional[str] = None) -> StockLevel:
    """Add `qty` (may be negative for a correction the caller has checked) to the exact row."""
    row = await row_for(db, part_id, location_id, bin_id, create=True, warehouse_id=warehouse_id)
    await _bump(db, row, "qty_available", qty)
    return row


async def draw(db, part_id: str, location_id: str, qty: float, *, bin_id: Optional[str] = None,
               allow_short: bool = False, only: Optional[list[StockLevel]] = None) -> list[tuple[Optional[str], float]]:
    """Take `qty` out of the location. Returns [(bin_id, taken), …] in the order taken.

    Each row is decremented by a guarded UPDATE (`qty_available >= take`), so two requests drawing
    at once can't push a bin negative; a lost race re-reads and tries again. When the rows don't
    hold enough: `NotEnoughStock` (nothing is changed), or with `allow_short` the rest is taken
    from the unbinned row, which may go negative — the old behaviour of paths that never checked.
    `only` limits the draw to the given rows (a warehouse's rows, for stock documents)."""
    if qty <= _EPS:
        return []
    for _attempt in range(5):
        candidates = only if only is not None else await rows(db, part_id, location_id)
        if only is not None:
            for r in candidates:
                await db.refresh(r, attribute_names=["qty_available"])
        if bin_id:
            candidates = sorted(candidates, key=lambda r: r.bin_id != bin_id)
        have = sum(max(r.qty_available or 0, 0) for r in candidates)
        if have + _EPS < qty and not allow_short:
            raise NotEnoughStock(have, qty)
        plan, left = [], qty
        for r in candidates:
            take = min(max(r.qty_available or 0, 0), left)
            if take > _EPS:
                plan.append((r, take))
                left -= take
            if left <= _EPS:
                break
        done: list[tuple[StockLevel, float]] = []
        lost = False
        for r, take in plan:
            res = await db.execute(update(StockLevel).where(
                StockLevel.id == r.id, StockLevel.qty_available >= take - _EPS)
                .values(qty_available=StockLevel.qty_available - take)
                .execution_options(synchronize_session=False))
            if res.rowcount == 0:
                lost = True
                break
            done.append((r, take))
        if lost:
            for r, take in done:              # undo this attempt, then retry on fresh numbers
                await _bump(db, r, "qty_available", take)
            continue
        for r, _take in done:
            await db.refresh(r, attribute_names=["qty_available"])
        out = [(r.bin_id, take) for r, take in done]
        if left > _EPS and allow_short:
            main = await row_for(db, part_id, location_id, None, create=True)
            await _bump(db, main, "qty_available", -left)
            out.append((None, left))
        return out
    if allow_short:      # kept losing races: the caller allowed a shortfall, so book it on the main row
        main = await row_for(db, part_id, location_id, None, create=True)
        await _bump(db, main, "qty_available", -qty)
        return [(None, qty)]
    raise NotEnoughStock(0, qty)


async def reserve(db, part_id: str, location_id: str, delta: float) -> None:
    """Hold (`delta` > 0) or release (`delta` < 0) stock at the location. A release never takes
    reserved below zero."""
    if delta > 0:
        main = await row_for(db, part_id, location_id, None, create=True)
        await _bump(db, main, "qty_reserved", delta)
        return
    left = -delta
    for _attempt in range(5):
        for r in await rows(db, part_id, location_id):
            if left <= _EPS:
                return
            await db.refresh(r, attribute_names=["qty_reserved"])
            take = min(max(r.qty_reserved or 0, 0), left)
            if take <= _EPS:
                continue
            # guarded, like draw: two releases at once can't take reserved below zero
            res = await db.execute(update(StockLevel).where(
                StockLevel.id == r.id, StockLevel.qty_reserved >= take - _EPS)
                .values(qty_reserved=StockLevel.qty_reserved - take)
                .execution_options(synchronize_session=False))
            if res.rowcount:
                await db.refresh(r, attribute_names=["qty_reserved"])
                left -= take
        if left <= _EPS:
            return


async def check_bin(db, org_id: str, location_id: str, bin_id: str):
    """The bin exists in this shop, is in use, and is at this location (404 otherwise)."""
    from fastapi import HTTPException

    from backend.models import StorageBin
    b = (await db.execute(select(StorageBin).where(
        StorageBin.id == bin_id, StorageBin.org_id == org_id, StorageBin.active == True))).scalar_one_or_none()  # noqa: E712
    if b is None:
        raise HTTPException(404, "Bin not found")
    if b.location_id != location_id:
        raise HTTPException(409, f"Bin '{b.name}' is at another location")
    return b


async def _bump(db, row: StockLevel, attr: str, delta: float) -> None:
    from backend.services.money import add_to_column
    await add_to_column(db, row, attr, delta, round_to=None)
