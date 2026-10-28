"""Several items per job (multi-asset).

Only for industries where one customer drops off many items at once: tools, small engine /
equipment, phones and computers. Everything here is tenant-scoped: callers pass org_id and
every query matches it (and order_id for items), so nothing can be read or attached across
shops or across jobs.

Rules
  * Available only for ELIGIBLE industry templates; the shop switches it on in Settings.
  * Switching industry to one outside ELIGIBLE makes the feature read as off (data kept,
    shown read-only); writes are refused until it's available and on again.
  * The job's own device (orders.device_id) is item #1, created on demand, so everything that
    knows one item per job keeps working. Item #1 can't be removed.
"""
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.industry import normalize_template_key

ELIGIBLE = frozenset({"tools", "small_engine", "mobile_phone", "computer"})
OUTCOMES = ("open", "fixed", "cant_fix", "declined")
MAX_ITEMS = 50


def available(org) -> bool:
    return normalize_template_key(getattr(org, "industry_template", None)) in ELIGIBLE


def enabled(org) -> bool:
    return bool(getattr(org, "multi_asset_jobs", False)) and available(org)


async def org_of(db: AsyncSession, org_id: str):
    from backend.models import Organisation
    return (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()


async def require_enabled(db: AsyncSession, org_id: str):
    org = await org_of(db, org_id)
    if not org or not available(org):
        raise HTTPException(409, "Several items per job is only for tools, equipment, phone and computer shops")
    if not enabled(org):
        raise HTTPException(409, "Turn on 'Several items per job' in Settings first")
    return org


async def ensure_primary(db: AsyncSession, order) -> None:
    """Item #1 = the job's own device. Idempotent."""
    from backend.models import OrderItem
    if not order.device_id:
        return
    have = (await db.execute(select(OrderItem.id).where(
        OrderItem.org_id == order.org_id, OrderItem.order_id == order.id,
        OrderItem.asset_id == order.device_id))).scalar_one_or_none()
    if not have:
        db.add(OrderItem(org_id=order.org_id, order_id=order.id, asset_id=order.device_id,
                         complaint=None, sort_order=0))
        await db.flush()


async def item_for_order(db: AsyncSession, org_id: str, order_id: str, item_id: Optional[str]):
    """The item, only if it belongs to this shop AND this job (else 422 — never another tenant's)."""
    from backend.models import OrderItem
    if not item_id:
        return None
    it = (await db.execute(select(OrderItem).where(
        OrderItem.id == item_id, OrderItem.org_id == org_id, OrderItem.order_id == order_id))).scalar_one_or_none()
    if not it:
        raise HTTPException(422, "That item isn't on this job")
    return it


async def count(db: AsyncSession, org_id: str, order_id: str) -> int:
    from backend.models import OrderItem
    return (await db.execute(select(func.count()).select_from(OrderItem).where(
        OrderItem.org_id == org_id, OrderItem.order_id == order_id))).scalar() or 0


# ── phase 2: billing per item, printed grouping, split ───────────────────────────────────────
VOID_INVOICE = ("void", "voided", "cancelled", "canceled")
WHOLE_JOB = "Whole job"


def display_name(item, asset=None) -> str:
    """What the customer and the bench call an item: brand + model, else serial, else its label.
    Public-safe (no IMEI)."""
    if asset is not None:
        name = " ".join(x for x in (asset.brand, asset.model) if x) or asset.serial
        if name:
            return name
    return (item.label if item is not None else None) or "Item"


async def item_names(db: AsyncSession, org_id: str, item_ids) -> dict:
    """{item_id: (name, sort_order)} for this shop's items only. Empty for a shop outside the
    eligible industries, so its documents and tracking page never show items (even ones left
    over from before it changed industry)."""
    from backend.models import Device, OrderItem
    ids = [i for i in set(item_ids or []) if i]
    if not ids or not available(await org_of(db, org_id)):
        return {}
    items = (await db.execute(select(OrderItem).where(
        OrderItem.org_id == org_id, OrderItem.id.in_(ids)))).scalars().all()
    aids = [i.asset_id for i in items if i.asset_id]
    assets = {d.id: d for d in (await db.execute(select(Device).where(
        Device.org_id == org_id, Device.id.in_(aids)))).scalars().all()} if aids else {}
    return {i.id: (display_name(i, assets.get(i.asset_id)), i.sort_order or 0) for i in items}


async def invoiced_line_ids(db: AsyncSession, org_id: str, order_id: str) -> set:
    """This job's lines already on a live (not void) invoice of this shop."""
    from backend.models import Invoice, InvoiceLine, OrderLine
    rows = (await db.execute(select(InvoiceLine.order_line_id)
                             .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
                             .join(OrderLine, OrderLine.id == InvoiceLine.order_line_id)
                             .where(Invoice.org_id == org_id, OrderLine.order_id == order_id,
                                    Invoice.status.notin_(VOID_INVOICE)))).scalars().all()
    return set(rows)


def sort_by_item(lines: list, names: dict, key=lambda l: l.get("item_id")) -> list:
    """Item groups in the job's item order, whole-job lines last. Stable within a group."""
    def k(l):
        iid = key(l)
        if iid and iid in names:
            return (0, names[iid][1], names[iid][0])
        return (1, 0, "")
    return sorted(lines, key=k)


def group_rows(lines: list[dict]) -> list[dict]:
    """Printed layout for lines that carry ``item`` (name or None): a heading per item, its
    lines, an item subtotal, then whole-job lines last. Lines with no item at all → as-is.
    Expects lines already in that order (see sort_by_item)."""
    if not any(l.get("item") for l in lines):
        return [dict(l, row="line") for l in lines]
    out: list[dict] = []
    started, cur, acc = False, None, 0.0
    for l in lines:
        g = l.get("item") or None
        if not started or g != cur:
            if started and cur:
                out.append({"row": "subtotal", "item": cur, "total": round(acc, 2)})
            out.append({"row": "group", "item": g or WHOLE_JOB})
            started, cur, acc = True, g, 0.0
        out.append(dict(l, row="line"))
        acc += float(l.get("total") or 0)
    if cur:
        out.append({"row": "subtotal", "item": cur, "total": round(acc, 2)})
    return out


async def bill_lines(db: AsyncSession, order, lines: list, *, display_mode: str = "detailed"):
    """An invoice for exactly these lines of this job (multi-invoice per order), grouped by item.
    Caller has already checked the lines belong to the job and aren't billed yet."""
    from backend.models import Invoice, InvoiceLine, InvoiceOrder
    from backend.services.money import msum
    from backend.services.numbering import next_number
    names = await item_names(db, order.org_id, [l.order_item_id for l in lines])
    lines = sort_by_item(lines, names, key=lambda l: l.order_item_id)
    subtotal = msum([float(l.total or 0) for l in lines])
    from backend.routers.orders import order_tax
    inv = Invoice(invoice_number=await next_number(db, order.org_id, "invoice"), org_id=order.org_id,
                  order_id=order.id, client_id=order.client_id, display_mode=display_mode, status="draft",
                  subtotal=subtotal, discount=0.0, tax=order_tax(lines), total=subtotal, amount_paid=0.0,
                  issued_at=datetime.utcnow())
    db.add(inv)
    await db.flush()
    db.add(InvoiceOrder(invoice_id=inv.id, order_id=order.id, sort_order=0))
    for n, l in enumerate(lines):
        db.add(InvoiceLine(invoice_id=inv.id, order_line_id=l.id, kind=(l.kind or l.line_type or "custom")[:16],
                           sort_order=n, description=(l.description or "")[:500],
                           qty=l.qty, unit_price=l.unit_price, total=l.total))
    await db.flush()
    return inv
