"""
Forms Editor — configure work-order types and their custom fields.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sa_delete
from pydantic import BaseModel
from typing import Optional, List
from backend.db.database import get_db
from backend.models import WorkOrderType, CustomField, Employee, FormLayout
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/forms", tags=["forms-editor"])

VALID_ENTITIES = {"order", "invoice", "contact", "product", "vehicle"}


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
            FormLayout.type_id == (type_id or None),
        )
    )).scalar_one_or_none()
    return {"entity": entity, "type_id": type_id, "fields": (row.data if row and row.data else [])}


@router.put("/system-layout")
async def put_system_layout(
    data: LayoutIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if data.entity not in VALID_ENTITIES:
        raise HTTPException(400, "Unknown entity")
    row = (await db.execute(
        select(FormLayout).where(
            FormLayout.org_id == current.org_id,
            FormLayout.entity == data.entity,
            FormLayout.type_id == (data.type_id or None),
        )
    )).scalar_one_or_none()
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


SHOW_OPS = {"is", "is_not", "gt", "lt", "filled"}


class FieldIn(BaseModel):
    field_label: str
    field_type: str
    required: bool = False
    lock_after_create: bool = False
    show_on_print: bool = False
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


ENTITY_TYPES = {"client", "device", "product"}


class EntityFieldIn(BaseModel):
    entity_type: str
    field_label: str
    field_type: str
    required: bool = False
    lock_after_create: bool = False
    show_on_print: bool = False
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
    }

def _ser_field(f: CustomField) -> dict:
    return {
        "id": f.id, "type_id": f.type_id, "field_key": f.field_key,
        "field_label": f.field_label, "field_type": f.field_type,
        "required": f.required, "lock_after_create": getattr(f, "lock_after_create", False),
        "show_on_print": getattr(f, "show_on_print", False),
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
    # field counts
    out = []
    for t in types:
        cnt = (await db.execute(
            select(func.count()).select_from(CustomField).where(CustomField.type_id == t.id)
        )).scalar() or 0
        d = _ser_type(t)
        d["field_count"] = cnt
        out.append(d)
    return {"items": out}


@router.post("/types", status_code=201)
async def create_type(
    data: TypeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
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
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _ser_type(t)


@router.put("/types/{type_id}")
async def update_type(
    type_id: str, data: TypeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
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
    await db.commit()
    return _ser_type(t)


@router.delete("/types/{type_id}")
async def delete_type(
    type_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
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
    return {"items": [_ser_field(f) for f in result.scalars().all()]}


@router.post("/types/{type_id}/fields", status_code=201)
async def add_field(
    type_id: str, data: FieldIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
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
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return _ser_field(f)


@router.put("/fields/{field_id}")
async def update_field(
    field_id: str, data: FieldIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
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
    await db.commit()
    return _ser_field(f)


@router.delete("/fields/{field_id}")
async def delete_field(
    field_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
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
    current: Employee = Depends(get_current_employee),
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
        raise HTTPException(400, "entity must be client, device or product")
    rows = (await db.execute(
        select(CustomField).where(
            CustomField.org_id == current.org_id,
            CustomField.type_id.is_(None),
            CustomField.entity_type == entity,
        ).order_by(CustomField.sort_order))).scalars().all()
    return {"entity": entity, "items": [_ser_field(f) for f in rows]}


@router.post("/entity-fields", status_code=201)
async def add_entity_field(
    data: EntityFieldIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    if data.entity_type not in ENTITY_TYPES:
        raise HTTPException(400, "entity_type must be client, device or product")
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
    current: Employee = Depends(get_current_employee),
):
    if entity not in ENTITY_TYPES:
        raise HTTPException(400, "entity must be client, device or product")
    rows = {f.id: f for f in (await db.execute(select(CustomField).where(
        CustomField.org_id == current.org_id, CustomField.type_id.is_(None),
        CustomField.entity_type == entity))).scalars().all()}
    for idx, fid in enumerate(data.ordered_ids):
        if fid in rows:
            rows[fid].sort_order = idx
    await db.commit()
    return {"ok": True}
