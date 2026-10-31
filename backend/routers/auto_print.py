"""WS-C #20 — auto-print the asset label when a job is created.

A label template (doc_type in the label family, body using {Asset …} variables) whose
`auto_print_on` is "create" is printed for the job's device(s) right after the job is saved.
The web app calls this once the create succeeds and opens the browser print dialog with the
returned HTML; nothing prints when no template is set to auto-print.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Device, DocumentTemplate, Employee, Order, OrderItem, Organisation
from backend.services.auth import get_current_employee
from backend.services.leads import own

router = APIRouter(prefix="/api/auto-print", tags=["printing"])


@router.get("/order-created/{order_id}")
async def order_created(order_id: str, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    from backend.routers.documents import LABEL_DOC_TYPES, _labels_html, _render, _resolve_asset, auto_events
    from backend.services.scoping import can_view_order
    order = await own(db, Order, order_id, current.org_id, "Job")
    if not await can_view_order(db, current, order):
        return {"items": []}
    tpls = (await db.execute(select(DocumentTemplate).where(
        DocumentTemplate.org_id == current.org_id, DocumentTemplate.auto_print_on.isnot(None),
        DocumentTemplate.doc_type.in_(LABEL_DOC_TYPES), DocumentTemplate.active == True)  # noqa: E712
        .order_by(DocumentTemplate.name))).scalars().all()
    tpls = [t for t in tpls if "create" in auto_events(t) and "{Asset " in (t.body_html or "")]
    if not tpls:
        return {"items": []}
    ids = [order.device_id] if order.device_id else []
    ids += [a for a in (await db.execute(select(OrderItem.asset_id).where(
        OrderItem.order_id == order.id, OrderItem.org_id == current.org_id,
        OrderItem.asset_id.is_not(None)))).scalars().all() if a not in ids]
    if not ids:
        return {"items": []}
    assets = (await db.execute(select(Device).where(Device.id.in_(ids), Device.org_id == current.org_id))).scalars().all()
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    out = []
    for t in tpls:
        tiles = [f"<div class='lbl'>{_render(t.body_html, await _resolve_asset(db, org, a), [])}</div>" for a in assets]
        out.append({"template_id": t.id, "name": t.name, "count": len(tiles), "html": _labels_html(t, tiles)})
    return {"items": out}
