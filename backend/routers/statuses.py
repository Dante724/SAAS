"""
Work-order statuses — configurable, grouped, with per-status automation triggers.

Status GROUPS (what reports + triggers key off):
  new · in_progress · pending · ready · closed · dropped
Closed/dropped groups are terminal and open the payment window.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from pydantic import BaseModel
from typing import Optional
from backend.db.database import get_db
from backend.models import OrderStatus, Employee
from backend.services.auth import get_current_employee, require_perm, record_scope

router = APIRouter(prefix="/api/statuses", tags=["statuses"],
                   dependencies=[Depends(record_scope)])   # role record scope on {order_id}/{client_id}

GROUPS = ["new", "in_progress", "pending", "ready", "closed", "dropped"]
TERMINAL_GROUPS = {"closed", "dropped"}

# The default status set seeded for a new org (mirrors the old hardcoded list,
# now configurable). key, name, group, color, default, triggers...
DEFAULT_STATUSES = [
    # key, name, group, color, is_default, t_invoice, t_notify, t_payment, terminal
    ("new",            "New",            "new",         "#4f8ef7", True,  False, True,  False, False),
    ("diagnosed",      "Diagnosed",      "in_progress", "#f5a623", False, False, True,  False, False),
    ("waiting_parts",  "Waiting parts",  "pending",     "#f06daa", False, False, True,  False, False),
    ("in_repair",      "In repair",      "in_progress", "#9b7bf4", False, False, False, False, False),
    ("ready",          "Ready",          "ready",       "#2cd4c0", False, True,  True,  False, False),
    ("done",           "Done",           "closed",      "#3dca7a", False, True,  True,  True,  True),
    ("delivered",      "Delivered",      "closed",      "#2cd4c0", False, False, True,  True,  True),
    ("dropped",        "Not collected",  "dropped",     "#90a0c0", False, False, False, True,  True),
]

# Estimate status set (Orderry): Draft→In progress→Sent→Pending→Accepted→Order created→Canceled.
# tuple: key, name, group, color, shows_accept, converts_to_order
ESTIMATE_STATUSES = [
    ("est_draft",       "Draft",          "new",         "#4f8ef7", False, False),
    ("est_progress",    "In progress",    "in_progress", "#9b7bf4", False, False),
    ("est_sent",        "Sent",           "ready",       "#2cd4c0", True,  False),
    ("est_pending",     "Pending",        "pending",     "#f5a623", True,  False),
    ("est_accepted",    "Accepted",       "closed",      "#3dca7a", False, False),
    ("est_converted",   "Order created",  "closed",      "#1d8a4e", False, True),
    ("est_canceled",    "Canceled",       "dropped",     "#90a0c0", False, False),
]

# 144ij — three more Orderry pipelines, seeded to MIRROR the current hard-coded
# entity statuses so the config is truthful from day one (Lead / Invoice /
# StockTransfer). key must equal the entity's stored status string so the list
# view can look up colour+name. tuple: key, name, group, color, is_default, terminal
# Keys are namespaced (inq_/inv_/tr_) so they never collide with a work-order
# status key — order-status lookups elsewhere resolve by bare key and must stay
# unambiguous. The trailing comment records the entity status each mirrors.
RECORD_TYPE_DEFAULTS: dict[str, list[tuple]] = {
    # Lead.status: new|in_progress|won|lost
    "inquiry": [
        ("inq_new",         "New",          "new",         "#4f8ef7", True,  False),
        ("inq_in_progress", "In progress",  "in_progress", "#f5a623", False, False),
        ("inq_won",         "Won",          "closed",      "#3dca7a", False, True),
        ("inq_lost",        "Lost",         "dropped",     "#90a0c0", False, True),
    ],
    # Invoice.status: draft|issued|paid|canceled
    "invoice": [
        ("inv_draft",       "Draft",        "new",         "#4f8ef7", True,  False),
        ("inv_issued",      "Issued",       "in_progress", "#9b7bf4", False, False),
        ("inv_paid",        "Paid",         "closed",      "#3dca7a", False, True),
        ("inv_canceled",    "Canceled",     "dropped",     "#90a0c0", False, True),
    ],
    # StockTransfer.status: draft|ready|in_transit|received|completed|canceled
    "transfer": [
        ("tr_draft",        "Draft",        "new",         "#4f8ef7", True,  False),
        ("tr_ready",        "Ready to ship","in_progress", "#f5a623", False, False),
        ("tr_in_transit",   "In transit",   "pending",     "#f06daa", False, False),
        ("tr_received",     "Received",     "ready",       "#2cd4c0", False, False),
        ("tr_completed",    "Completed",    "closed",      "#1d8a4e", False, True),
        ("tr_canceled",     "Canceled",     "dropped",     "#90a0c0", False, True),
    ],
    # 144an — Purchase order WORKFLOW stage (manual, fully configurable — Orderry parity).
    # This is the human-driven pipeline the buyer moves the PO through; it is SEPARATE from
    # the automatic receipt state (PurchaseOrder.status: ordered/partial/received/cancelled),
    # which is driven only by actually receiving stock and never faked from a label. Keys are
    # po_* so they never collide with a work-order status key.
    "purchase_order": [
        ("po_draft",        "Draft",            "new",         "#4f8ef7", True,  False),
        ("po_awaiting",     "Awaiting approval","new",         "#f5a623", False, False),
        ("po_approved",     "Approved",         "in_progress", "#9b7bf4", False, False),
        ("po_ordered",      "Ordered",          "in_progress", "#4f8ef7", False, False),
        ("po_in_transit",   "In transit",       "pending",     "#f06daa", False, False),
        ("po_delayed",      "Delayed",          "pending",     "#e8833a", False, False),
        ("po_received",     "Received",         "ready",        "#2cd4c0", False, False),
        ("po_closed",       "Closed",           "closed",       "#1d8a4e", False, True),
        ("po_cancelled",    "Cancelled",        "dropped",      "#90a0c0", False, True),
    ],
}

RECORD_TYPES = ["work_order", "estimate", "inquiry", "invoice", "transfer", "purchase_order"]


def _ser(s: OrderStatus) -> dict:
    return {
        "id": s.id, "key": s.key, "name": s.name, "status_group": s.status_group,
        "color": s.color, "sort_order": s.sort_order, "is_terminal": s.is_terminal,
        "is_default": s.is_default, "triggers_invoice": s.triggers_invoice,
        "triggers_notification": s.triggers_notification, "triggers_payment": s.triggers_payment,
        "locks_edits": s.locks_edits, "active": s.active,
        "time_limit_min": s.time_limit_min, "moves_asset_to": s.moves_asset_to,
        "is_estimate": s.is_estimate, "shows_accept": s.shows_accept,
        "record_type": s.record_type, "converts_to_order": s.converts_to_order,
        "require_comment": s.require_comment, "require_payment": s.require_payment,
        "require_checklist": s.require_checklist,
        "allow_messages": s.allow_messages,
    }


async def ensure_seeded(db: AsyncSession, org_id: str):
    """Create the default status set for an org if it has none."""
    # 144in self-heal: earlier builds could leave estimate statuses mis-typed as
    # record_type='work_order' (startup auto-add defaulted the new column) AND a
    # correct estimate-typed copy, producing duplicate keys that crash bare-key
    # status lookups (MultipleResultsFound). Drop the mis-typed estimate orphans;
    # no-op on clean orgs. Estimate keys (est_*) never belong to work_order.
    heal = await db.execute(delete(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.record_type == "work_order",
        OrderStatus.key.like("est\\_%", escape="\\")))
    if heal.rowcount:
        await db.commit()
    existing = (await db.execute(
        select(func.count()).select_from(OrderStatus).where(OrderStatus.org_id == org_id)
    )).scalar() or 0
    if not existing:
        for i, (key, name, grp, color, dflt, t_inv, t_not, t_pay, term) in enumerate(DEFAULT_STATUSES):
            db.add(OrderStatus(
                org_id=org_id, key=key, name=name, status_group=grp, color=color,
                sort_order=i, is_default=dflt, triggers_invoice=t_inv,
                triggers_notification=t_not, triggers_payment=t_pay, is_terminal=term,
                record_type="work_order",
            ))
        await db.commit()
    # seed estimate statuses if missing (separate set)
    est_existing = (await db.execute(
        select(func.count()).select_from(OrderStatus).where(
            OrderStatus.org_id == org_id, OrderStatus.record_type == "estimate")
    )).scalar() or 0
    if not est_existing:
        for i, (key, name, grp, color, accept, conv) in enumerate(ESTIMATE_STATUSES):
            db.add(OrderStatus(
                org_id=org_id, key=key, name=name, status_group=grp, color=color,
                sort_order=i, is_estimate=True, record_type="estimate",
                shows_accept=accept, converts_to_order=conv,
                is_terminal=grp in ("closed", "dropped"),
                triggers_notification=(key == "est_sent"),
            ))
        await db.commit()
    # 144ij — seed the three newer Orderry pipelines, each independently.
    for rt, rows in RECORD_TYPE_DEFAULTS.items():
        rt_existing = (await db.execute(
            select(func.count()).select_from(OrderStatus).where(
                OrderStatus.org_id == org_id, OrderStatus.record_type == rt)
        )).scalar() or 0
        if not rt_existing:
            for i, (key, name, grp, color, dflt, term) in enumerate(rows):
                db.add(OrderStatus(
                    org_id=org_id, key=key, name=name, status_group=grp, color=color,
                    sort_order=i, is_default=dflt, is_terminal=term, record_type=rt,
                ))
            await db.commit()


@router.get("")
async def list_statuses(
    is_estimate: bool = False,
    record_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    # record_type wins when given; else derive from the legacy is_estimate flag.
    rt = record_type or ("estimate" if is_estimate else "work_order")
    if rt not in RECORD_TYPES:
        raise HTTPException(400, f"record_type must be one of {RECORD_TYPES}")
    await ensure_seeded(db, current.org_id)
    rows = (await db.execute(
        select(OrderStatus).where(OrderStatus.org_id == current.org_id, OrderStatus.active == True,  # noqa: E712
                                  OrderStatus.record_type == rt)
        .order_by(OrderStatus.sort_order)
    )).scalars().all()
    counts = await _job_counts(db, current.org_id, rt) if rt in ("work_order", "estimate") else None
    items = []
    for s in rows:
        d = _ser(s)
        if counts is not None:
            cur, recent = counts
            # Open statuses: jobs in them now. Closed ones: jobs that closed there in the last 30 days.
            d["job_count"] = recent.get(s.key, 0) if s.is_terminal else cur.get(s.key, 0)
            d["in_use"] = cur.get(s.key, 0)
        items.append(d)
    return {"groups": GROUPS, "record_types": RECORD_TYPES, "items": items}


async def _job_counts(db: AsyncSession, org_id: str, rt: str) -> tuple[dict, dict]:
    from datetime import datetime, timedelta

    from backend.models import Order
    base = (Order.org_id == org_id, Order.archived == False, Order.is_estimate == (rt == "estimate"))  # noqa: E712
    cur = dict((await db.execute(select(Order.status, func.count()).where(*base).group_by(Order.status))).all())
    since = datetime.utcnow() - timedelta(days=30)
    recent = dict((await db.execute(select(Order.status, func.count()).where(
        *base, Order.closed_at >= since).group_by(Order.status))).all())
    return cur, recent


class StatusIn(BaseModel):
    name: str
    key: Optional[str] = None
    status_group: str = "in_progress"
    color: str = "#888888"
    is_default: bool = False
    triggers_invoice: bool = False
    triggers_notification: bool = False
    triggers_payment: bool = False
    locks_edits: bool = False
    time_limit_min: Optional[int] = None
    moves_asset_to: Optional[str] = None
    is_estimate: bool = False
    record_type: Optional[str] = None  # wins over is_estimate when given
    converts_to_order: bool = False
    shows_accept: bool = False
    require_comment: bool = False
    require_payment: bool = False
    require_checklist: bool = False
    allow_messages: Optional[bool] = None   # None = automatic (see services/messaging_rule)


def _resolve_rt(data: "StatusIn") -> str:
    """record_type takes precedence; else derive from the legacy is_estimate flag."""
    rt = data.record_type or ("estimate" if data.is_estimate else "work_order")
    if rt not in RECORD_TYPES:
        raise HTTPException(400, f"record_type must be one of {RECORD_TYPES}")
    return rt


def _slugify(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_") or "status"


@router.post("", status_code=201)
async def create_status(
    data: StatusIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.status_group not in GROUPS:
        raise HTTPException(400, f"Group must be one of {GROUPS}")
    rt = _resolve_rt(data)
    key = data.key or _slugify(data.name)
    # ensure unique key within org (keys are org-global; suffix on collision)
    dupe = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == current.org_id, OrderStatus.key == key, OrderStatus.active == True))).scalar_one_or_none()  # noqa: E712
    if dupe:
        key = f"{key}_{(await db.execute(select(func.count()).select_from(OrderStatus).where(OrderStatus.org_id == current.org_id))).scalar()}"
    if data.is_default:
        for s in (await db.execute(select(OrderStatus).where(OrderStatus.org_id == current.org_id, OrderStatus.is_default == True, OrderStatus.record_type == rt))).scalars().all():  # noqa: E712
            s.is_default = False
    maxsort = (await db.execute(select(func.max(OrderStatus.sort_order)).where(OrderStatus.org_id == current.org_id))).scalar() or 0
    s = OrderStatus(
        org_id=current.org_id, key=key, name=data.name, status_group=data.status_group,
        color=data.color, sort_order=maxsort + 1, is_default=data.is_default,
        record_type=rt, is_estimate=(rt == "estimate"), converts_to_order=data.converts_to_order,
        shows_accept=data.shows_accept,
        is_terminal=data.status_group in TERMINAL_GROUPS,
        triggers_invoice=data.triggers_invoice, triggers_notification=data.triggers_notification,
        triggers_payment=data.triggers_payment, locks_edits=data.locks_edits,
        time_limit_min=data.time_limit_min, moves_asset_to=data.moves_asset_to,
        require_comment=data.require_comment, require_payment=data.require_payment,
        require_checklist=data.require_checklist, allow_messages=data.allow_messages,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return _ser(s)


@router.put("/{status_id}")
async def update_status(
    status_id: str, data: StatusIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    s = (await db.execute(select(OrderStatus).where(OrderStatus.id == status_id, OrderStatus.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Status not found")
    if data.is_default and not s.is_default:
        for o in (await db.execute(select(OrderStatus).where(OrderStatus.org_id == current.org_id, OrderStatus.is_default == True, OrderStatus.record_type == s.record_type))).scalars().all():  # noqa: E712
            o.is_default = False
    s.name = data.name
    s.status_group = data.status_group
    s.color = data.color
    s.is_default = data.is_default
    s.is_terminal = data.status_group in TERMINAL_GROUPS
    s.triggers_invoice = data.triggers_invoice
    s.triggers_notification = data.triggers_notification
    s.triggers_payment = data.triggers_payment
    s.locks_edits = data.locks_edits
    s.time_limit_min = data.time_limit_min
    s.moves_asset_to = data.moves_asset_to
    s.converts_to_order = data.converts_to_order
    s.shows_accept = data.shows_accept
    s.require_comment = data.require_comment
    s.require_payment = data.require_payment
    s.require_checklist = data.require_checklist
    if "allow_messages" in data.model_fields_set:
        s.allow_messages = data.allow_messages
    await db.commit()
    return _ser(s)


class ReorderIn(BaseModel):
    ordered_ids: list[str]


@router.post("/reorder")
async def reorder_statuses(
    data: ReorderIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    for i, sid in enumerate(data.ordered_ids):
        s = (await db.execute(select(OrderStatus).where(OrderStatus.id == sid, OrderStatus.org_id == current.org_id))).scalar_one_or_none()
        if s:
            s.sort_order = i
    await db.commit()
    return {"ok": True}


@router.delete("/{status_id}")
async def delete_status(
    status_id: str,
    move_to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Retire a status. With ``move_to`` (another status key of the same type), jobs currently
    in it move there first — a plain status change: it's logged, but no automations run."""
    s = (await db.execute(select(OrderStatus).where(OrderStatus.id == status_id, OrderStatus.org_id == current.org_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Status not found")
    if s.is_default:
        raise HTTPException(400, "Cannot delete the default status")
    if move_to:
        if s.record_type not in ("work_order", "estimate"):
            raise HTTPException(400, "Only work order and estimate statuses hold jobs")
        target = (await db.execute(select(OrderStatus).where(
            OrderStatus.org_id == current.org_id, OrderStatus.key == move_to, OrderStatus.active == True,  # noqa: E712
            OrderStatus.record_type == s.record_type, OrderStatus.id != s.id))).scalars().first()
        if not target:
            raise HTTPException(400, "Pick another active status to move the jobs to")
        from datetime import datetime

        from backend.models import Order, OrderStatusLog
        now = datetime.utcnow()
        orders = (await db.execute(select(Order).where(
            Order.org_id == current.org_id, Order.status == s.key,
            Order.is_estimate == (s.record_type == "estimate")))).scalars().all()
        for o in orders:
            o.status = target.key
            o.status_entered_at = now
            if target.is_terminal and not o.closed_at:
                o.closed_at = now
            db.add(OrderStatusLog(order_id=o.id, from_status=s.key, to_status=target.key,
                                  changed_by_id=current.id, note=f"Status \"{s.name}\" was removed"))
    s.active = False
    await db.commit()
    return {"ok": True}


# ════════ Phase B.7 — Transition rules ════════
from backend.models import StatusTransition


@router.get("/transitions")
async def list_transitions(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(StatusTransition).where(StatusTransition.org_id == current.org_id))).scalars().all()
    return {"items": [{"id": t.id, "from_status_id": t.from_status_id,
                       "to_status_id": t.to_status_id, "role_id": t.role_id} for t in rows]}


class TransitionIn(BaseModel):
    from_status_id: str
    to_status_id: str
    role_id: Optional[str] = None


@router.post("/transitions", status_code=201)
async def create_transition(
    data: TransitionIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = StatusTransition(org_id=current.org_id, from_status_id=data.from_status_id,
                         to_status_id=data.to_status_id, role_id=data.role_id)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"id": t.id}


@router.delete("/transitions/{tid}")
async def delete_transition(
    tid: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    t = (await db.execute(select(StatusTransition).where(StatusTransition.id == tid, StatusTransition.org_id == current.org_id))).scalar_one_or_none()
    if t:
        await db.delete(t)
        await db.commit()
    return {"ok": True}


@router.get("/allowed/{order_id}")
async def allowed_next(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Statuses the current order can move to (respecting transition rules + role).
    If no rules are defined from the current status, all statuses are allowed."""
    from backend.models import Order
    o = (await db.execute(select(Order).where(Order.id == order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not o:
        raise HTTPException(404, "Order not found")
    statuses = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == current.org_id, OrderStatus.active == True))).scalars().all()  # noqa: E712
    cur = next((s for s in statuses if s.key == o.status), None)
    if not cur:
        return {"items": [s.key for s in statuses]}
    rules = (await db.execute(select(StatusTransition).where(
        StatusTransition.org_id == current.org_id, StatusTransition.from_status_id == cur.id))).scalars().all()
    if not rules:
        return {"items": [s.key for s in statuses]}  # no rules = unrestricted
    allowed_ids = {r.to_status_id for r in rules if r.role_id in (None, current.role_id)}
    return {"items": [s.key for s in statuses if s.id in allowed_ids or s.id == cur.id]}


async def is_transition_allowed(db, org_id, role_id, from_key, to_key) -> bool:
    statuses = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.active == True))).scalars().all()  # noqa: E712
    sfrom = next((s for s in statuses if s.key == from_key), None)
    sto = next((s for s in statuses if s.key == to_key), None)
    if not sfrom or not sto:
        return True
    rules = (await db.execute(select(StatusTransition).where(
        StatusTransition.org_id == org_id, StatusTransition.from_status_id == sfrom.id))).scalars().all()
    if not rules:
        return True  # unrestricted when no rules from this status
    return any(r.to_status_id == sto.id and r.role_id in (None, role_id) for r in rules)
