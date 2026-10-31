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
from backend.models import Employee, Order, OrderLine, Organisation, Recommendation
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
    delivery = None
    if r.status == "draft":
        r.status = "sent"
        r.published_at = datetime.utcnow()
        delivery = await _tell_customer(db, current, order_id, 1)
        await db.commit()
        await db.refresh(r)
    return {**serialize_rec(r), "delivery": delivery}


async def _tell_customer(db, current, order_id: str, n: int) -> dict:
    """R1 — newly published recommendations: send the customer the link to their page."""
    from backend.models import Client
    from backend.services.customer_messages import send_to_customer, track_link
    o = await _order(db, current.org_id, order_id)
    if not o.public_token:
        from backend.routers.orders import unique_public_token
        o.public_token = await unique_public_token(db)
    client = (await db.execute(select(Client).where(
        Client.id == o.client_id, Client.org_id == current.org_id))).scalar_one_or_none() if o.client_id else None
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    what = "a recommendation" if n == 1 else f"{n} recommendations"
    body = (f"{org.name if org else 'Your repair shop'} has {what} for your job {o.order_number}. "
            f"Take a look and choose what you'd like done: {track_link(o)}")
    return await send_to_customer(db, org_id=current.org_id, client=client, body=body,
                                  subject=f"Recommended work for {o.order_number}",
                                  order_id=o.id, trigger_event="recommendation_published", saved=False)


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
    delivery = await _tell_customer(db, current, order_id, len(rows)) if rows else None
    await db.commit()
    return {"published": len(rows), "delivery": delivery}


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
    if r.status == "approved":
        # The approved work is already on the job (and maybe invoiced); deleting the
        # recommendation would leave that line with no record of who agreed to it.
        raise HTTPException(409, "The customer already approved this. Remove the line from the job instead.")
    await db.delete(r)
    await db.commit()


class ApproveForCustomerIn(BaseModel):
    how: str = "phone"            # phone | in_person
    customer_name: Optional[str] = None


@router.post("/{order_id}/recommendations/{rec_id}/approve-for-customer")
async def approve_for_customer(
    order_id: str,
    rec_id: str,
    data: ApproveForCustomerIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_approve_rec_for_customer")),
):
    """The customer said yes on the phone or at the counter: staff record it. Logged with who
    took the approval and how, so it never reads as the customer clicking it themselves."""
    if data.how not in ("phone", "in_person"):
        raise HTTPException(422, "how must be phone|in_person")
    order = await _order(db, current.org_id, order_id)
    r = (await db.execute(select(Recommendation).where(
        Recommendation.id == rec_id, Recommendation.org_id == current.org_id,
        Recommendation.order_id == order_id).with_for_update())).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Recommendation not found")
    if r.status not in ("draft", "sent", "declined"):
        raise HTTPException(409, "This recommendation was already approved")
    line = await approve_recommendation(db, r)
    who = (data.customer_name or "").strip()[:120] or "Customer"
    r.decided_by_name = f"{who}, {'by phone' if data.how == 'phone' else 'in person'} (taken by {current.name})"[:200]
    r.decline_reason = None
    r.remind_after = None
    prev_total = float(order.total or 0)
    order.total = q2(prev_total + float(line.total or 0))
    from backend.services.invoice_money import sync_order_invoices
    await sync_order_invoices(db, order, prev_total)    # a draft invoice follows the job
    from backend.services import audit
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="rec.approve_for_customer",
                       entity_type="recommendation", entity_id=r.id, diff={"how": data.how, "name": who})
    await db.commit()
    await db.refresh(r)
    return serialize_rec(r)


class ReplyIn(BaseModel):
    body: str
    channel: Optional[str] = None   # auto (default) | email | sms


@router.post("/{order_id}/customer-reply", status_code=201)
async def customer_reply(
    order_id: str,
    data: ReplyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_reply_customer")),
):
    """Build 144be — staff replies to the customer from the job's activity box. Same path as the
    conversation box (orders.post_customer_message): one thread, really sent, honest result.
    Gated by the org toggle ``oe_allow_staff_reply`` and the ``can_reply_customer`` permission."""
    from backend.routers.orders import post_customer_message
    return await post_customer_message(db, current, order_id, data.body, data.channel)


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

    from backend.routers.orders import _auto_apply_tax, _calc_line, _catalog_warranty
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
        warranty_days=await _catalog_warranty(db, rec.org_id, rec.kind, rec.ref_id),   # G2: catalog warranty
        total=total)
    db.add(ol)
    await db.flush()
    rec.status = "approved"
    rec.order_line_id = ol.id
    rec.decided_at = datetime.utcnow()
    return ol
