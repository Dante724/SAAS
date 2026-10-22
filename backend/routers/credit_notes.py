"""opb — credit notes: /api/finance/credit-notes CRUD + /issue + /cancel.

Drafts are created, edited and deleted with "Create and edit draft credit notes"; issuing and
canceling (which move money) need "Issue and cancel credit notes". The posting rules live in
services/credit_notes.py.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core import paging as _paging
from backend.db.database import get_db
from backend.models import Client, CreditNote, Employee, Invoice
from backend.services import credit_notes as svc
from backend.services.auth import assert_perm, record_scope, require_perm
from backend.services.invoicing import VOID_STATUSES

router = APIRouter(prefix="/api/finance/credit-notes", tags=["credit-notes"],
                   dependencies=[Depends(record_scope)])


class CreditNoteLineIn(BaseModel):
    description: str = Field("", max_length=500)
    qty: float = Field(1, gt=0, le=1_000_000)
    unit_price: float = Field(0, ge=0, le=100_000_000)
    tax_rate: float = Field(0, ge=0, le=100)


class CreditNoteIn(BaseModel):
    client_id: Optional[str] = None
    invoice_id: Optional[str] = None
    reason: str = Field("", max_length=500)
    lines: list[CreditNoteLineIn] = Field(default_factory=list, max_length=200)

    @field_validator("lines")
    @classmethod
    def _described(cls, v):
        if any(not (l.description or "").strip() for l in v):
            raise ValueError("Every line needs a description")
        return v


def ser(cn: CreditNote, *, client_name: Optional[str] = None, invoice_number: Optional[str] = None,
        lines: bool = False) -> dict:
    out = {
        "id": cn.id, "number": cn.number, "status": cn.status, "reason": cn.reason or "",
        "client_id": cn.client_id, "client_name": client_name,
        "invoice_id": cn.invoice_id, "invoice_number": invoice_number,
        "subtotal": cn.subtotal, "tax_total": cn.tax_total, "total": cn.total,
        "issued_at": cn.issued_at.isoformat() if cn.issued_at else None,
        "canceled_at": cn.canceled_at.isoformat() if cn.canceled_at else None,
        "created_at": cn.created_at.isoformat() if cn.created_at else None,
    }
    if lines:
        out["lines"] = [{"id": l.id, "description": l.description, "qty": l.qty, "unit_price": l.unit_price,
                         "tax_rate": l.tax_rate, "net": l.net, "tax": l.tax, "total": l.total}
                        for l in cn.lines]
    return out


async def _load(db: AsyncSession, org_id: str, cn_id: str, *, lock: bool = False) -> CreditNote:
    q = (select(CreditNote).where(CreditNote.id == cn_id, CreditNote.org_id == org_id)
         .options(selectinload(CreditNote.lines)))
    if lock:
        q = q.with_for_update(of=CreditNote)
    cn = (await db.execute(q)).scalar_one_or_none()
    if not cn:
        raise HTTPException(404, "Credit note not found")
    return cn


async def _detail(db: AsyncSession, cn: CreditNote) -> dict:
    name = (await db.execute(select(Client.name).where(Client.id == cn.client_id))).scalar_one_or_none() \
        if cn.client_id else None
    number = (await db.execute(select(Invoice.invoice_number).where(Invoice.id == cn.invoice_id))).scalar_one_or_none() \
        if cn.invoice_id else None
    return ser(cn, client_name=name, invoice_number=number, lines=True)


async def _apply(db: AsyncSession, org_id: str, cn: CreditNote, data: CreditNoteIn) -> None:
    """Validate the client / invoice link and write a draft's fields and lines."""
    client_id = data.client_id or None
    if data.invoice_id:
        inv = (await db.execute(select(Invoice).where(
            Invoice.id == data.invoice_id, Invoice.org_id == org_id))).scalar_one_or_none()
        if not inv:
            raise HTTPException(404, "Invoice not found")
        if (inv.status or "") in VOID_STATUSES:
            raise HTTPException(409, "That invoice was cancelled, so it can't be credited")
        # The credit belongs to whoever the invoice was raised for (that's whose ledger it charged).
        if client_id and inv.client_id and client_id != inv.client_id:
            raise HTTPException(422, "The client must match the invoice's client")
        client_id = inv.client_id or client_id
    if client_id:
        ok = (await db.execute(select(Client.id).where(
            Client.id == client_id, Client.org_id == org_id))).scalar_one_or_none()
        if not ok:
            raise HTTPException(404, "Client not found")
    if not client_id and not data.invoice_id:
        raise HTTPException(422, "Pick a client or an invoice for this credit note")
    cn.client_id = client_id
    cn.invoice_id = data.invoice_id or None
    cn.reason = (data.reason or "").strip()[:500]
    svc.build_lines(cn, [l.model_dump() for l in data.lines])


@router.get("")
async def list_credit_notes(
    status: Optional[str] = None, client_id: Optional[str] = None, invoice_id: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = _paging.skip_query(), limit: int = _paging.limit_query(50),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_credit_notes")),
):
    limit = _paging.clamp(limit)
    conds = [CreditNote.org_id == current.org_id]
    if status:
        conds.append(CreditNote.status == status)
    if client_id:
        conds.append(CreditNote.client_id == client_id)
    if invoice_id:
        conds.append(CreditNote.invoice_id == invoice_id)
    if (search or "").strip():
        s = search.strip()
        conds.append(or_(
            CreditNote.number.icontains(s), CreditNote.reason.icontains(s),
            CreditNote.client_id.in_(select(Client.id).where(Client.org_id == current.org_id, Client.name.icontains(s))),
            CreditNote.invoice_id.in_(select(Invoice.id).where(Invoice.org_id == current.org_id,
                                                               Invoice.invoice_number.icontains(s)))))
    rows = (await db.execute(select(CreditNote).where(*conds)
                             .order_by(CreditNote.created_at.desc()).offset(skip).limit(limit))).scalars().all()
    total = (await db.execute(select(func.count()).select_from(CreditNote).where(*conds))).scalar() or 0
    cids = {r.client_id for r in rows if r.client_id}
    iids = {r.invoice_id for r in rows if r.invoice_id}
    names = dict((await db.execute(select(Client.id, Client.name).where(
        Client.org_id == current.org_id, Client.id.in_(cids)))).all()) if cids else {}
    numbers = dict((await db.execute(select(Invoice.id, Invoice.invoice_number).where(
        Invoice.org_id == current.org_id, Invoice.id.in_(iids)))).all()) if iids else {}
    return {"total": total, "skip": skip, "limit": limit,
            "items": [ser(r, client_name=names.get(r.client_id), invoice_number=numbers.get(r.invoice_id))
                      for r in rows]}


@router.get("/{cn_id}")
async def get_credit_note(
    cn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_credit_notes")),
):
    return await _detail(db, await _load(db, current.org_id, cn_id))


@router.post("", status_code=201)
async def create_credit_note(
    data: CreditNoteIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_credit_notes")),
):
    cn = CreditNote(org_id=current.org_id, status="draft", created_by_id=current.id, lines=[])
    await _apply(db, current.org_id, cn, data)
    db.add(cn)
    from backend.services import audit
    await db.flush()
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="credit_note.create",
                       entity_type="credit_note", entity_id=cn.id, diff={"total": cn.total})
    await db.commit()
    return await _detail(db, await _load(db, current.org_id, cn.id))


@router.put("/{cn_id}")
async def update_credit_note(
    cn_id: str, data: CreditNoteIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_credit_notes")),
):
    cn = await _load(db, current.org_id, cn_id, lock=True)
    if cn.status != "draft":
        raise HTTPException(409, "An issued credit note is locked. Cancel it and create a new one.")
    await _apply(db, current.org_id, cn, data)
    await db.commit()
    return await _detail(db, await _load(db, current.org_id, cn.id))


@router.delete("/{cn_id}")
async def delete_credit_note(
    cn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_credit_notes")),
):
    """Delete a draft. Issued notes carry a number and moved money, so they're canceled instead."""
    cn = await _load(db, current.org_id, cn_id, lock=True)
    if cn.status != "draft":
        raise HTTPException(409, "Only a draft can be deleted. Cancel an issued credit note instead.")
    await db.delete(cn)
    await db.commit()
    return {"ok": True}


@router.post("/{cn_id}/issue")
async def issue_credit_note(
    cn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_issue_credit_notes")),
):
    cn = await _load(db, current.org_id, cn_id, lock=True)
    if not cn.invoice_id:
        # A standalone note mints client credit with no invoice to cap it — the same power as
        # putting money on the client's balance.
        await assert_perm(db, current, "can_make_client_payments")
    await svc.issue(db, cn, current.id)
    from backend.services import audit
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="credit_note.issue",
                       entity_type="credit_note", entity_id=cn.id, diff={"number": cn.number, "total": cn.total})
    await db.commit()
    return await _detail(db, await _load(db, current.org_id, cn.id))


@router.post("/{cn_id}/cancel")
async def cancel_credit_note(
    cn_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_issue_credit_notes")),
):
    cn = await _load(db, current.org_id, cn_id, lock=True)
    await svc.cancel(db, cn, current.id)
    from backend.services import audit
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="credit_note.cancel",
                       entity_type="credit_note", entity_id=cn.id, diff={"number": cn.number, "total": cn.total})
    await db.commit()
    return await _detail(db, await _load(db, current.org_id, cn.id))
