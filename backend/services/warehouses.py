"""Builds 84/89/90 — warehouses.

Locations still exist; a warehouse lives *at* one. Stock is addressed by warehouse,
but every location keeps a **default product warehouse**, so the many existing call
sites that pass a `location_id` still resolve to something real. That shim is what
lets this land without rewriting every inventory endpoint at once.

Also here:
  * `guard_action` — Build 89's per-warehouse allowed actions and per-employee
    overrides (a role may reach a warehouse, yet still not be allowed to write off
    in it).
  * asset-warehouse provisioning + the Build 90 status→warehouse automation.
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Device,
    Employee,
    StockLevel,
    Warehouse,
    WarehouseAccess,
)

ACTIONS = ["post", "writeoff", "transfer", "sell", "stocktake"]

# Internal staging warehouses every company gets (Orderry's "Pending repair" /
# "Ready for pickup"). Assets pass through these as the job progresses.
INTERNAL_KEYS = {
    "pending_repair": "Pending repair",
    "ready_for_pickup": "Ready for pickup",
}


async def default_for_location(db: AsyncSession, org_id: str,
                               location_id: Optional[str]) -> Optional[Warehouse]:
    """The location's default product warehouse — the compatibility shim.

    144bg: fall back gracefully so stock documents (write-offs, transfers, stocktakes,
    postings) still resolve a warehouse when the location has product warehouses but none
    is flagged is_default. Order: default product WH → any product WH → any WH at location.
    Only the previously-None case changes, so existing callers are unaffected.
    """
    if not location_id:
        return None
    base = select(Warehouse).where(
        Warehouse.org_id == org_id, Warehouse.location_id == location_id,
        Warehouse.deleted_at.is_(None))
    w = (await db.execute(base.where(
        Warehouse.kind == "product", Warehouse.is_default == True))).scalars().first()  # noqa: E712
    if not w:
        w = (await db.execute(base.where(Warehouse.kind == "product")
                              .order_by(Warehouse.created_at))).scalars().first()
    if not w:
        # 144bg: provision the location's implicit default product warehouse on first need.
        # The model already treats is_default as "the location's implicit warehouse"; older orgs
        # (and ones that only ever had asset warehouses) never had one created, which broke every
        # product stock document. Created lazily and flushed into the caller's transaction.
        w = Warehouse(org_id=org_id, name="Main stock", kind="product",
                      scope="location", location_id=location_id, is_default=True)
        db.add(w)
        await db.flush()
    return w


async def provision_default(db: AsyncSession, location) -> Warehouse:
    """A new location's default product warehouse, named and set up like the one sign-up makes —
    so it shows in Settings › Warehouses straight away, not only after the first stock document."""
    w = (await db.execute(select(Warehouse).where(
        Warehouse.org_id == location.org_id, Warehouse.location_id == location.id,
        Warehouse.kind == "product", Warehouse.deleted_at.is_(None)))).scalars().first()
    if w:
        return w
    w = Warehouse(org_id=location.org_id, name=f"{location.name} warehouse"[:120], kind="product",
                  scope="location", location_id=location.id, is_default=True, allowed_actions=list(ACTIONS))
    db.add(w)
    await db.flush()
    return w


async def resolve(db: AsyncSession, org_id: str, *, warehouse_id: Optional[str] = None,
                  location_id: Optional[str] = None) -> Optional[Warehouse]:
    """Accept either a warehouse id (new callers) or a location id (old callers)."""
    if warehouse_id:
        w = (await db.execute(select(Warehouse).where(
            Warehouse.id == warehouse_id, Warehouse.org_id == org_id,
            Warehouse.deleted_at.is_(None)))).scalar_one_or_none()
        if not w:
            raise HTTPException(404, "Warehouse not found")
        return w
    return await default_for_location(db, org_id, location_id)


async def usable_at(db: AsyncSession, org_id: str, location_id: Optional[str]) -> list[Warehouse]:
    """Warehouses reachable from a location: its own + every company-wide one."""
    rows = (await db.execute(select(Warehouse).where(
        Warehouse.org_id == org_id, Warehouse.kind == "product",
        Warehouse.active == True, Warehouse.deleted_at.is_(None)))).scalars().all()  # noqa: E712
    return [w for w in rows
            if w.scope == "company" or (location_id and w.location_id == location_id)]


async def guard_action(db: AsyncSession, current: Employee, action: str,
                       *, warehouse: Optional[Warehouse] = None,
                       warehouse_id: Optional[str] = None,
                       location_id: Optional[str] = None) -> Optional[Warehouse]:
    """Build 89 — may this employee perform `action` in this warehouse?

    Three layers, most specific wins:
      1. the warehouse must permit the action at all (`allowed_actions`);
      2. an explicit per-employee `WarehouseAccess` row, if present, is authoritative;
      3. otherwise the role's warehouse scope (Build 83) decides reachability.
    """
    w = warehouse or await resolve(db, current.org_id, warehouse_id=warehouse_id,
                                   location_id=location_id)
    if not w:
        return None   # nothing to guard (org has no warehouse for this location yet)

    allowed = w.allowed_actions if isinstance(w.allowed_actions, list) else ACTIONS
    if action not in allowed:
        raise HTTPException(403, f"This warehouse doesn't allow '{action}'")

    access = (await db.execute(select(WarehouseAccess).where(
        WarehouseAccess.warehouse_id == w.id,
        WarehouseAccess.employee_id == current.id))).scalar_one_or_none()
    if access is not None:
        acts = access.actions if isinstance(access.actions, list) else []
        if action not in acts:
            raise HTTPException(403, f"You aren't allowed to '{action}' in this warehouse")
        return w

    # fall back to the role-level scope from Build 83
    from backend.services import scoping
    role = await scoping.role_for(db, current)
    if not scoping.may_use_warehouse(role, w.location_id) and \
       not scoping.may_use_warehouse(role, w.id):
        raise HTTPException(403, "You don't have access to this warehouse")
    return w


# ── Audit SEC-04: warehouse reach on every stock path ────────────────────────────────────────
# `guard_action` checked the role's warehouse scope on 6 endpoints only; jobs, transfers, stocktakes,
# reservations, returns and sales moved stock anywhere. `require_reach` is called by the stock-row
# service itself (services/stock_rows add/draw/reserve), which every stock change goes through, for
# the person on this request (`db.info["actor_id"]`, set by get_current_employee). Background jobs,
# public pages and API keys have no person and are not limited. Readers filter with `reach`.
_REACH_KEY = "warehouse_reach"


class Reach(frozenset):
    """What a limited person may reach in inventory (from `reach`).

    Re-review N3: reach is per warehouse, like `guard_action`. A **location** listed on the role
    reaches every warehouse there (`locations`). A **warehouse** listed (or a per-employee access
    row) reaches that warehouse only (`warehouses`), never the other warehouses at its location.
    Stock rows with no warehouse tag (older data) are the location's default product warehouse,
    so they are reached through it (`default_locs`). `wh_locs` = locations where at least one
    warehouse is reached. The frozenset itself is every id above (callers only test membership
    through `reachable` / `reach_clause`)."""

    locations: frozenset
    warehouses: frozenset
    default_locs: frozenset
    wh_locs: frozenset

    def __new__(cls, locations=(), warehouses=(), default_locs=(), wh_locs=()):
        self = super().__new__(cls, set(locations) | set(warehouses))
        self.locations = frozenset(locations)
        self.warehouses = frozenset(warehouses)
        self.default_locs = frozenset(default_locs)
        self.wh_locs = frozenset(wh_locs) | self.default_locs
        return self

    def touches(self, location_id: Optional[str]) -> bool:
        """Can this person reach anything at all at this location?"""
        return location_id in self.locations or location_id in self.wh_locs


async def reach(db: AsyncSession, employee: Optional[Employee] = None) -> Optional[Reach]:
    """The places this person may work with in inventory (a `Reach`); None = all of them.
    A per-employee warehouse access row (Build 89) with any action grants reach to that
    warehouse too."""
    actor_id = employee.id if employee is not None else db.info.get("actor_id")
    if not actor_id:
        return None
    cache = db.info.setdefault(_REACH_KEY, {})
    if actor_id in cache:
        return cache[actor_id]
    from backend.services import scoping
    emp = employee or (await db.execute(select(Employee).where(Employee.id == actor_id))).scalar_one_or_none()
    role = await scoping.role_for(db, emp) if emp is not None else None
    allowed = scoping.allowed_warehouses(role)
    if allowed is None or emp is None:
        cache[actor_id] = None
        return None
    from sqlalchemy import or_

    from backend.models import Location
    listed = set(allowed)
    locations = set((await db.execute(select(Location.id).where(
        Location.org_id == emp.org_id, Location.id.in_(listed or [""])))).scalars().all())
    warehouses, default_locs, wh_locs = set(), set(), set()

    def _take(wid, lid, is_default, kind):
        warehouses.add(wid)
        if lid:
            wh_locs.add(lid)
            if is_default and kind == "product":
                default_locs.add(lid)

    for wid, lid, dflt, kind in (await db.execute(select(
            Warehouse.id, Warehouse.location_id, Warehouse.is_default, Warehouse.kind).where(
            Warehouse.org_id == emp.org_id,
            or_(Warehouse.id.in_(listed or [""]), Warehouse.location_id.in_(locations or [""]))))).all():
        _take(wid, lid, dflt, kind)
    for wid, lid, dflt, kind, acts in (await db.execute(
            select(Warehouse.id, Warehouse.location_id, Warehouse.is_default, Warehouse.kind, WarehouseAccess.actions)
            .join(WarehouseAccess, WarehouseAccess.warehouse_id == Warehouse.id)
            .where(WarehouseAccess.employee_id == emp.id))).all():
        if isinstance(acts, list) and acts:
            _take(wid, lid, dflt, kind)
    cache[actor_id] = Reach(locations, warehouses, default_locs, wh_locs)
    return cache[actor_id]


def reachable(r: Optional[frozenset], location_id: Optional[str], warehouse_id: Optional[str] = None) -> bool:
    """Is stock at this place visible/usable under `r` (from `reach`)? With no warehouse given,
    the place is the location's default product warehouse."""
    if r is None or (location_id is None and warehouse_id is None):
        return True
    if not isinstance(r, Reach):                 # a plain set of ids (older callers / tests)
        return (location_id in r) or (warehouse_id is not None and warehouse_id in r)
    if location_id is not None and location_id in r.locations:
        return True
    if warehouse_id is not None:
        return warehouse_id in r.warehouses
    return location_id in r.default_locs


def reach_clause(r: Optional[frozenset], model=StockLevel):
    """SQL filter for rows (stock levels, movements, layers, units) at reachable places, or None."""
    if r is None:
        return None
    from sqlalchemy import and_, or_
    if not isinstance(r, Reach):
        ids = list(r) or [""]
        return or_(model.location_id.in_(ids), model.warehouse_id.in_(ids))
    return or_(model.location_id.in_(list(r.locations) or [""]),
               model.warehouse_id.in_(list(r.warehouses) or [""]),
               and_(model.warehouse_id.is_(None), model.location_id.in_(list(r.default_locs) or [""])))


async def deny(db: AsyncSession, location_id: Optional[str], warehouse_id: Optional[str] = None):
    """The 403 for stock out of reach, naming the place (re-review N5: a part going back to the
    branch a moved job took it from must say which branch, not just "this warehouse")."""
    from backend.models import Location
    place = None
    if warehouse_id:
        place = (await db.execute(select(Warehouse.name).where(Warehouse.id == warehouse_id))).scalar_one_or_none()
    loc = (await db.execute(select(Location.name).where(Location.id == location_id))).scalar_one_or_none() \
        if location_id else None
    where = " › ".join(x for x in (loc, place) if x) or "this warehouse"
    return HTTPException(403, f"You don't have access to stock at {where}. Ask a manager, or someone who "
                              f"works there, to do this.")


async def require_reach(db: AsyncSession, location_id: Optional[str], warehouse_id: Optional[str] = None,
                        employee: Optional[Employee] = None) -> None:
    """403 unless the person on this request may move stock at this location / warehouse."""
    if not reachable(await reach(db, employee), location_id, warehouse_id):
        raise await deny(db, location_id, warehouse_id)


async def balance(db: AsyncSession, warehouse: Warehouse) -> float:
    """Total units held — a warehouse can only be deleted at zero balance."""
    if warehouse.kind == "asset":
        return float((await db.execute(select(func.count()).select_from(Device).where(
            Device.warehouse_id == warehouse.id))).scalar() or 0)
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
                                   .where(StockLevel.warehouse_id == warehouse.id))).scalar() or 0)


# ── Build 90: asset warehouses ───────────────────────────────────────────────
async def asset_warehouse_for_client(db: AsyncSession, org_id: str, client_id: str) -> Warehouse:
    """Every client has an asset warehouse; create it on first use."""
    w = (await db.execute(select(Warehouse).where(
        Warehouse.org_id == org_id, Warehouse.kind == "asset",
        Warehouse.asset_role == "client", Warehouse.client_id == client_id))).scalars().first()
    if w:
        return w
    from backend.models import Client
    name = (await db.execute(select(Client.name).where(Client.id == client_id))).scalar_one_or_none()
    w = Warehouse(org_id=org_id, name=f"{name or 'Client'} (assets)", kind="asset",
                  scope="company", asset_role="client", client_id=client_id, allowed_actions=[])
    db.add(w)
    await db.flush()
    return w


async def asset_warehouse_for_employee(db: AsyncSession, org_id: str, employee_id: str) -> Warehouse:
    w = (await db.execute(select(Warehouse).where(
        Warehouse.org_id == org_id, Warehouse.kind == "asset",
        Warehouse.asset_role == "employee",
        Warehouse.employee_id == employee_id))).scalars().first()
    if w:
        return w
    name = (await db.execute(select(Employee.name).where(
        Employee.id == employee_id))).scalar_one_or_none()
    w = Warehouse(org_id=org_id, name=f"{name or 'Employee'} (assets)", kind="asset",
                  scope="company", asset_role="employee", employee_id=employee_id,
                  allowed_actions=[])
    db.add(w)
    await db.flush()
    return w


async def internal_asset_warehouse(db: AsyncSession, org_id: str, key: str) -> Warehouse:
    w = (await db.execute(select(Warehouse).where(
        Warehouse.org_id == org_id, Warehouse.kind == "asset",
        Warehouse.system_key == key))).scalars().first()
    if w:
        return w
    w = Warehouse(org_id=org_id, name=INTERNAL_KEYS.get(key, key), kind="asset",
                  scope="company", asset_role="internal", system_key=key, allowed_actions=[])
    db.add(w)
    await db.flush()
    return w


async def move_asset(db: AsyncSession, org_id: str, device: Device, warehouse: Warehouse,
                     *, reason: str, order_id: Optional[str] = None,
                     status_key: Optional[str] = None) -> None:
    """Move an asset between warehouses and log it. No-op when it's already there."""
    if device.warehouse_id == warehouse.id:
        return
    from backend.models import AssetMovement
    db.add(AssetMovement(org_id=org_id, asset_id=device.id,
                         from_zone=device.warehouse_id, to_zone=warehouse.id,
                         order_id=order_id, status_key=status_key))
    device.warehouse_id = warehouse.id


async def on_order_status(db: AsyncSession, org_id: str, order, status_key: str,
                          *, is_closing: bool, employee_id: Optional[str] = None) -> None:
    """Build 90 — the asset follows the job (Orderry's status-driven automation):

        check-in     → "Pending repair"
        assigned     → the assigned technician's warehouse
        ready/done   → "Ready for pickup"
        closed       → back to the client's warehouse

    Rentals invert the flow (company → client for the rental period), so an order
    whose type is a rental sends the asset *to* the client on check-in instead.
    """
    if not order.device_id:
        return
    device = (await db.execute(select(Device).where(Device.id == order.device_id))).scalar_one_or_none()
    if not device:
        return

    if is_closing:
        if device.client_id:
            target = await asset_warehouse_for_client(db, org_id, device.client_id)
            await move_asset(db, org_id, device, target,
                             reason=f"Order {order.order_number} closed",
                             order_id=order.id, status_key=status_key)
        return

    from backend.models import OrderStatus
    st = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.key == status_key))).scalars().first()
    group = (st.status_group if st else "") or ""

    if group == "new":
        target = await internal_asset_warehouse(db, org_id, "pending_repair")
    elif group == "in_progress" and order.assigned_to_id:
        target = await asset_warehouse_for_employee(db, org_id, order.assigned_to_id)
    elif group in ("ready", "done"):
        target = await internal_asset_warehouse(db, org_id, "ready_for_pickup")
    else:
        return
    await move_asset(db, org_id, device, target,
                     reason=f"Order {order.order_number} → {status_key}",
                     order_id=order.id, status_key=status_key)


def serialize(w: Warehouse, bal: Optional[float] = None) -> dict:
    d = {"id": w.id, "name": w.name, "kind": w.kind, "scope": w.scope,
         "location_id": w.location_id, "is_default": w.is_default,
         "asset_role": w.asset_role, "system_key": w.system_key,
         "client_id": w.client_id, "employee_id": w.employee_id,
         "allowed_actions": w.allowed_actions if isinstance(w.allowed_actions, list) else [],
         "active": w.active}
    if bal is not None:
        d["balance"] = bal
    return d
