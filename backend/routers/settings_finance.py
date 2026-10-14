"""Settings: numbering engine, legal entities, tax depth (Phases 1 & 2)."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    Employee,
    LegalEntity,
    NumberSequence,
    TaxException,
    TaxGroup,
    TaxRule,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services.numbering import DEFAULT_TEMPLATES, peek_example

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── Numbering ────────────────────────────────────────────────────────────────
class NumberingIn(BaseModel):
    doc_type: str
    template: str
    begins_from: int = 1
    scope_key: str = "org"


@router.get("/numbering")
async def list_numbering(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(NumberSequence).where(
        NumberSequence.org_id == current.org_id))).scalars().all()
    configured = {(r.doc_type, r.scope_key): r for r in rows}
    out = []
    for dt, default in DEFAULT_TEMPLATES.items():
        r = configured.get((dt, "org"))
        tmpl = r.template if r else default
        out.append({"doc_type": dt, "scope_key": "org", "template": tmpl,
                    "next_value": r.next_value if r else 1,
                    "example": await peek_example(tmpl, r.next_value if r else 1),
                    "is_default": r is None})
    # include any non-org scopes too
    for r in rows:
        if r.scope_key != "org":
            out.append({"doc_type": r.doc_type, "scope_key": r.scope_key,
                        "template": r.template, "next_value": r.next_value,
                        "example": await peek_example(r.template, r.next_value),
                        "is_default": False})
    return {"items": out}


@router.put("/numbering")
async def set_numbering(
    data: NumberingIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.doc_type not in DEFAULT_TEMPLATES:
        raise HTTPException(422, f"Unknown doc_type. Valid: {sorted(DEFAULT_TEMPLATES)}")
    if "{C" not in data.template:
        raise HTTPException(422, "Template must contain a {C} counter token")
    seq = (await db.execute(select(NumberSequence).where(
        NumberSequence.org_id == current.org_id, NumberSequence.doc_type == data.doc_type,
        NumberSequence.scope_key == data.scope_key))).scalar_one_or_none()
    if seq:
        seq.template = data.template
        seq.next_value = data.begins_from
    else:
        seq = NumberSequence(org_id=current.org_id, doc_type=data.doc_type,
                             scope_key=data.scope_key, template=data.template,
                             next_value=data.begins_from)
        db.add(seq)
    await db.commit()
    return {"ok": True, "example": await peek_example(data.template, data.begins_from)}


@router.post("/numbering/preview")
async def preview_numbering(data: NumberingIn, current: Employee = Depends(get_current_employee)):
    return {"example": await peek_example(data.template, data.begins_from)}


# ── Legal entities ───────────────────────────────────────────────────────────
_LE_EXTRA = ["registration_no", "vat_no", "bank_name", "bank_account", "phone", "email", "director"]


class LegalEntityIn(BaseModel):
    name: str
    address: Optional[str] = None
    tax_id: Optional[str] = None
    details: Optional[str] = None
    registration_no: Optional[str] = None
    vat_no: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    director: Optional[str] = None
    is_default: bool = False


def _ser_entity(e: LegalEntity) -> dict:
    d = {"id": e.id, "name": e.name, "address": e.address, "tax_id": e.tax_id,
         "details": e.details, "is_default": e.is_default, "active": e.active}
    for c in _LE_EXTRA:
        d[c] = getattr(e, c, None)
    return d


@router.get("/legal-entities")
async def list_entities(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(LegalEntity).where(
        LegalEntity.org_id == current.org_id, LegalEntity.active == True)  # noqa: E712
        .order_by(LegalEntity.name))).scalars().all()
    return {"items": [_ser_entity(e) for e in rows]}


async def _clear_default_entities(db, org_id):
    for e in (await db.execute(select(LegalEntity).where(
            LegalEntity.org_id == org_id, LegalEntity.is_default == True))).scalars().all():  # noqa: E712
        e.is_default = False


@router.post("/legal-entities", status_code=201)
async def create_entity(
    data: LegalEntityIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if not data.name.strip():
        raise HTTPException(400, "Name is required")
    existing = (await db.execute(select(LegalEntity).where(
        LegalEntity.org_id == current.org_id, LegalEntity.active == True))).scalars().all()  # noqa: E712
    make_default = data.is_default or not existing   # first entity is the default
    if make_default:
        await _clear_default_entities(db, current.org_id)
    e = LegalEntity(org_id=current.org_id, name=data.name, address=data.address,
                    tax_id=data.tax_id, details=data.details, is_default=make_default,
                    **{c: getattr(data, c) for c in _LE_EXTRA})
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return _ser_entity(e)


@router.put("/legal-entities/{entity_id}")
async def update_entity(
    entity_id: str, data: LegalEntityIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    e = (await db.execute(select(LegalEntity).where(
        LegalEntity.id == entity_id, LegalEntity.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Legal entity not found")
    if data.is_default and not e.is_default:
        await _clear_default_entities(db, current.org_id)
    e.name, e.address, e.tax_id, e.details, e.is_default = (
        data.name, data.address, data.tax_id, data.details, data.is_default)
    for c in _LE_EXTRA:
        setattr(e, c, getattr(data, c))
    await db.commit()
    return _ser_entity(e)


@router.delete("/legal-entities/{entity_id}")
async def delete_entity(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    e = (await db.execute(select(LegalEntity).where(
        LegalEntity.id == entity_id, LegalEntity.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Legal entity not found")
    e.active = False
    await db.commit()
    return {"ok": True}


# ── Tax depth: groups, rules (ordered stack), exceptions ─────────────────────
class TaxGroupIn(BaseModel):
    name: str
    member_tax_ids: list[str] = []


@router.get("/tax-groups")
async def list_tax_groups(db: AsyncSession = Depends(get_db),
                          current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(TaxGroup).where(TaxGroup.org_id == current.org_id))).scalars().all()
    return {"items": [{"id": g.id, "name": g.name,
                       "member_tax_ids": json.loads(g.member_tax_ids or "[]")} for g in rows]}


@router.post("/tax-groups", status_code=201)
async def create_tax_group(data: TaxGroupIn, db: AsyncSession = Depends(get_db),
                           current: Employee = Depends(require_perm("can_manage_settings"))):
    g = TaxGroup(org_id=current.org_id, name=data.name,
                 member_tax_ids=json.dumps(data.member_tax_ids))
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return {"id": g.id}


class TaxRuleIn(BaseModel):
    doc_family: str        # tickets_sales|posting
    tax_ids: list[str]     # ordered; index = priority


@router.get("/tax-rules")
async def list_tax_rules(db: AsyncSession = Depends(get_db),
                         current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(TaxRule).where(TaxRule.org_id == current.org_id)
                             .order_by(TaxRule.doc_family, TaxRule.priority))).scalars().all()
    out: dict = {}
    for r in rows:
        out.setdefault(r.doc_family, []).append(r.tax_id)
    return {"stacks": out}


@router.put("/tax-rules")
async def set_tax_rules(data: TaxRuleIn, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_manage_settings"))):
    if data.doc_family not in ("tickets_sales", "posting"):
        raise HTTPException(422, "doc_family must be tickets_sales|posting")
    # replace the stack for this family
    for r in (await db.execute(select(TaxRule).where(
            TaxRule.org_id == current.org_id, TaxRule.doc_family == data.doc_family))).scalars().all():
        await db.delete(r)
    for i, tid in enumerate(data.tax_ids):
        db.add(TaxRule(org_id=current.org_id, doc_family=data.doc_family, tax_id=tid, priority=i))
    await db.commit()
    return {"ok": True, "count": len(data.tax_ids)}


class TaxExceptionIn(BaseModel):
    doc_family: str
    target_type: str       # labor|service|product|category
    target_id: str
    tax_id: Optional[str] = None
    exempt: bool = False
    taxation_system: Optional[str] = None


@router.get("/tax-exceptions")
async def list_tax_exceptions(db: AsyncSession = Depends(get_db),
                              current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(TaxException).where(
        TaxException.org_id == current.org_id))).scalars().all()
    return {"items": [{"id": e.id, "doc_family": e.doc_family, "target_type": e.target_type,
                       "target_id": e.target_id, "tax_id": e.tax_id, "exempt": e.exempt,
                       "taxation_system": e.taxation_system} for e in rows]}


@router.post("/tax-exceptions", status_code=201)
async def create_tax_exception(data: TaxExceptionIn, db: AsyncSession = Depends(get_db),
                               current: Employee = Depends(require_perm("can_manage_settings"))):
    if not data.exempt and not data.tax_id:
        raise HTTPException(422, "Provide tax_id or set exempt=true")
    e = TaxException(org_id=current.org_id, doc_family=data.doc_family,
                     target_type=data.target_type, target_id=data.target_id,
                     tax_id=data.tax_id, exempt=data.exempt, taxation_system=data.taxation_system)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return {"id": e.id}


@router.delete("/tax-exceptions/{exc_id}")
async def delete_tax_exception(exc_id: str, db: AsyncSession = Depends(get_db),
                               current: Employee = Depends(require_perm("can_manage_settings"))):
    e = (await db.execute(select(TaxException).where(
        TaxException.id == exc_id, TaxException.org_id == current.org_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Exception not found")
    await db.delete(e)
    await db.commit()
    return {"ok": True}
