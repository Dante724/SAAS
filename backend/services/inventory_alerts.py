"""Build 144bo — in-app inventory alerts.

Two production features, both delivered through the existing StaffNotification feed
(services.push.notify) so they land in the bell + unread count + web-push with no new
channel:

  1. Low-stock alert  — fires once when a sale/WO consume drops a part's global on-hand
     to/at or below its ``min_stock`` line. Deduped via ``Part.low_stock_alerted`` ("once
     until restocked"): it won't fire again until a receipt lifts the part back above the line.
  2. Job-unblocked   — when a part is received, work orders that were short of it and are
     now coverable get flagged; recipients + each job's assigned technician are notified.

Recipients are role-gated: active staff whose role grants BOTH ``can_manage_inventory`` and
``can_receive_inventory_alerts`` (default-allow, so an owner can mute a role by setting the
toggle to False in Settings → Roles). The assigned technician of an unblocked job is pinged
too, unless their role explicitly denies the alert toggle.

Design notes (senior-architect):
- Pure decision helpers (``crossed_low``, ``newly_covered``) are unit-tested in isolation.
- Notification rows are created with ``commit=False`` so they ride the SAME transaction as the
  stock movement that triggered them — a receipt and its "job unblocked" ping commit together
  or not at all, and a muted recipient never blocks the stock write.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Employee, Order, OrderLine, OrderStatus, Part, Role, StockLevel
from backend.services import push
from backend.services.permissions import role_allows

ALERT_PERM = "can_receive_inventory_alerts"
BASE_PERM = "can_manage_inventory"
KIND_LOW = "inventory.low_stock"
KIND_UNBLOCKED = "inventory.job_unblocked"


# ── pure decision helpers (unit-tested) ────────────────────────────────────────
def crossed_low(before: float, after: float, min_stock: float) -> bool:
    """True only on the downward edge into low territory: stock was above the line and
    has now fallen to/at or below it. min_stock<=0 means the part isn't tracked for low stock."""
    if min_stock is None or min_stock <= 0:
        return False
    return before > min_stock and after <= min_stock


def newly_covered(before: float, after: float, needs: list[tuple[str, float]]) -> list[str]:
    """Given per-job needs (oldest first) competing for the same part, return the order ids
    whose cumulative demand was NOT met by `before` on-hand but IS met by `after`. FIFO: the
    oldest waiting jobs get the newly-received stock first."""
    out: list[str] = []
    cum = 0.0
    for oid, qty in needs:
        cum += qty or 0
        if before < cum <= after:
            out.append(oid)
    return out


# ── recipient selection ────────────────────────────────────────────────────────
async def _alert_recipients(db: AsyncSession, org_id: str) -> list[Employee]:
    """Active staff whose role allows both the inventory-manage perm and the alert toggle."""
    emps = (await db.execute(select(Employee).where(
        Employee.org_id == org_id, Employee.active == True))).scalars().all()  # noqa: E712
    if not emps:
        return []
    role_ids = {e.role_id for e in emps if e.role_id}
    roles = {r.id: r for r in (await db.execute(select(Role).where(
        Role.id.in_(role_ids)))).scalars().all()} if role_ids else {}
    out = []
    for e in emps:
        role = roles.get(e.role_id)
        if role_allows(role, BASE_PERM) and role_allows(role, ALERT_PERM):
            out.append(e)
    return out


async def _role_allows_alert(db: AsyncSession, emp: Employee) -> bool:
    if not emp or not emp.role_id:
        return True  # no role => default-allow
    role = (await db.execute(select(Role).where(Role.id == emp.role_id))).scalar_one_or_none()
    return role_allows(role, ALERT_PERM)


async def maybe_low_stock_by_id(db: AsyncSession, org_id: str, part_id: str) -> bool:
    """Convenience wrapper for consume call-sites that only hold a part_id. Loads the Part
    (org-scoped) and runs the low-stock check. Never raises."""
    if not part_id:
        return False
    part = (await db.execute(select(Part).where(
        Part.id == part_id, Part.org_id == org_id))).scalar_one_or_none()
    if not part:
        return False
    return await maybe_low_stock(db, org_id, part)


async def global_on_hand(db: AsyncSession, part_id: str) -> float:
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
        .where(StockLevel.part_id == part_id))).scalar() or 0)


# ── Feature 2: low-stock on consume ─────────────────────────────────────────────
async def maybe_low_stock(db: AsyncSession, org_id: str, part: Part) -> bool:
    """Call right after a consume (use) movement. Fires a one-shot low-stock alert when the
    part's global on-hand is at/below min_stock and it hasn't already been flagged. Returns
    True if an alert was raised. No-op (returns False) when not tracked or already alerted."""
    if not part or (part.min_stock or 0) <= 0 or part.low_stock_alerted:
        return False
    on_hand = await global_on_hand(db, part.id)
    if on_hand > (part.min_stock or 0):
        return False
    part.low_stock_alerted = True
    recips = await _alert_recipients(db, org_id)
    title = "Low stock"
    body = f"{part.name} is low — {on_hand:g} left (reorder at {part.min_stock:g})."
    for e in recips:
        await push.notify(db, org_id=org_id, employee_id=e.id, kind=KIND_LOW,
                          title=title, body=body, deep_link="/inventory", commit=False)
    return True


# ── Feature 1 + dedupe reset: on receive ────────────────────────────────────────
async def on_received(db: AsyncSession, org_id: str, part: Part,
                      before_on_hand: float, after_on_hand: float) -> list[str]:
    """Call right after a receive movement. (a) Resets the low-stock dedupe flag once stock is
    back above the line so it can fire again next time. (b) Notifies when work orders that were
    short of this part are now coverable. Returns the list of unblocked order numbers."""
    # (a) reset dedupe
    if part.low_stock_alerted and after_on_hand > (part.min_stock or 0):
        part.low_stock_alerted = False

    # (b) job-unblocked — which open orders needed this part, oldest first
    closed = {"closed", "done", "delivered", "dropped", "cancelled", "completed", "picked_up"}
    for (k,) in (await db.execute(select(OrderStatus.key).where(
            OrderStatus.org_id == org_id,
            OrderStatus.status_group.in_(["closed", "dropped"])))).all():
        if k:
            closed.add(k)
    orders = (await db.execute(select(Order).where(
        Order.org_id == org_id, Order.archived == False,  # noqa: E712
        ~Order.status.in_(closed)))).scalars().all()
    if not orders:
        return []
    ometa = {o.id: o for o in orders}
    lines = (await db.execute(select(OrderLine).where(
        OrderLine.order_id.in_(list(ometa.keys())),
        OrderLine.line_type == "part", OrderLine.ref_id == part.id))).scalars().all()
    if not lines:
        return []
    need: dict = defaultdict(float)
    for l in lines:
        need[l.order_id] += l.qty or 0
    # oldest-first so the newly-received stock covers the longest-waiting jobs
    ordered = sorted(need.items(), key=lambda kv: (ometa[kv[0]].created_at or 0, kv[0]))
    unblocked_ids = newly_covered(before_on_hand, after_on_hand, ordered)
    if not unblocked_ids:
        return []

    nums = [ometa[oid].order_number for oid in unblocked_ids]
    recips = await _alert_recipients(db, org_id)
    recip_ids = {e.id for e in recips}
    # also ping the assigned technician of each unblocked job (unless their role mutes it)
    for oid in unblocked_ids:
        o = ometa[oid]
        if o.assigned_to_id and o.assigned_to_id not in recip_ids:
            tech = (await db.execute(select(Employee).where(
                Employee.id == o.assigned_to_id, Employee.active == True))).scalar_one_or_none()  # noqa: E712
            if tech and await _role_allows_alert(db, tech):
                recips.append(tech); recip_ids.add(tech.id)

    title = "Parts received — job can resume" if len(nums) == 1 else f"Parts received — {len(nums)} jobs can resume"
    body = f"{part.name} is in stock. Waiting: {', '.join(nums)}."
    for e in recips:
        dl = f"/orders?open={unblocked_ids[0]}" if len(unblocked_ids) == 1 else "/inventory"
        await push.notify(db, org_id=org_id, employee_id=e.id, kind=KIND_UNBLOCKED,
                          title=title, body=body, deep_link=dl, commit=False)
    return nums
