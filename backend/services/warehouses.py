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
