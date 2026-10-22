"""Work-order checklists (Build 144dp — Orderry-style v2).

Templates are Template → Steps → Sections → typed Fields. Attaching a template to
an order snapshots its fields onto an OrderChecklist, so later template edits never
change a job's history. A work order can carry several checklists, each with its own
status and derived pass/fail result.

The flat 144-era per-order endpoints (`/orders/{id}/checklist`, singular) are kept
unchanged for the mobile PWA; the completion gate in orders.change_status reads
OrderChecklistItem.required/done for both the flat and the v2 items.
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    ChecklistSection,
    ChecklistStep,
    ChecklistTemplate,
    ChecklistTemplateItem,
    Employee,
    Order,
    OrderChecklist,
    OrderChecklistItem,
)
from backend.services.auth import get_current_employee, require_perm, record_scope

router = APIRouter(prefix="/api", tags=["checklists"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}

FIELD_KINDS = {"check_result", "yes_no", "text", "textarea", "dropdown",
               "multiselect", "date", "file", "catalog_pick"}
CHECK_STATES = {"pass", "fail", "caution", "unknown"}


def _loads(s: Optional[str], default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


# ── Template serialization ────────────────────────────────────────────────────
async def _steps_tree(db: AsyncSession, template_id: str) -> list[dict]:
    steps = (await db.execute(select(ChecklistStep).where(
        ChecklistStep.template_id == template_id).order_by(ChecklistStep.sort_order))).scalars().all()
    secs = (await db.execute(select(ChecklistSection).where(
        ChecklistSection.step_id.in_([s.id for s in steps] or [""])).order_by(ChecklistSection.sort_order))).scalars().all()
    items = (await db.execute(select(ChecklistTemplateItem).where(
        ChecklistTemplateItem.template_id == template_id).order_by(ChecklistTemplateItem.sort_order))).scalars().all()
    secs_by_step: dict = {}
    for s in secs:
        secs_by_step.setdefault(s.step_id, []).append(s)
    items_by_sec: dict = {}
    for it in items:
        items_by_sec.setdefault(it.section_id, []).append(it)

    def _item(it):
        return {"id": it.id, "label": it.label, "description": it.description,
                "field_kind": it.field_kind, "options": _loads(it.options, []),
                "required": it.required, "client_visible": it.client_visible,
                "affects_result": it.affects_result, "sort_order": it.sort_order,
                "upsell_ref_id": it.upsell_ref_id, "upsell_kind": it.upsell_kind,
                "upsell_price": it.upsell_price, "upsell_threshold": it.upsell_threshold,
                "upsell_comparator": it.upsell_comparator}

    out = []
    for st in steps:
        sections = [{
            "id": sec.id, "title": sec.title, "allow_notes": sec.allow_notes,
            "sort_order": sec.sort_order,
            "items": [_item(i) for i in items_by_sec.get(sec.id, [])],
        } for sec in secs_by_step.get(st.id, [])]
        out.append({"id": st.id, "title": st.title, "description": st.description,
                    "sort_order": st.sort_order, "sections": sections})
    return out


async def _tpl(db: AsyncSession, t: ChecklistTemplate) -> dict:
    # legacy flat items = items with no section (kept for the mobile PWA)
    flat = (await db.execute(select(ChecklistTemplateItem).where(
        ChecklistTemplateItem.template_id == t.id,
        ChecklistTemplateItem.section_id.is_(None)).order_by(ChecklistTemplateItem.sort_order))).scalars().all()
    return {
        "id": t.id, "name": t.name, "type_id": t.type_id, "active": t.active,
        "scope": t.scope, "type_ids": _loads(t.type_ids, []),
        "steps": await _steps_tree(db, t.id),
        "items": [{"id": i.id, "label": i.label, "required": i.required,
                   "sort_order": i.sort_order} for i in flat],
    }


# ── Template pydantic ─────────────────────────────────────────────────────────
class ItemIn(BaseModel):
    label: str
    description: Optional[str] = None
    field_kind: str = "check_result"
    options: list[str] = []
    required: bool = True
    client_visible: bool = False
    affects_result: bool = True
    # 144go — smart upsell config
    upsell_ref_id: Optional[str] = None
    upsell_kind: Optional[str] = None          # service | product | custom
    upsell_price: Optional[float] = None
    upsell_threshold: Optional[float] = None
    upsell_comparator: Optional[str] = None    # lt | gt | fail


class SectionIn(BaseModel):
    title: str
    allow_notes: bool = False
    items: list[ItemIn] = []


class StepIn(BaseModel):
    title: str
    description: Optional[str] = None
    sections: list[SectionIn] = []


class TemplateItemIn(BaseModel):  # legacy flat
    label: str
    required: bool = True


class TemplateIn(BaseModel):
    name: str
    type_id: Optional[str] = None
    scope: str = "all"                 # all | selected
    type_ids: list[str] = []
    steps: list[StepIn] = []
    items: list[TemplateItemIn] = []   # legacy flat fallback


async def _write_structure(db: AsyncSession, org_id: str, t: ChecklistTemplate, data: TemplateIn):
    """(Re)write a template's steps/sections/items from the payload."""
    t.name = data.name
    t.type_id = data.type_id
    t.scope = data.scope if data.scope in ("all", "selected") else "all"
    t.type_ids = json.dumps(data.type_ids or [])
    # Wipe the existing structure CHILD-FIRST (items -> sections -> steps). Don't rely on the DB's
    # ON DELETE CASCADE: on any connection without FK enforcement it silently doesn't run and the
    # old sections are orphaned (found in a real DB — 144ep). Order checklists are snapshots, so
    # rewriting a template never touches a job's recorded answers.
    old_steps = (await db.execute(select(ChecklistStep).where(ChecklistStep.template_id == t.id))).scalars().all()
    for it in (await db.execute(select(ChecklistTemplateItem).where(
            ChecklistTemplateItem.template_id == t.id))).scalars().all():
        await db.delete(it)
    if old_steps:
        for se in (await db.execute(select(ChecklistSection).where(
                ChecklistSection.step_id.in_([s.id for s in old_steps])))).scalars().all():
            await db.delete(se)
    for st in old_steps:
        await db.delete(st)
    await db.flush()

    order_i = 0
    for si, step in enumerate(data.steps):
        st = ChecklistStep(org_id=org_id, template_id=t.id, title=step.title,
                           description=step.description, sort_order=si)
        db.add(st)
        await db.flush()
        for ci, sec in enumerate(step.sections):
            se = ChecklistSection(org_id=org_id, step_id=st.id, title=sec.title,
                                  allow_notes=sec.allow_notes, sort_order=ci)
            db.add(se)
            await db.flush()
            for it in sec.items:
                fk = it.field_kind if it.field_kind in FIELD_KINDS else "check_result"
                db.add(ChecklistTemplateItem(
                    org_id=org_id, template_id=t.id, section_id=se.id, label=it.label,
                    description=it.description, field_kind=fk, options=json.dumps(it.options or []),
                    required=it.required, client_visible=it.client_visible,
                    affects_result=it.affects_result, sort_order=order_i,
                    upsell_ref_id=it.upsell_ref_id, upsell_kind=it.upsell_kind,
                    upsell_price=it.upsell_price, upsell_threshold=it.upsell_threshold,
                    upsell_comparator=(it.upsell_comparator if it.upsell_comparator in ("lt", "gt", "fail") else None)))
                order_i += 1
    # legacy flat items (no section) — mobile PWA
    for li, it in enumerate(data.items):
        db.add(ChecklistTemplateItem(org_id=org_id, template_id=t.id, section_id=None,
                                     label=it.label, required=it.required, sort_order=li))


@router.get("/checklist-templates")
async def list_templates(
    type_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(ChecklistTemplate).where(ChecklistTemplate.org_id == current.org_id,
                                        ChecklistTemplate.active == True)  # noqa: E712
    tpls = (await db.execute(q.order_by(ChecklistTemplate.name))).scalars().all()
    out = []
    for t in tpls:
        # filter by applicability to an order type when asked
        if type_id and t.scope == "selected" and type_id not in _loads(t.type_ids, []) and t.type_id != type_id:
            continue
        out.append(await _tpl(db, t))
    return {"items": out}


@router.get("/checklist-templates/{tid}")
async def get_template(
    tid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = (await db.execute(select(ChecklistTemplate).where(
        ChecklistTemplate.id == tid, ChecklistTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    return await _tpl(db, t)


def _require_fields(data: TemplateIn) -> None:
    """A template with nothing to fill in saved "successfully" but every attempt to apply it to a
    work order failed with "This template has no fields". Refuse it up front instead, and name
    any field the user added but left without a label (those used to be dropped silently)."""
    for step in data.steps:
        for sec in step.sections:
            if any(not (it.label or "").strip() for it in sec.items):
                raise HTTPException(422, f"Every item in “{step.title or 'Step'}” needs a label")
    has = any(it.label.strip() for st in data.steps for sec in st.sections for it in sec.items) or \
        any(it.label.strip() for it in data.items)
    if not has:
        raise HTTPException(422, "Add at least one item to the checklist")


@router.post("/checklist-templates", status_code=201)
async def create_template(
    data: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_checklist_templates")),
):
    dupe = (await db.execute(select(ChecklistTemplate).where(
        ChecklistTemplate.org_id == current.org_id, ChecklistTemplate.name == data.name,
        ChecklistTemplate.active == True))).scalar_one_or_none()  # noqa: E712
    if dupe:
        raise HTTPException(409, "A template with this name already exists")
    _require_fields(data)
    t = ChecklistTemplate(org_id=current.org_id, name=data.name)
    db.add(t)
    await db.flush()
    await _write_structure(db, current.org_id, t, data)
    await db.commit()
    await db.refresh(t)
    return await _tpl(db, t)


@router.put("/checklist-templates/{tid}")
async def update_template(
    tid: str,
    data: TemplateIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_checklist_templates")),
):
    t = (await db.execute(select(ChecklistTemplate).where(
        ChecklistTemplate.id == tid, ChecklistTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    _require_fields(data)
    await _write_structure(db, current.org_id, t, data)
    await db.commit()
    await db.refresh(t)
    return await _tpl(db, t)


@router.delete("/checklist-templates/{tid}")
async def delete_template(
    tid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_checklist_templates")),
):
    t = (await db.execute(select(ChecklistTemplate).where(
        ChecklistTemplate.id == tid, ChecklistTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    t.active = False
    await db.commit()
    return {"ok": True}


# ── Per-order v2 checklists (OrderChecklist header + typed items) ──────────────
async def _order_or_404(db: AsyncSession, org_id: str, order_id: str) -> Order:
    o = (await db.execute(select(Order).where(
        Order.id == order_id, Order.org_id == org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    return o


async def _cl_or_404(db: AsyncSession, org_id: str, order_id: str, cid: str) -> OrderChecklist:
    cl = (await db.execute(select(OrderChecklist).where(
        OrderChecklist.id == cid, OrderChecklist.order_id == order_id,
        OrderChecklist.org_id == org_id))).scalar_one_or_none()
    if not cl:
        raise HTTPException(404, "Checklist not found")
    return cl


async def _cl_items(db: AsyncSession, cid: str) -> list[OrderChecklistItem]:
    return (await db.execute(select(OrderChecklistItem).where(
        OrderChecklistItem.checklist_id == cid).order_by(OrderChecklistItem.sort_order))).scalars().all()


def _ser_cl_item(c: OrderChecklistItem) -> dict:
    return {"id": c.id, "label": c.label, "step_title": c.step_title, "section_title": c.section_title,
            "field_kind": c.field_kind, "options": _loads(c.options, []), "required": c.required,
            "client_visible": c.client_visible, "affects_result": c.affects_result,
            "value": _loads(c.value, None), "file_ids": _loads(c.file_ids, []),
            "done": c.done, "done_at": c.done_at.isoformat() if c.done_at else None,
            "upsell_ref_id": c.upsell_ref_id, "upsell_kind": c.upsell_kind,
            "upsell_price": c.upsell_price, "upsell_threshold": c.upsell_threshold,
            "upsell_comparator": c.upsell_comparator}


def _upsell_triggered(c: OrderChecklistItem) -> bool:
    """Does this item's current value fire its upsell rule?  Deterministic:
    'fail' → a Fail check_result; 'lt'/'gt' → numeric value past the threshold.
    A check_result item with a linked product but no explicit comparator defaults
    to firing on Fail."""
    if not c.upsell_ref_id and c.upsell_price is None:
        return False
    val = _loads(c.value, None)
    comp = c.upsell_comparator or ("fail" if c.field_kind == "check_result" else None)
    if comp == "fail":
        return str(val).lower() == "fail"
    if comp in ("lt", "gt") and c.upsell_threshold is not None:
        try:
            n = float(val)
        except (TypeError, ValueError):
            return False
        return n < c.upsell_threshold if comp == "lt" else n > c.upsell_threshold
    return False


async def _sync_checklist_upsell(db: AsyncSession, order_id: str, org_id: str,
                                 c: OrderChecklistItem, actor: Employee):
    """A checklist item's auto-drafted upsell, via the shared rule surface (144gp
    Phase B). Lifecycle (idempotent, retract-aware, never touches sent/approved)
    lives in services/field_rules — one code path shared with custom-field rules."""
    from backend.services.field_rules import sync_upsell_draft
    await sync_upsell_draft(
        db, org_id=org_id, order_id=order_id, source="checklist", source_ref_id=c.id,
        triggered=_upsell_triggered(c),
        title=f"Recommended: {c.label}",
        note=f"From checklist finding: {c.label}" + (f" ({c.section_title})" if c.section_title else ""),
        kind=(c.upsell_kind or "custom"), ref_id=c.upsell_ref_id, price=c.upsell_price or 0.0,
        actor_id=actor.id, actor_name=actor.name)


def _recompute(cl: OrderChecklist, items: list[OrderChecklistItem]):
    """Derive completion status + pass/fail result from the items."""
    req = [c for c in items if c.required]
    complete = all(c.done for c in req) if req else all(c.done for c in items)
    cl.status = "completed" if (items and complete) else "in_progress"
    cl.completed_at = datetime.utcnow() if cl.status == "completed" else None
    # result: failed if any affects_result check-result item is 'fail'
    failed = any(c.field_kind == "check_result" and c.affects_result and
                 (_loads(c.value, None) == "fail") for c in items)
    if cl.status == "completed":
        cl.result = "failed" if failed else "passed"
    else:
        cl.result = "failed" if failed else None


def _summary(cl: OrderChecklist, items: list[OrderChecklistItem]) -> dict:
    steps = {}
    for c in items:
        steps.setdefault(c.step_title or "", []).append(c)
    steps_total = len(steps)
    steps_done = sum(1 for _, its in steps.items() if all(i.done for i in its if i.required) and any(i.done for i in its))
    return {"status": cl.status, "result": cl.result,
            "items_total": len(items), "items_done": sum(1 for c in items if c.done),
            "steps_total": steps_total, "steps_done": steps_done}


@router.get("/orders/{order_id}/checklists")
async def list_order_checklists(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _order_or_404(db, current.org_id, order_id)
    cls = (await db.execute(select(OrderChecklist).where(
        OrderChecklist.order_id == order_id, OrderChecklist.org_id == current.org_id)
        .order_by(OrderChecklist.created_at))).scalars().all()
    names = {}
    out = []
    for cl in cls:
        items = await _cl_items(db, cl.id)
        aid = cl.author_id
        if aid and aid not in names:
            e = (await db.execute(select(Employee).where(Employee.id == aid))).scalar_one_or_none()
            names[aid] = e.name if e else None
        out.append({"id": cl.id, "title": cl.title, "author": names.get(aid),
                    "created_at": cl.created_at.isoformat() if cl.created_at else None,
                    "completed_at": cl.completed_at.isoformat() if cl.completed_at else None,
                    **_summary(cl, items)})
    return {"items": out}


class AttachIn(BaseModel):
    template_id: str


@router.post("/orders/{order_id}/checklists", status_code=201)
async def attach_checklist(
    order_id: str,
    data: AttachIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_create_checklist")),
):
    await _order_or_404(db, current.org_id, order_id)
    t = (await db.execute(select(ChecklistTemplate).where(
        ChecklistTemplate.id == data.template_id, ChecklistTemplate.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    tree = await _steps_tree(db, t.id)
    cl = OrderChecklist(org_id=current.org_id, order_id=order_id, template_id=t.id,
                        title=t.name, status="in_progress", author_id=current.id)
    db.add(cl)
    await db.flush()
    order_i = 0
    for st in tree:
        for sec in st["sections"]:
            for it in sec["items"]:
                db.add(OrderChecklistItem(
                    org_id=current.org_id, order_id=order_id, checklist_id=cl.id,
                    step_title=st["title"], section_title=sec["title"], label=it["label"],
                    field_kind=it["field_kind"], options=json.dumps(it["options"]),
                    required=it["required"], client_visible=it["client_visible"],
                    affects_result=it["affects_result"], sort_order=order_i,
                    upsell_ref_id=it.get("upsell_ref_id"), upsell_kind=it.get("upsell_kind"),
                    upsell_price=it.get("upsell_price"), upsell_threshold=it.get("upsell_threshold"),
                    upsell_comparator=it.get("upsell_comparator")))
                order_i += 1
    if order_i == 0:
        raise HTTPException(422, "This template has no fields")
    await db.commit()
    await db.refresh(cl)
    return {"id": cl.id, "title": cl.title, **_summary(cl, await _cl_items(db, cl.id))}


@router.get("/orders/{order_id}/checklists/{cid}")
async def get_order_checklist(
    order_id: str,
    cid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _order_or_404(db, current.org_id, order_id)
    cl = await _cl_or_404(db, current.org_id, order_id, cid)
    items = await _cl_items(db, cid)
    return {"id": cl.id, "title": cl.title, "summary": _summary(cl, items),
            "items": [_ser_cl_item(c) for c in items]}


class FillIn(BaseModel):
    value: Optional[object] = None
    file_ids: Optional[list[str]] = None


@router.patch("/orders/{order_id}/checklists/{cid}/items/{iid}")
async def fill_item(
    order_id: str,
    cid: str,
    iid: str,
    data: FillIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_edit_checklist")),
):
    cl = await _cl_or_404(db, current.org_id, order_id, cid)
    c = (await db.execute(select(OrderChecklistItem).where(
        OrderChecklistItem.id == iid, OrderChecklistItem.checklist_id == cid,
        OrderChecklistItem.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Checklist item not found")
    if c.field_kind == "check_result" and data.value is not None and data.value not in CHECK_STATES:
        raise HTTPException(422, f"check_result must be one of {sorted(CHECK_STATES)}")
    if data.value is not None:
        c.value = json.dumps(data.value)
    if data.file_ids is not None:
        c.file_ids = json.dumps(data.file_ids)
    has_value = _loads(c.value, None) not in (None, "", [])
    has_file = bool(_loads(c.file_ids, []))
    c.done = has_value or (c.field_kind == "file" and has_file)
    c.done_by_id = current.id if c.done else None
    c.done_at = datetime.utcnow() if c.done else None
    await db.flush()
    # 144go — smart upsell: a Fail / threshold breach auto-drafts a recommendation.
    await _sync_checklist_upsell(db, order_id, current.org_id, c, current)
    items = await _cl_items(db, cid)
    _recompute(cl, items)
    await db.commit()
    return {"item": _ser_cl_item(c), "summary": _summary(cl, items)}


# Build 144dp — differentiator: a failed check becomes a customer Recommendation
# (upsell), reusing the existing recommendations feature + client track page.
class RecommendIn(BaseModel):
    note: Optional[str] = None
    unit_price: float = 0.0


@router.post("/orders/{order_id}/checklists/{cid}/items/{iid}/recommend", status_code=201)
async def item_to_recommendation(
    order_id: str,
    cid: str,
    iid: str,
    data: RecommendIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_recommend")),
):
    await _cl_or_404(db, current.org_id, order_id, cid)
    c = (await db.execute(select(OrderChecklistItem).where(
        OrderChecklistItem.id == iid, OrderChecklistItem.checklist_id == cid,
        OrderChecklistItem.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Checklist item not found")
    from backend.models import Recommendation
    r = Recommendation(
        org_id=current.org_id, order_id=order_id,
        title=f"Address: {c.label}", note=data.note or (c.section_title or None),
        kind="custom", qty=1.0, unit_price=data.unit_price or 0.0,
        status="draft", author_id=current.id, author_name=current.name)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return {"id": r.id, "title": r.title, "status": r.status}


@router.delete("/orders/{order_id}/checklists/{cid}")
async def delete_order_checklist(
    order_id: str,
    cid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_delete_checklist")),
):
    cl = await _cl_or_404(db, current.org_id, order_id, cid)
    await db.delete(cl)
    await db.commit()
    return {"ok": True}


@router.get("/orders/{order_id}/checklist-progress")
async def checklist_progress(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Compact roll-up for the status-badge chip: any unfinished checklists?"""
    await _order_or_404(db, current.org_id, order_id)
    cls = (await db.execute(select(OrderChecklist).where(
        OrderChecklist.order_id == order_id, OrderChecklist.org_id == current.org_id))).scalars().all()
    unfinished = 0
    steps_total = steps_done = 0
    for cl in cls:
        items = await _cl_items(db, cl.id)
        s = _summary(cl, items)
        steps_total += s["steps_total"]
        steps_done += s["steps_done"]
        if cl.status != "completed":
            unfinished += 1
    return {"checklists": len(cls), "unfinished": unfinished,
            "steps_total": steps_total, "steps_done": steps_done}


# ── Legacy flat per-order checklist (mobile PWA; unchanged) ────────────────────
async def _items(db: AsyncSession, order_id: str) -> list[OrderChecklistItem]:
    return (await db.execute(select(OrderChecklistItem).where(
        OrderChecklistItem.order_id == order_id,
        OrderChecklistItem.checklist_id.is_(None)).order_by(OrderChecklistItem.sort_order))).scalars().all()


def _ser_item(c: OrderChecklistItem) -> dict:
    return {"id": c.id, "label": c.label, "required": c.required, "sort_order": c.sort_order,
            "done": c.done, "done_at": c.done_at.isoformat() if c.done_at else None}


def _flat_summary(items: list[OrderChecklistItem]) -> dict:
    req = [c for c in items if c.required]
    return {"total": len(items), "done": sum(1 for c in items if c.done),
            "required": len(req), "required_done": sum(1 for c in req if c.done),
            "complete": all(c.done for c in req)}


@router.get("/orders/{order_id}/checklist")
async def get_checklist(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _order_or_404(db, current.org_id, order_id)
    items = await _items(db, order_id)
    return {"items": [_ser_item(c) for c in items], "summary": _flat_summary(items)}


class AddItemIn(BaseModel):
    label: Optional[str] = None
    required: bool = True
    template_id: Optional[str] = None


@router.post("/orders/{order_id}/checklist", status_code=201)
async def add_checklist(
    order_id: str,
    data: AddItemIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _order_or_404(db, current.org_id, order_id)
    existing = await _items(db, order_id)
    base = len(existing)
    if data.template_id:
        tpl_items = (await db.execute(select(ChecklistTemplateItem).where(
            ChecklistTemplateItem.template_id == data.template_id,
            ChecklistTemplateItem.org_id == current.org_id).order_by(ChecklistTemplateItem.sort_order))).scalars().all()
        if not tpl_items:
            raise HTTPException(404, "Template not found or empty")
        for idx, ti in enumerate(tpl_items):
            db.add(OrderChecklistItem(org_id=current.org_id, order_id=order_id, label=ti.label,
                                      required=ti.required, sort_order=base + idx))
    elif data.label:
        db.add(OrderChecklistItem(org_id=current.org_id, order_id=order_id, label=data.label,
                                  required=data.required, sort_order=base))
    else:
        raise HTTPException(422, "Provide either label or template_id")
    await db.commit()
    items = await _items(db, order_id)
    return {"items": [_ser_item(c) for c in items], "summary": _flat_summary(items)}


class ToggleIn(BaseModel):
    done: bool


@router.patch("/orders/{order_id}/checklist/{item_id}")
async def toggle_item(
    order_id: str,
    item_id: str,
    data: ToggleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    c = (await db.execute(select(OrderChecklistItem).where(
        OrderChecklistItem.id == item_id, OrderChecklistItem.order_id == order_id,
        OrderChecklistItem.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Checklist item not found")
    c.done = data.done
    c.done_by_id = current.id if data.done else None
    c.done_at = datetime.utcnow() if data.done else None
    await db.commit()
    items = await _items(db, order_id)
    return {"item": _ser_item(c), "summary": _flat_summary(items)}


@router.delete("/orders/{order_id}/checklist/{item_id}")
async def delete_item(
    order_id: str,
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    c = (await db.execute(select(OrderChecklistItem).where(
        OrderChecklistItem.id == item_id, OrderChecklistItem.order_id == order_id,
        OrderChecklistItem.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Checklist item not found")
    await db.delete(c)
    await db.commit()
    return {"ok": True}
