"""Build 144bd — recommendations (upsell checklist the customer approves).

Staff side: draft/edit/delete recommendations on a work order, then publish to the
customer. Gated by the ``can_recommend`` role permission (default-allow, so existing
roles keep working; turn it off to make a role read-only here).

The customer-facing approve/decline endpoints live in ``public.py`` (no auth).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, EventFeedItem, Order, OrderLine, Organisation, Recommendation
from backend.services.auth import get_current_employee, require_perm, record_scope
from backend.services.money import q2

router = APIRouter(prefix="/api/orders", tags=["recommendations"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}

MAX_MEDIA_CHARS = 8_000_000
VALID_KIND = {"service", "product", "custom"}
VALID_URGENCY = {"now", "soon"}     # None = no urgency label


class RecIn(BaseModel):
    title: str
    note: Optional[str] = None
    kind: str = "custom"
    ref_id: Optional[str] = None
    qty: float = 1.0
    unit_price: float = 0.0
    media_data: Optional[str] = None
    order_item_id: Optional[str] = None     # several items per job: which item (null = whole job)
    urgency: Optional[str] = None           # now | soon | None


class RecPatch(BaseModel):
    title: Optional[str] = None
    note: Optional[str] = None
    kind: Optional[str] = None
    ref_id: Optional[str] = None
    qty: Optional[float] = None
    unit_price: Optional[float] = None
    media_data: Optional[str] = None
    order_item_id: Optional[str] = None     # "" / null = whole job
    urgency: Optional[str] = None           # now | soon | "" / None = no label


async def _check_item(db: AsyncSession, org_id: str, order_id: str, item_id: Optional[str]) -> Optional[str]:
    """Several items per job: the item must be on THIS job of THIS shop (else 422)."""
    if not item_id:
        return None
    from backend.services import multi_asset as ma
    await ma.require_enabled(db, org_id)
    await ma.item_for_order(db, org_id, order_id, item_id)
    return item_id


def serialize_rec(r: Recommendation) -> dict:
    return {
        "id": r.id,
        "order_id": r.order_id,
        "title": r.title,
        "note": r.note,
        "kind": r.kind,
        "ref_id": r.ref_id,
        "qty": r.qty,
        "unit_price": r.unit_price,
        "line_total": q2((r.qty or 0) * (r.unit_price or 0)),
        "media_data": r.media_data,
        "status": r.status,
        "urgency": r.urgency,
        "decline_reason": r.decline_reason,
        "remind_after": r.remind_after.isoformat() if r.remind_after else None,
        "decided_by_name": r.decided_by_name,
        "source": r.source,
        "source_ref_id": r.source_ref_id,
        "order_item_id": r.order_item_id,
        "author_name": r.author_name,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "published_at": r.published_at.isoformat() if r.published_at else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
    }


async def _order(db: AsyncSession, org_id: str, order_id: str) -> Order:
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Work order not found")
    return o


async def _rec(db: AsyncSession, org_id: str, order_id: str, rec_id: str) -> Recommendation:
    r = (await db.execute(select(Recommendation).where(
        Recommendation.id == rec_id, Recommendation.order_id == order_id,
        Recommendation.org_id == org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Recommendation not found")
    return r


@router.get("/{order_id}/recommendations")
async def list_recommendations(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _order(db, current.org_id, order_id)
    rows = (await db.execute(select(Recommendation).where(
        Recommendation.order_id == order_id, Recommendation.org_id == current.org_id)
        .order_by(Recommendation.created_at))).scalars().all()
    return {"items": [serialize_rec(r) for r in rows]}


@router.post("/{order_id}/recommendations", status_code=201)
async def add_recommendation(
    order_id: str,
    data: RecIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_recommend")),
):
    await _order(db, current.org_id, order_id)
    if not (data.title or "").strip():
        raise HTTPException(400, "Title is required")
    if data.kind not in VALID_KIND:
        raise HTTPException(422, "kind must be service|product|custom")
    if data.media_data and len(data.media_data) > MAX_MEDIA_CHARS:
        raise HTTPException(413, "Photo too large")
    if data.urgency and data.urgency not in VALID_URGENCY:
        raise HTTPException(422, "urgency must be now|soon")
    r = Recommendation(
        org_id=current.org_id, order_id=order_id, title=data.title.strip(),
        note=data.note, kind=data.kind, ref_id=data.ref_id,
        qty=data.qty or 1.0, unit_price=data.unit_price or 0.0,
        media_data=data.media_data, status="draft", urgency=data.urgency or None,
        order_item_id=await _check_item(db, current.org_id, order_id, data.order_item_id),
        author_id=current.id, author_name=current.name)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return serialize_rec(r)


@router.patch("/{order_id}/recommendations/{rec_id}")
async def update_recommendation(
    order_id: str,
    rec_id: str,
    data: RecPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_recommend")),
):
    r = await _rec(db, current.org_id, order_id, rec_id)
    if r.status in ("approved", "declined"):
        raise HTTPException(409, "This recommendation was already decided by the customer")
    fields = data.model_dump(exclude_unset=True)
    if "kind" in fields and fields["kind"] not in VALID_KIND:
        raise HTTPException(422, "kind must be service|product|custom")
    if fields.get("media_data") and len(fields["media_data"]) > MAX_MEDIA_CHARS:
        raise HTTPException(413, "Photo too large")
    if "urgency" in fields:
        fields["urgency"] = fields["urgency"] or None
        if fields["urgency"] and fields["urgency"] not in VALID_URGENCY:
            raise HTTPException(422, "urgency must be now|soon")
    if "order_item_id" in fields:
        fields["order_item_id"] = await _check_item(db, current.org_id, order_id, fields["order_item_id"])
    for k, v in fields.items():
        setattr(r, k, v)
    await db.commit()
    await db.refresh(r)
    return serialize_rec(r)


@router.post("/{order_id}/recommendations/{rec_id}/publish")
async def publish_recommendation(
    order_id: str,
    rec_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_publish_recommend")),
):
    """Move a draft to the customer's view (status=sent)."""
    r = await _rec(db, current.org_id, order_id, rec_id)
    if r.status == "draft":
        r.status = "sent"
        r.published_at = datetime.utcnow()
        await db.commit()
        await db.refresh(r)
    return serialize_rec(r)


@router.post("/{order_id}/recommendations/publish-all")
async def publish_all(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_publish_recommend")),
):
    await _order(db, current.org_id, order_id)
    rows = (await db.execute(select(Recommendation).where(
        Recommendation.order_id == order_id, Recommendation.org_id == current.org_id,
        Recommendation.status == "draft"))).scalars().all()
    now = datetime.utcnow()
    for r in rows:
        r.status = "sent"
        r.published_at = now
    await db.commit()
    return {"published": len(rows)}


@router.post("/{order_id}/recommendations/{rec_id}/unpublish")
async def unpublish_recommendation(
    order_id: str,
    rec_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_publish_recommend")),
):
    """Pull a sent (not-yet-decided) recommendation back to draft."""
    r = await _rec(db, current.org_id, order_id, rec_id)
    if r.status == "sent":
        r.status = "draft"
        r.published_at = None
        await db.commit()
        await db.refresh(r)
    return serialize_rec(r)


@router.delete("/{order_id}/recommendations/{rec_id}", status_code=204)
async def delete_recommendation(
    order_id: str,
    rec_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_recommend")),
):
    r = await _rec(db, current.org_id, order_id, rec_id)
    await db.delete(r)
    await db.commit()


class ReplyIn(BaseModel):
    body: str


@router.post("/{order_id}/customer-reply", status_code=201)
async def customer_reply(
    order_id: str,
    data: ReplyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_reply_customer")),
):
    """Build 144be — staff replies to the customer on the public page. Posts a
    client-visible feed message (item_type=staff_reply) that shows on track/portal.
    Gated by the org toggle ``oe_allow_staff_reply`` and the ``can_reply_customer``
    role permission — turn either off and the reply capability disappears.
    """
    o = await _order(db, current.org_id, order_id)
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one_or_none()
    if org is not None and getattr(org, "oe_allow_staff_reply", True) is False:
        raise HTTPException(403, "Replying to customers is turned off for this shop")
    body = (data.body or "").strip()
    if not body:
        raise HTTPException(400, "Message is empty")
    if len(body) > 2000:
        body = body[:2000]
    db.add(EventFeedItem(
        org_id=current.org_id, entity_type="order", entity_id=o.id,
        item_type="staff_reply", body=body, author_id=current.id,
        author_name=current.name, visibility="client"))
    await db.commit()
    return {"ok": True}


@router.post("/{order_id}/recommendations/seen")
async def clear_rec_unread(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Staff opened the tab — clear the approve/decline notification dot."""
    o = await _order(db, current.org_id, order_id)
    o.rec_unread = 0
    await db.commit()
    return {"ok": True}


async def _live_item(db: AsyncSession, rec: Recommendation) -> Optional[str]:
    """The rec's item, only if it is still on the rec's job in the rec's shop."""
    if not rec.order_item_id:
        return None
    from backend.models import OrderItem
    return (await db.execute(select(OrderItem.id).where(
        OrderItem.id == rec.order_item_id, OrderItem.org_id == rec.org_id,
        OrderItem.order_id == rec.order_id))).scalar_one_or_none()


async def approve_recommendation(db: AsyncSession, rec: Recommendation) -> OrderLine:
    """Shared by the public approve endpoints: mark approved + create the OrderLine.

    144be — the approved line runs the SAME price chain as a hand-added estimate line:
    the org's default tax stack is auto-applied (honouring tax-inclusive pricing and
    product/category exceptions) and the total is computed with ``_calc_line``, so an
    approved upsell can never disagree with the rest of the order's tax treatment.
    """
    from types import SimpleNamespace

    from backend.routers.orders import _auto_apply_tax, _calc_line
    line_type = "labour" if rec.kind == "service" else ("part" if rec.kind == "product" else "fee")
    shim = SimpleNamespace(
        qty=rec.qty or 1.0, unit_price=rec.unit_price or 0.0,
        discount=0.0, discount_type="pct",
        kind=rec.kind, line_type=line_type, ref_id=rec.ref_id,
        tax_id=None, tax_rate=0.0, tax_mode="imposed")
    try:
        await _auto_apply_tax(db, rec.org_id, shim)
    except Exception:  # noqa: BLE001 — tax is best-effort; never block an approval
        pass
    total = _calc_line(shim)
    ol = OrderLine(
        order_id=rec.order_id, line_type=line_type, kind=rec.kind, ref_id=rec.ref_id,
        description=rec.title, qty=rec.qty or 1.0, unit_price=rec.unit_price or 0.0,
        discount=0.0, discount_type="pct",
        tax_id=shim.tax_id, tax_rate=shim.tax_rate or 0.0, tax_mode=shim.tax_mode or "imposed",
        # 144bl: attribute the upsell to whoever recommended it, so the existing
        # per-line commission engine pays them when the order closes.
        technician_id=rec.author_id,
        order_item_id=await _live_item(db, rec),
        total=total)
    db.add(ol)
    await db.flush()
    rec.status = "approved"
    rec.order_line_id = ol.id
    rec.decided_at = datetime.utcnow()
    return ol
