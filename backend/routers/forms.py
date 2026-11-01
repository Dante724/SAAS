"""
Forms Editor — configure work-order types and their custom fields.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete
from pydantic import BaseModel
from typing import Optional, List, Union
from backend.db.database import get_db
from backend.models import WorkOrderType, CustomField, Employee, FormLayout
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/forms", tags=["forms-editor"])

VALID_ENTITIES = {"order", "invoice", "contact", "product", "vehicle", "client", "device", "lead"}


class LayoutIn(BaseModel):
    entity: str
    type_id: Optional[str] = None
    fields: List[dict] = []   # [{key, visible, required, sort}]


@router.get("/system-layout")
async def get_system_layout(
    entity: str,
    type_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Return the saved system-field layout for an entity (+ optional order type).
    Empty list means 'use the catalog defaults' (the frontend owns the catalog)."""
    if entity not in VALID_ENTITIES:
        raise HTTPException(400, "Unknown entity")
    row = (await db.execute(
        select(FormLayout).where(
            FormLayout.org_id == current.org_id,
            FormLayout.entity == entity,
            FormLayout.type_id.is_(None) if not type_id else FormLayout.type_id == type_id,
        )
    )).scalars().first()
    return {"entity": entity, "type_id": type_id, "fields": (row.data if row and row.data else [])}


@router.put("/system-layout")
async def put_system_layout(
    data: LayoutIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.entity not in VALID_ENTITIES:
        raise HTTPException(400, "Unknown entity")
    row = (await db.execute(
        select(FormLayout).where(
            FormLayout.org_id == current.org_id,
            FormLayout.entity == data.entity,
            FormLayout.type_id.is_(None) if not data.type_id else FormLayout.type_id == data.type_id,
        )
    )).scalars().first()
    if not row:
        row = FormLayout(org_id=current.org_id, entity=data.entity, type_id=data.type_id or None)
        db.add(row)
    row.data = data.fields or []
    await db.commit()
    return {"ok": True, "count": len(row.data)}

VALID_FIELD_TYPES = {"text", "textarea", "number", "select", "multiselect", "link", "date", "datetime", "address", "bool", "header"}


# ── Schemas ──────────────────────────────────

class TypeIn(BaseModel):
    name: str
    color: str = "#4f8ef7"
    initial_status: str = "new"
    default_asset_type: str = "Device"
    is_default: bool = False
    sched_default_duration: int = 60
    sched_required: bool = False
    # Closing gates (J1). Omitted on an update = keep what's set.
    requires_signature: Optional[bool] = None
    requires_checklist: Optional[bool] = None


SHOW_OPS = {"is", "is_not", "gt", "lt", "filled"}


class FieldIn(BaseModel):
    field_label: str
    field_type: str
    required: bool = False
    lock_after_create: bool = False
    show_on_print: bool = False
    show_on_job_card: Optional[bool] = None   # None = leave as is (older editors don't send it)
    show_in_portal: Optional[bool] = None
    ask_at_checkin: Optional[bool] = None   # self check-in: ask the customer this field
    autofill_source: Optional[str] = None
    placeholder: Optional[str] = None
    options: List[str] = []
    directory_key: Optional[str] = None   # 144hi — bind select options to a Directory
    parent_field_key: Optional[str] = None  # 144hj — cascade: filter to children of this field's value
    # 144gp — conditional visibility
    show_when_field_key: Optional[str] = None
    show_when_op: Optional[str] = None
    show_when_value: Optional[str] = None
    # 144gp Phase B — upsell action rule
    upsell_ref_id: Optional[str] = None
    upsell_kind: Optional[str] = None
    upsell_price: Optional[float] = None
    upsell_comparator: Optional[str] = None
    upsell_value: Optional[str] = None
    upsell_threshold: Optional[float] = None
    # 144gr — field-gates-status
    gate_status_key: Optional[str] = None
    gate_comparator: Optional[str] = None
    gate_value: Optional[str] = None
    gate_threshold: Optional[float] = None


class FieldReorder(BaseModel):
    ordered_ids: List[str]


ENTITY_TYPES = {"client", "device", "product", "lead"}   # lead = inquiries (WS-C)


class EntityFieldIn(BaseModel):
    entity_type: str
    field_label: str
    field_type: str
    required: bool = False
    lock_after_create: bool = False
    show_on_print: bool = False
    show_on_job_card: Optional[bool] = None   # None = leave as is (older editors don't send it)
    show_in_portal: Optional[bool] = None
    ask_at_checkin: Optional[bool] = None   # self check-in: ask the customer this field
    autofill_source: Optional[str] = None
    placeholder: Optional[str] = None
    options: List[str] = []
    directory_key: Optional[str] = None   # 144hi — bind select options to a Directory
    parent_field_key: Optional[str] = None  # 144hj — cascade
    # 144gp — conditional visibility
    show_when_field_key: Optional[str] = None
    show_when_op: Optional[str] = None
    show_when_value: Optional[str] = None


# ── Helpers ──────────────────────────────────

def _slug(label: str) -> str:
    base = "".join(c.lower() if c.isalnum() else "_" for c in label).strip("_")
    return base or "field"

def _ser_type(t: WorkOrderType) -> dict:
    return {
        "id": t.id, "name": t.name, "color": t.color,
        "initial_status": t.initial_status, "default_asset_type": t.default_asset_type, "sort_order": t.sort_order,
        "is_default": t.is_default, "active": t.active,
        "sched_default_duration": t.sched_default_duration, "sched_required": t.sched_required,
        "requires_signature": bool(t.requires_signature), "requires_checklist": bool(t.requires_checklist),
    }

def _ser_field(f: CustomField) -> dict:
    return {
        "id": f.id, "type_id": f.type_id, "field_key": f.field_key,
        "field_label": f.field_label, "field_type": f.field_type,
        "required": f.required, "lock_after_create": getattr(f, "lock_after_create", False),
        "show_on_print": getattr(f, "show_on_print", False),
        "show_on_job_card": getattr(f, "show_on_job_card", True) is not False,
        "show_in_portal": bool(getattr(f, "show_in_portal", False)),
        "ask_at_checkin": bool(getattr(f, "ask_at_checkin", False)),
        "autofill_source": getattr(f, "autofill_source", None),
        "placeholder": f.placeholder,
        "options": f.options or [], "directory_key": getattr(f, "directory_key", None),
        "parent_field_key": getattr(f, "parent_field_key", None), "sort_order": f.sort_order,
        "show_when_field_key": getattr(f, "show_when_field_key", None),
        "show_when_op": getattr(f, "show_when_op", None),
        "show_when_value": getattr(f, "show_when_value", None),
        "upsell_ref_id": getattr(f, "upsell_ref_id", None),
        "upsell_kind": getattr(f, "upsell_kind", None),
        "upsell_price": getattr(f, "upsell_price", None),
        "upsell_comparator": getattr(f, "upsell_comparator", None),
        "upsell_value": getattr(f, "upsell_value", None),
        "upsell_threshold": getattr(f, "upsell_threshold", None),
        "gate_status_key": getattr(f, "gate_status_key", None),
        "gate_comparator": getattr(f, "gate_comparator", None),
        "gate_value": getattr(f, "gate_value", None),
        "gate_threshold": getattr(f, "gate_threshold", None),
    }


UPSELL_OPS = {"is", "is_not", "lt", "gt"}


def _apply_shows_on(f: CustomField, data) -> None:
    if getattr(data, "show_on_job_card", None) is not None:
        f.show_on_job_card = data.show_on_job_card
    elif f.show_on_job_card is None:
        f.show_on_job_card = True
    if getattr(data, "show_in_portal", None) is not None:
        f.show_in_portal = data.show_in_portal
    elif f.show_in_portal is None:
        f.show_in_portal = False
    if getattr(data, "ask_at_checkin", None) is not None:
        f.ask_at_checkin = data.ask_at_checkin
    elif f.ask_at_checkin is None:
        f.ask_at_checkin = False


def _apply_upsell_cfg(f: CustomField, data) -> None:
    """Copy the upsell + gate action-rule fields from a payload onto a CustomField.
    Tolerant of payloads without them (entity fields don't carry the rules in v1)."""
    comp = getattr(data, "upsell_comparator", None)
    f.upsell_ref_id = getattr(data, "upsell_ref_id", None) or None
    f.upsell_kind = getattr(data, "upsell_kind", None) or None
    f.upsell_price = getattr(data, "upsell_price", None)
    f.upsell_comparator = comp if comp in UPSELL_OPS else None
    f.upsell_value = getattr(data, "upsell_value", None)
    f.upsell_threshold = getattr(data, "upsell_threshold", None)
    gcomp = getattr(data, "gate_comparator", None)
    f.gate_status_key = getattr(data, "gate_status_key", None) or None
    f.gate_comparator = gcomp if gcomp in UPSELL_OPS else None
    f.gate_value = getattr(data, "gate_value", None)
    f.gate_threshold = getattr(data, "gate_threshold", None)


async def status_gate_block(db, org_id: str, order, target_key: str) -> Optional[str]:
    """144gr — is any order-type field configured to block entering `target_key`,
    and does its current value trigger the gate? Returns a message if blocked."""
    if not order.type_id:
        return None
    fields = (await db.execute(select(CustomField).where(
        CustomField.org_id == org_id, CustomField.type_id == order.type_id,
        CustomField.entity_type == "order", CustomField.gate_status_key == target_key))).scalars().all()
    if not fields:
        return None
    from backend.services.field_rules import numeric_rule_triggered
    values = order.custom_data or {}
    for f in fields:
        if not f.gate_comparator:
            continue
        if numeric_rule_triggered(f.gate_comparator, f.gate_threshold, values.get(f.field_key), f.gate_value):
            return f"'{f.field_label}' blocks entering this status — resolve it first."
    return None


def _clean_show(op: Optional[str]) -> Optional[str]:
    return op if op in SHOW_OPS else None


def _field_visible(f: CustomField, values: dict) -> bool:
    """Evaluate a field's conditional show-rule against the current form values.
    No rule → always visible. Controlling key absent → hidden (safe: never blocks)."""
    key = getattr(f, "show_when_field_key", None)
    op = getattr(f, "show_when_op", None)
    if not key or op not in SHOW_OPS:
        return True
    cur = values.get(key)
    want = getattr(f, "show_when_value", None)
    if op == "filled":
        return not _is_empty(cur)
    if _is_empty(cur):
        return False
    if op == "is":
        return str(cur) == str(want)
    if op == "is_not":
        return str(cur) != str(want)
    try:
        n, w = float(cur), float(want)
    except (TypeError, ValueError):
        return False
    return n > w if op == "gt" else n < w


async def load_entity_fields(db, org_id: str, entity: str):
    """Custom fields configured for an entity (client / device / product)."""
    return (await db.execute(select(CustomField).where(
        CustomField.org_id == org_id, CustomField.type_id.is_(None),
        CustomField.entity_type == entity).order_by(CustomField.sort_order))).scalars().all()


def _is_empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


async def resolve_custom_data(db, org_id: str, entity: str, incoming: Optional[dict],
                              existing: Optional[dict] = None, creating: bool = False) -> dict:
    """Merge incoming custom-field values over any stored ones, enforcing two rules:
    - required fields must carry a non-empty value (raises 422 otherwise);
    - lock_after_create fields keep their stored value once set (edits are ignored).
    Returns the resolved dict to persist."""
    fields = await load_entity_fields(db, org_id, entity)
    incoming = dict(incoming or {})
    existing = dict(existing or {})
    out = dict(existing)
    # First pass: merge values so visibility can be evaluated against the full set.
    for f in fields:
        if f.field_type == "header":
            continue
        k = f.field_key
        if getattr(f, "lock_after_create", False) and not creating and not _is_empty(existing.get(k)):
            out[k] = existing.get(k)          # locked & already set → immutable
        elif k in incoming:
            out[k] = incoming[k]
    # Second pass: a hidden field (its show-rule isn't met) is neither required nor
    # stored — conditional visibility must never block a submit (Smart Fields Phase A).
    for f in fields:
        if f.field_type == "header":
            continue
        k = f.field_key
        if not _field_visible(f, out):
            out.pop(k, None)
            continue
        if f.required and f.field_type != "bool" and _is_empty(out.get(k)):
            raise HTTPException(422, f"'{f.field_label}' is required")
    return out


async def sync_order_field_upsells(db, org_id: str, order, actor=None) -> None:
    """144gp Phase B — evaluate order-type custom fields' upsell rules against the
    order's custom_data and draft/retract recommendations through the shared surface.
    A hidden field (its show-rule unmet) never fires."""
    from backend.services.field_rules import numeric_rule_triggered, sync_upsell_draft
    if not order.type_id:
        return
    fields = (await db.execute(select(CustomField).where(
        CustomField.org_id == org_id, CustomField.type_id == order.type_id,
        CustomField.entity_type == "order"))).scalars().all()
    values = order.custom_data or {}
    for f in fields:
        if not (f.upsell_ref_id or f.upsell_price is not None) or not f.upsell_comparator:
            continue
        if not _field_visible(f, values):
            triggered = False
        else:
            triggered = numeric_rule_triggered(f.upsell_comparator, f.upsell_threshold,
                                               values.get(f.field_key), f.upsell_value)
        await sync_upsell_draft(
            db, org_id=org_id, order_id=order.id, source="field", source_ref_id=f.id,
            triggered=triggered,
            title=f"Recommended: {f.field_label}",
            note=f"From field: {f.field_label}",
            kind=(f.upsell_kind or "custom"), ref_id=f.upsell_ref_id, price=f.upsell_price or 0.0,
            actor_id=getattr(actor, "id", None), actor_name=getattr(actor, "name", None))


# ── Work order types ─────────────────────────

@router.get("/types")
async def list_types(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(
        select(WorkOrderType)
        .where(WorkOrderType.org_id == current.org_id, WorkOrderType.active == True)  # noqa: E712
        .order_by(WorkOrderType.sort_order, WorkOrderType.name)
    )
    types = result.scalars().all()
    # field counts, one grouped query for every type
    counts = dict((await db.execute(
        select(CustomField.type_id, func.count()).where(CustomField.type_id.in_([t.id for t in types]))
        .group_by(CustomField.type_id))).all()) if types else {}
    out = []
    for t in types:
        d = _ser_type(t)
        d["field_count"] = counts.get(t.id, 0)
        out.append(d)
    return {"items": out}


@router.post("/types", status_code=201)
async def create_type(
    data: TypeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    count = (await db.execute(
        select(func.count()).select_from(WorkOrderType).where(WorkOrderType.org_id == current.org_id)
    )).scalar() or 0
    if data.is_default:
        # clear other defaults
        existing = (await db.execute(
            select(WorkOrderType).where(WorkOrderType.org_id == current.org_id, WorkOrderType.is_default == True)  # noqa: E712
        )).scalars().all()
        for e in existing:
            e.is_default = False
    t = WorkOrderType(
        org_id=current.org_id, name=data.name, color=data.color,
        initial_status=data.initial_status, default_asset_type=data.default_asset_type,
        is_default=data.is_default, sort_order=count,
        sched_default_duration=data.sched_default_duration, sched_required=data.sched_required,
        requires_signature=bool(data.requires_signature), requires_checklist=bool(data.requires_checklist),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _ser_type(t)


@router.put("/types/{type_id}")
async def update_type(
    type_id: str, data: TypeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    result = await db.execute(select(WorkOrderType).where(WorkOrderType.id == type_id, WorkOrderType.org_id == current.org_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Type not found")
    if data.is_default and not t.is_default:
        existing = (await db.execute(
            select(WorkOrderType).where(WorkOrderType.org_id == current.org_id, WorkOrderType.is_default == True)  # noqa: E712
        )).scalars().all()
        for e in existing:
            e.is_default = False
    t.name = data.name
    t.color = data.color
    t.initial_status = data.initial_status
    t.default_asset_type = data.default_asset_type
    t.is_default = data.is_default
    t.sched_default_duration = data.sched_default_duration
    t.sched_required = data.sched_required
    if data.requires_signature is not None:
        t.requires_signature = data.requires_signature
    if data.requires_checklist is not None:
        t.requires_checklist = data.requires_checklist
    await db.commit()
    return _ser_type(t)


@router.delete("/types/{type_id}")
async def delete_type(
    type_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    result = await db.execute(select(WorkOrderType).where(WorkOrderType.id == type_id, WorkOrderType.org_id == current.org_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Type not found")
    # soft-delete the type, hard-delete its fields
    await db.execute(sa_delete(CustomField).where(CustomField.type_id == type_id))
    t.active = False
    await db.commit()
    return {"ok": True}


# ── Custom fields for a type ─────────────────

@router.get("/types/{type_id}/fields")
async def list_fields(
    type_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(
        select(CustomField)
        .where(CustomField.org_id == current.org_id, CustomField.type_id == type_id)
        .order_by(CustomField.sort_order)
    )
    hidden = await hidden_field_ids(db, current.org_id, "order", type_id)   # hidden in Customize
    return {"items": [_ser_field(f) for f in result.scalars().all() if f.id not in hidden]}


@router.post("/types/{type_id}/fields", status_code=201)
async def add_field(
    type_id: str, data: FieldIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.field_type not in VALID_FIELD_TYPES:
        raise HTTPException(400, "Invalid field type")
    # verify type belongs to org
    t = (await db.execute(select(WorkOrderType).where(WorkOrderType.id == type_id, WorkOrderType.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Type not found")
    count = (await db.execute(
        select(func.count()).select_from(CustomField).where(CustomField.type_id == type_id)
    )).scalar() or 0
    f = CustomField(
        org_id=current.org_id, type_id=type_id, entity_type="order",
        field_key=_slug(data.field_label) + "_" + str(count),
        field_label=data.field_label, field_type=data.field_type,
        required=data.required, lock_after_create=data.lock_after_create,
        show_on_print=getattr(data, "show_on_print", False),
        autofill_source=getattr(data, "autofill_source", None) or None,
        placeholder=data.placeholder,
        options=data.options, directory_key=getattr(data, "directory_key", None) or None,
        parent_field_key=getattr(data, "parent_field_key", None) or None, sort_order=count,
        show_when_field_key=data.show_when_field_key or None,
        show_when_op=_clean_show(data.show_when_op),
        show_when_value=data.show_when_value,
    )
    _apply_upsell_cfg(f, data)
    _apply_shows_on(f, data)
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return _ser_field(f)


@router.put("/fields/{field_id}")
async def update_field(
    field_id: str, data: FieldIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    f = (await db.execute(select(CustomField).where(CustomField.id == field_id, CustomField.org_id == current.org_id))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "Field not found")
    f.field_label = data.field_label
    f.field_type = data.field_type
    f.required = data.required
    f.lock_after_create = data.lock_after_create
    f.show_on_print = getattr(data, "show_on_print", False)
    f.autofill_source = getattr(data, "autofill_source", None) or None
    f.placeholder = data.placeholder
    f.options = data.options
    f.directory_key = getattr(data, "directory_key", None) or None
    f.parent_field_key = getattr(data, "parent_field_key", None) or None
    f.show_when_field_key = data.show_when_field_key or None
    f.show_when_op = _clean_show(data.show_when_op)
    f.show_when_value = data.show_when_value
    _apply_upsell_cfg(f, data)
    _apply_shows_on(f, data)
    await db.commit()
    return _ser_field(f)


@router.delete("/fields/{field_id}")
async def delete_field(
    field_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    f = (await db.execute(select(CustomField).where(CustomField.id == field_id, CustomField.org_id == current.org_id))).scalar_one_or_none()
    if not f:
        raise HTTPException(404, "Field not found")
    await db.delete(f)
    await db.commit()
    return {"ok": True}


@router.patch("/types/{type_id}/fields/reorder")
async def reorder_fields(
    type_id: str, data: FieldReorder,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    result = await db.execute(
        select(CustomField).where(CustomField.org_id == current.org_id, CustomField.type_id == type_id)
    )
    fields = {f.id: f for f in result.scalars().all()}
    for idx, fid in enumerate(data.ordered_ids):
        if fid in fields:
            fields[fid].sort_order = idx
    await db.commit()
    return {"ok": True}


# ── Org-level entity custom fields (client / asset) ──────────
# Fields with type_id = NULL and entity_type in {client, device}. These render on
# the client form, the asset form, and (for device) the work-order create modal.

@router.get("/entity-fields")
async def list_entity_fields(
    entity: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if entity not in ENTITY_TYPES:
        raise HTTPException(400, "entity must be client, device, product or lead")
    rows = (await db.execute(
        select(CustomField).where(
            CustomField.org_id == current.org_id,
            CustomField.type_id.is_(None),
            CustomField.entity_type == entity,
        ).order_by(CustomField.sort_order))).scalars().all()
    hidden = await hidden_field_ids(db, current.org_id, entity, None)   # hidden in Customize
    return {"entity": entity, "items": [_ser_field(f) for f in rows if f.id not in hidden]}


@router.post("/entity-fields", status_code=201)
async def add_entity_field(
    data: EntityFieldIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.entity_type not in ENTITY_TYPES:
        raise HTTPException(400, "entity_type must be client, device, product or lead")
    if data.field_type not in VALID_FIELD_TYPES:
        raise HTTPException(400, "Invalid field type")
    count = (await db.execute(select(func.count()).select_from(CustomField).where(
        CustomField.org_id == current.org_id, CustomField.type_id.is_(None),
        CustomField.entity_type == data.entity_type))).scalar() or 0
    f = CustomField(
        org_id=current.org_id, type_id=None, entity_type=data.entity_type,
        field_key=_slug(data.field_label) + "_" + str(count),
        field_label=data.field_label, field_type=data.field_type,
        required=data.required, lock_after_create=data.lock_after_create,
        show_on_print=getattr(data, "show_on_print", False),
        autofill_source=getattr(data, "autofill_source", None) or None,
        placeholder=data.placeholder,
        options=data.options, directory_key=getattr(data, "directory_key", None) or None,
        parent_field_key=getattr(data, "parent_field_key", None) or None, sort_order=count,
        show_when_field_key=data.show_when_field_key or None,
        show_when_op=_clean_show(data.show_when_op),
        show_when_value=data.show_when_value)
    _apply_upsell_cfg(f, data)
    _apply_shows_on(f, data)
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return _ser_field(f)


@router.get("/autofill")
async def autofill_values(
    entity: str = "order",
    type_id: Optional[str] = None,
    device_id: Optional[str] = None,
    client_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """144gq — resolve auto-fill values for a form being opened. Returns
    {field_key: value} for fields whose `autofill_source` can be resolved from the
    linked device (`asset.<attr>`) or the client's most recent order
    (`last_order.<field_key>`). Best-effort: unresolvable sources are skipped."""
    from backend.models import Device, Order
    if type_id:
        fields = (await db.execute(select(CustomField).where(
            CustomField.org_id == current.org_id, CustomField.type_id == type_id))).scalars().all()
    else:
        fields = await load_entity_fields(db, current.org_id, entity)
    fields = [f for f in fields if getattr(f, "autofill_source", None)]
    if not fields:
        return {"values": {}}

    device = None
    if device_id:
        device = (await db.execute(select(Device).where(
            Device.id == device_id, Device.org_id == current.org_id))).scalar_one_or_none()
    last_cd = None
    if client_id:
        last = (await db.execute(select(Order).where(
            Order.org_id == current.org_id, Order.client_id == client_id)
            .order_by(Order.created_at.desc()))).scalars().first()
        last_cd = (last.custom_data or {}) if last else {}

    out: dict = {}
    for f in fields:
        src = f.autofill_source or ""
        if src.startswith("asset.") and device is not None:
            val = getattr(device, src.split(".", 1)[1], None)
            if val not in (None, ""):
                out[f.field_key] = val
        elif src.startswith("last_order.") and last_cd is not None:
            val = last_cd.get(src.split(".", 1)[1])
            if val not in (None, ""):
                out[f.field_key] = val
    return {"values": out}


@router.patch("/entity-fields/reorder")
async def reorder_entity_fields(
    entity: str, data: FieldReorder,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if entity not in ENTITY_TYPES:
        raise HTTPException(400, "entity must be client, device, product or lead")
    rows = {f.id: f for f in (await db.execute(select(CustomField).where(
        CustomField.org_id == current.org_id, CustomField.type_id.is_(None),
        CustomField.entity_type == entity))).scalars().all()}
    for idx, fid in enumerate(data.ordered_ids):
        if fid in rows:
            rows[fid].sort_order = idx
    await db.commit()
    return {"ok": True}


# ── Form layout: one ordered list per form (built-in + custom fields) ─────────
# The Customize screen edits this; the work-order form renders in this order. Built-in fields
# have no rows, so their order/visibility/required live in FormLayout.data as
# [{ref: "sys:<key>" | "cf:<id>", visible, required}]. Custom fields keep sort_order/required
# on their rows too (written on save) so older readers stay correct.
LAYOUT_FORMS = {"order", "client", "device", "product", "lead"}

# Built-in fields per form that the form actually renders and that can move / hide / be required.
SYSTEM_FIELDS: dict[str, list[tuple[str, str]]] = {
    "order": [("client", "Client"), ("asset", "Device / asset"), ("complaint", "Fault reported / complaint"),
              ("priority", "Priority"), ("manager", "Manager"), ("assignee", "Technician"),
              ("scheduled_for", "Scheduled for")],
    # Name always comes first on these forms and is always required, so it isn't listed.
    "client": [("phone", "Phone"), ("email", "Email"), ("address", "Address"), ("source", "Lead source"),
               ("loyalty_tier", "Loyalty tier"), ("discount_card", "Discount card / Member ID"), ("notes", "Notes")],
    "device": [("asset_type", "Type"), ("condition", "Condition"), ("brand", "Brand"), ("model", "Model"),
               ("identifiers", "Serial / IMEI / VIN"), ("color", "Color")],
    "product": [("category", "Category"), ("description", "Description"), ("code", "Code"), ("sku", "SKU"),
                ("mpn", "MPN"), ("stock_policy", "Stock tracking"), ("pricing", "Pricing"), ("compatibility", "Compatible devices")],
    # WS-C: the inquiry form (Inquiries → New inquiry). Built-ins can be reordered or hidden.
    "lead": [("phone", "Phone"), ("email", "Email"), ("lead_type", "Inquiry type"), ("device", "Device"),
             ("serial", "Serial / IMEI"), ("inquiry", "What they asked for")],
}
# Built-ins that always carry a value (or are whole sections), so "required" would mean nothing.
_NO_REQUIRE = {"order": {"priority"}, "client": {"loyalty_tier"}, "device": {"asset_type"},
               "product": {"stock_policy", "pricing", "compatibility"},
               "lead": {"phone", "email", "lead_type", "device", "serial", "inquiry"}}
# Built-in key → which create-payload attributes fill it (any non-empty one counts).
_ENTITY_SYS_ATTRS = {
    "client": {"phone": ("phone",), "email": ("email",), "address": ("address",), "source": ("source",),
               "discount_card": ("discount_card",), "notes": ("notes",)},
    "device": {"condition": ("condition",), "brand": ("brand",), "model": ("model",),
               "identifiers": ("serial", "imei", "vin", "hin"), "color": ("color",)},
    "product": {"category": ("category_id",), "description": ("description",), "code": ("code",), "sku": ("sku",),
                "mpn": ("mpn",)},
}
_LEGACY_KEY = {"vehicle": "asset"}   # old SystemFieldsEditor keys

# One-click suggestions for "+ Add a field", by industry template. Kept short and practical.
_SUG = lambda label, ftype="text", options=None: {"label": label, "field_type": ftype, "options": options or []}  # noqa: E731
SUGGESTIONS: dict[str, dict[str, list[dict]]] = {
    "order": {
        "mobile_phone": [_SUG("Passcode"), _SUG("Pattern lock"), _SUG("Battery health %", "number"),
                         _SUG("Water damage", "bool"), _SUG("Accessories received", "multiselect",
                                                          ["Charger", "Case", "SIM card", "Memory card", "Box"]),
                         _SUG("Data backup needed", "bool"), _SUG("Approve repairs up to", "number")],
        "computer": [_SUG("Login password"), _SUG("Charger received", "bool"), _SUG("Operating system", "select",
                     ["Windows 11", "Windows 10", "macOS", "ChromeOS", "Linux"]), _SUG("Data backup needed", "bool"),
                     _SUG("Approve repairs up to", "number")],
        "auto": [_SUG("Mileage in", "number"), _SUG("Fuel level", "select", ["Empty", "1/4", "1/2", "3/4", "Full"]),
                 _SUG("Keys received", "bool"), _SUG("Customer waiting", "bool"), _SUG("Approve repairs up to", "number")],
        "small_engine": [_SUG("Engine hours", "number"), _SUG("Fuel type", "select", ["Gas", "Diesel", "Mixed 50:1", "Battery"]),
                         _SUG("Pick-up / delivery", "bool"), _SUG("Approve repairs up to", "number")],
        "_default": [_SUG("Passcode"), _SUG("Accessories received", "multiselect", ["Charger", "Case", "Cable", "Box"]),
                     _SUG("Condition notes", "textarea"), _SUG("Approve repairs up to", "number"),
                     _SUG("Preferred contact", "select", ["Call", "Text", "Email"])],
    },
    "client": {"_default": [_SUG("Preferred contact", "select", ["Call", "Text", "Email"]), _SUG("Company"),
                            _SUG("Birthday", "date"), _SUG("Tax exempt", "bool"), _SUG("Second phone")]},
    "device": {"_default": [_SUG("Storage", "select", ["64 GB", "128 GB", "256 GB", "512 GB", "1 TB"]), _SUG("Carrier"),
                            _SUG("Purchase date", "date"), _SUG("Under manufacturer warranty", "bool")]},
    "product": {"_default": [_SUG("Supplier part number"), _SUG("Compatible with", "textarea"), _SUG("Bin location")]},
}


def _layout_custom_q(org_id: str, form: str, type_id: Optional[str]):
    q = select(CustomField).where(CustomField.org_id == org_id)
    if form == "order":
        return q.where(CustomField.type_id == type_id)
    return q.where(CustomField.type_id.is_(None), CustomField.entity_type == form)


async def _layout_row(db, org_id: str, form: str, type_id: Optional[str], fallback: bool = True):
    def q(tid):
        return select(FormLayout).where(FormLayout.org_id == org_id, FormLayout.entity == form,
                                        FormLayout.type_id.is_(None) if not tid else FormLayout.type_id == tid)
    row = (await db.execute(q(type_id))).scalars().first()
    if row is None and fallback and type_id:
        row = (await db.execute(q(None))).scalars().first()
    return row


async def hidden_field_ids(db, org_id: str, form: str, type_id: Optional[str]) -> set:
    row = await _layout_row(db, org_id, form, type_id)
    return {i["ref"][3:] for i in (row.data or []) if isinstance(i, dict) and str(i.get("ref", "")).startswith("cf:")
            and i.get("visible") is False} if row else set()


# How strictly a field is required: "" optional · "intake" when the record is created ·
# "close" before a work order can move to a closed/done status (work orders only).
def _level(v, form: str) -> str:
    if v is True or v == "intake":
        return "intake"
    if v == "close" and form == "order":
        return "close"
    return ""


async def build_layout(db, org_id: str, form: str, type_id: Optional[str]) -> list[dict]:
    customs = (await db.execute(_layout_custom_q(org_id, form, type_id).order_by(CustomField.sort_order))).scalars().all()
    system = SYSTEM_FIELDS.get(form, [])
    row = await _layout_row(db, org_id, form, type_id)
    saved = []
    for i in (row.data or []) if row else []:
        if not isinstance(i, dict):
            continue
        ref = i.get("ref") or (f"sys:{_LEGACY_KEY.get(i.get('key'), i.get('key'))}" if i.get("key") else None)
        if ref:
            saved.append({**i, "ref": ref})
    sys_by = {k: lbl for k, lbl in system}
    cf_by = {f.id: f for f in customs}
    out, seen = [], set()

    def add(ref, cfg=None):
        if ref in seen:
            return
        cfg = cfg or {}
        visible = cfg.get("visible", True) is not False
        if ref.startswith("sys:") and ref[4:] in sys_by:
            k = ref[4:]
            requirable = k not in _NO_REQUIRE.get(form, set())
            lvl = _level(cfg.get("required"), form) if visible and requirable else ""
            out.append({"ref": ref, "kind": "system", "key": k, "label": sys_by[k], "visible": visible,
                        "required": lvl == "intake", "require": lvl, "requirable": requirable})
        elif ref.startswith("cf:") and ref[3:] in cf_by:
            fld = cf_by[ref[3:]]
            lvl = "" if fld.field_type == "header" or not visible else (
                "intake" if fld.required else _level(cfg.get("required"), form))
            out.append({"ref": ref, "kind": "header" if fld.field_type == "header" else "custom", "key": fld.field_key,
                        "label": fld.field_label, "visible": visible, "requirable": fld.field_type != "header",
                        "required": lvl == "intake", "require": lvl, "field": _ser_field(fld), "_row": fld})
        else:
            return
        seen.add(ref)

    for i in saved:
        add(i["ref"], i)
    if not saved:
        # Never customised: keep today's look — the type's own fields first, then the built-ins.
        for f_ in customs:
            add(f"cf:{f_.id}")
    for k, _ in system:
        add(f"sys:{k}")
    for f_ in customs:
        add(f"cf:{f_.id}")
    return out


# Built-in work-order fields → "is it filled?" on a saved order (Before-closing check).
_ORDER_SYS_FILLED = {
    "client": lambda o: bool(o.client_id), "asset": lambda o: bool(o.device_id),
    "complaint": lambda o: not _is_empty(o.complaint), "priority": lambda o: True,
    "manager": lambda o: bool(o.manager_id), "assignee": lambda o: bool(o.assigned_to_id),
    "scheduled_for": lambda o: bool(o.scheduled_for),
}


def _cf_filled(fld: CustomField, v) -> bool:
    if fld.field_type == "bool":
        return v is True          # a yes/no needed to close means "ticked", e.g. "Data backed up"
    if isinstance(v, list):
        return len(v) > 0
    return not _is_empty(v)


async def missing_before_close(db, org_id: str, order) -> list[str]:
    """Labels of the fields this order's form marks "Before closing" that are still empty.
    Fields hidden in Customize, or hidden by their own show-rule, never block."""
    if not order.type_id or getattr(order, "is_estimate", False):
        return []
    values = order.custom_data or {}
    out = []
    for it in await build_layout(db, org_id, "order", order.type_id):
        if not it["visible"] or it.get("require") != "close":
            continue
        if it["kind"] == "system":
            if not _ORDER_SYS_FILLED.get(it["key"], lambda o: True)(order):
                out.append(it["label"])
        elif it["kind"] == "custom":
            fld = it["_row"]
            if _field_visible(fld, values) and not _cf_filled(fld, values.get(fld.field_key)):
                out.append(it["label"])
    return out


def _fmt_value(v) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


async def shown_values(db, org_id: str, flag: str, order=None, device=None, client=None) -> list[dict]:
    """[{label, value}] for the custom fields that "Show on" an output — flag is "show_on_print"
    (printed receipt) or "show_in_portal" (customer tracking page / portal). Work-order fields
    first (that type's, in form order), then the device's, then the client's. Fields hidden in
    Customize, hidden by their show-rule, or left empty are skipped."""
    out = []
    sources = [("order", order, order.type_id if order is not None else None), ("device", device, None), ("client", client, None)]
    for form, rec, type_id in sources:
        if rec is None or (form == "order" and not type_id):
            continue
        values = rec.custom_data or {}
        hidden = await hidden_field_ids(db, org_id, form, type_id)
        for f in (await db.execute(_layout_custom_q(org_id, form, type_id).order_by(CustomField.sort_order))).scalars().all():
            if f.field_type == "header" or f.id in hidden or not getattr(f, flag, False) or not _field_visible(f, values):
                continue
            v = values.get(f.field_key)
            if v is None or v == [] or (isinstance(v, str) and not v.strip()):
                continue
            out.append({"label": f.field_label, "value": _fmt_value(v)})
    return out


async def check_entity_required(db, org_id: str, form: str, data) -> None:
    """422 when a client / device / product create leaves a built-in field empty that its form
    (Settings → Forms → Customize) requires. Custom fields are checked by resolve_custom_data."""
    attrs = _ENTITY_SYS_ATTRS.get(form, {})
    missing = []
    for it in await build_layout(db, org_id, form, None):
        if it["kind"] != "system" or not it["visible"] or it["require"] != "intake" or it["key"] not in attrs:
            continue
        if all(_is_empty(getattr(data, a, None)) for a in attrs[it["key"]]):
            missing.append(it["label"])
    if missing:
        raise HTTPException(422, f"Fill in {', '.join(missing)} first.")


async def missing_at_intake(db, org_id: str, type_id: Optional[str], data) -> list[str]:
    """Labels of fields the work-order form requires at intake that a create payload leaves
    empty. The form checks this too; this keeps API callers and stale forms honest."""
    if not type_id:
        return []
    values = data.custom_data or {}
    filled = {
        "client": bool(data.client_id), "complaint": not _is_empty(data.complaint), "priority": True,
        "manager": bool(data.manager_id), "assignee": bool(data.assigned_to_id), "scheduled_for": bool(data.scheduled_for),
        "asset": bool(data.device_id or data.asset_brand or data.asset_model or data.asset_serial
                      or data.asset_imei or data.asset_vin or data.asset_hin),
    }
    out = []
    for it in await build_layout(db, org_id, "order", type_id):
        if not it["visible"] or it.get("require") != "intake":
            continue
        if it["kind"] == "system":
            if not filled.get(it["key"], True):
                out.append(it["label"])
        elif it["kind"] == "custom":
            fld = it["_row"]
            if fld.field_type != "bool" and _field_visible(fld, values) and not _cf_filled(fld, values.get(fld.field_key)):
                out.append(it["label"])
    return out


async def _template_key(db, org_id: str) -> str:
    from backend.models import Organisation
    from backend.services import industry as _ind
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    return _ind.normalize_template_key(getattr(org, "industry_template", None))


def _public(items: list[dict]) -> list[dict]:
    return [{k: v for k, v in i.items() if k != "_row"} for i in items]


@router.get("/layout")
async def get_layout(
    form: str,
    type_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if form not in LAYOUT_FORMS:
        raise HTTPException(400, "form must be order, client, device, product or lead")
    if form == "order" and not type_id:
        raise HTTPException(422, "type_id is required for the work order form")
    items = _public(await build_layout(db, current.org_id, form, type_id))
    have = {i["label"].strip().lower() for i in items}
    tk = await _template_key(db, current.org_id)
    lib = SUGGESTIONS.get(form, {})
    sugg = [x for x in (lib.get(tk) or lib.get("_default") or []) if x["label"].lower() not in have]
    return {"form": form, "type_id": type_id, "items": items, "suggestions": sugg}


class LayoutItemIn(BaseModel):
    ref: str
    visible: bool = True
    required: Union[bool, str] = False   # False/"" · True/"intake" · "close" (work orders only)


class LayoutSaveIn(BaseModel):
    form: str
    type_id: Optional[str] = None
    items: List[LayoutItemIn]


@router.put("/layout")
async def save_layout(
    data: LayoutSaveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.form not in LAYOUT_FORMS:
        raise HTTPException(400, "form must be order, client, device, product or lead")
    customs = {f.id: f for f in (await db.execute(_layout_custom_q(current.org_id, data.form, data.type_id))).scalars().all()}
    sys_keys = {k for k, _ in SYSTEM_FIELDS.get(data.form, [])}
    clean, order = [], 0
    for it in data.items:
        # A hidden field can't be required — it would block saving a form that doesn't show it.
        lvl = _level(it.required, data.form) if it.visible else ""
        if it.ref.startswith("sys:") and it.ref[4:] in sys_keys:
            if it.ref[4:] in _NO_REQUIRE.get(data.form, set()):
                lvl = ""
            clean.append({"ref": it.ref, "visible": it.visible, "required": lvl})
        elif it.ref.startswith("cf:") and it.ref[3:] in customs:
            f = customs[it.ref[3:]]
            f.sort_order = order
            order += 1
            if f.field_type == "header":
                lvl = ""
            # "At intake" stays on the field row (older readers enforce it); "Before closing" lives here.
            f.required = lvl == "intake"
            clean.append({"ref": it.ref, "visible": it.visible, **({"required": "close"} if lvl == "close" else {})})
    row = await _layout_row(db, current.org_id, data.form, data.type_id, fallback=False)
    if not row:
        row = FormLayout(org_id=current.org_id, entity=data.form, type_id=data.type_id or None)
        db.add(row)
    row.data = clean
    await db.commit()
    return {"ok": True, "items": _public(await build_layout(db, current.org_id, data.form, data.type_id))}
