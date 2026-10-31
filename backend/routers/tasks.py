"""Build 86 — Tasks, properly.

Until now a task was a title, an assignee and a due date. Orderry's tasks carry the
context the assignee actually needs — *what, when, where and for whom* — so a task
links to a work order, a client, a lead, a location and a parent task, and has an
event feed for comments and files. Completion records whether it landed **on time**,
which is what makes tasks payable (build 70's `term_condition` machinery).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.services.dates import parse_dt
from backend.models import Client, Employee, Order, Task
from backend.services.auth import get_current_employee, has_perm
from backend.services.dates import now_utc

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

STATUSES = ["todo", "in_progress", "done"]


def _ser(t: Task, names: dict | None = None) -> dict:
    names = names or {}
    overdue = bool(t.due_at and not t.completed_at and t.due_at < now_utc())
    return {
        "id": t.id, "title": t.title, "description": t.description,
        "status": t.status,
        "priority": int(t.priority) if str(t.priority or "").lstrip("-").isdigit() else t.priority,
        "assigned_to_id": t.assigned_to_id, "assignee_name": names.get(t.assigned_to_id),
        "assignee_ids": list(t.assignee_ids or []),
        "assignee_names": [names.get(i) for i in (t.assignee_ids or []) if names.get(i)],
        "created_by_id": t.created_by_id, "author_name": names.get(t.created_by_id),
        "order_id": t.order_id, "client_id": t.client_id, "lead_id": t.lead_id,
        "location_id": t.location_id, "parent_task_id": t.parent_task_id,
        "due_at": t.due_at.isoformat() if t.due_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "completed_on_time": t.completed_on_time,
        "overdue": overdue,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def _names(db: AsyncSession, org_id: str) -> dict:
    return {e.id: e.name for e in (await db.execute(select(Employee).where(
        Employee.org_id == org_id))).scalars().all()}


@router.get("")
async def list_tasks(
    status: Optional[str] = None,
    assigned_to_id: Optional[str] = None,
    order_id: Optional[str] = None,
    client_id: Optional[str] = None,
    mine: bool = False,
    overdue: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Tasks sorted by due date — overdue first, then today, then upcoming (the order
    an assignee actually wants). `mine=true` gives the "My tasks" view."""
    q = select(Task).where(Task.org_id == current.org_id)
    if status:
        q = q.where(Task.status == status)
    if assigned_to_id:
        q = q.where(Task.assigned_to_id == assigned_to_id)
    if order_id:
        q = q.where(Task.order_id == order_id)
    if client_id:
        q = q.where(Task.client_id == client_id)
    if overdue:
        q = q.where(Task.completed_at.is_(None), Task.due_at < now_utc())
    rows = (await db.execute(q)).scalars().all()
    if mine:   # assigned to me (primary or one of several), or created by me
        rows = [t for t in rows if _involves(t, current.id)]

    far = datetime.max
    rows.sort(key=lambda t: (t.completed_at is not None, t.due_at or far))
    names = await _names(db, current.org_id)
    numbers = await _order_numbers(db, current.org_id, [t.order_id for t in rows])
    return {"items": [{**_ser(t, names), "order_number": numbers.get(t.order_id)} for t in rows],
            "statuses": STATUSES}


async def _order_numbers(db: AsyncSession, org_id: str, order_ids) -> dict:
    """J4 — job numbers for linked jobs, this shop's jobs only (a bad link never shows another
    shop's job)."""
    ids = {i for i in order_ids if i}
    if not ids:
        return {}
    return dict((await db.execute(select(Order.id, Order.order_number).where(
        Order.id.in_(ids), Order.org_id == org_id))).all())


class TaskIn(BaseModel):
    title: str
    description: Optional[str] = None
    assigned_to_id: Optional[str] = None
    assignee_ids: Optional[list[str]] = None   # 144ex: multiple technicians
    priority: int = 0
    due_at: Optional[str] = None
    order_id: Optional[str] = None
    client_id: Optional[str] = None
    lead_id: Optional[str] = None
    location_id: Optional[str] = None
    parent_task_id: Optional[str] = None


@router.post("", status_code=201)
async def create_task(
    data: TaskIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    ids = list(dict.fromkeys(data.assignee_ids or []))   # de-dupe, keep order
    primary = data.assigned_to_id or (ids[0] if ids else None)
    if primary and primary not in ids:
        ids = [primary] + ids
    await _check_assign(db, current, ids)
    from backend.services.idempotency import idem_begin
    claim = await idem_begin(db, current.org_id, request, "tasks.create")
    if claim.replay is not None:
        return claim.replay
    try:
        result = await _create_task(data, ids, primary, db, current)
    except BaseException:
        await claim.release(db)
        raise
    await claim.finish(db, result)
    return result


class PartRequestIn(BaseModel):
    order_id: str
    description: str = Field(min_length=1, max_length=200)
    qty: float = Field(default=1, gt=0, le=999)
    note: Optional[str] = Field(default=None, max_length=1000)


@router.post("/part-request", status_code=201)
async def request_part(
    data: PartRequestIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Tekly Go: a technician says a job needs a part ordered. Becomes an urgent task on the job
    for the job's manager (or for the whole shop when the job has none), so the person who orders
    stock sees it. Anyone working on the job may ask, which is why this doesn't need
    can_assign_staff the way handing someone a normal task does."""
    from backend.routers.orders import _guard_order_access
    order = (await db.execute(select(Order).where(
        Order.id == data.order_id, Order.org_id == current.org_id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _guard_order_access(db, current, order)
    from backend.services.idempotency import idem_begin
    claim = await idem_begin(db, current.org_id, request, "tasks.part_request")
    if claim.replay is not None:
        return claim.replay
    try:
        qty = f"{data.qty:g}"
        who = order.manager_id if order.manager_id and order.manager_id != current.id else None
        task = TaskIn(
            title=f"Order part: {data.description.strip()}{'' if qty == '1' else f' ×{qty}'} for {order.order_number}",
            description=(data.note or "").strip() or None, priority=1, order_id=order.id, client_id=order.client_id)
        result = await _create_task(task, [who] if who else [], who, db, current)
    except BaseException:
        await claim.release(db)
        raise
    await claim.finish(db, result)
    return result


async def _create_task(data: TaskIn, ids: list, primary, db: AsyncSession, current: Employee):
    t = Task(
        org_id=current.org_id, title=data.title, description=data.description,
        assigned_to_id=primary, assignee_ids=ids, created_by_id=current.id,
        order_id=data.order_id, client_id=data.client_id, lead_id=data.lead_id,
        location_id=data.location_id or current.location_id,
        parent_task_id=data.parent_task_id, priority=data.priority,
        due_at=parse_dt(data.due_at, "due_at") if data.due_at else None,
        status="todo")
    db.add(t)
    await db.flush()
    await _feed(db, current, t, f"Task created: {t.title}")
    await _notify(db, current, t, "assigned")
    await db.commit()
    await db.refresh(t)
    return {**_ser(t, await _names(db, current.org_id)),
            "order_number": (await _order_numbers(db, current.org_id, [t.order_id])).get(t.order_id)}


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, task_id)
    d = _ser(t, await _names(db, current.org_id))
    # J4 — linked records are read within this shop only, as a second line of defence behind
    # the tenancy guard that refuses cross-shop links when a task is saved.
    if t.order_id:
        o = (await db.execute(select(Order).where(
            Order.id == t.order_id, Order.org_id == current.org_id))).scalar_one_or_none()
        d["order_number"] = o.order_number if o else None
    if t.client_id:
        c = (await db.execute(select(Client).where(
            Client.id == t.client_id, Client.org_id == current.org_id))).scalar_one_or_none()
        d["client_name"] = c.name if c else None
    d["subtasks"] = [_ser(x) for x in (await db.execute(select(Task).where(
        Task.parent_task_id == t.id, Task.org_id == current.org_id))).scalars().all()]
    return d


class TaskPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
    assigned_to_id: Optional[str] = None
    assignee_ids: Optional[list[str]] = None   # 144ex: full technician set (replaces existing)
    due_at: Optional[str] = None
    client_id: Optional[str] = None
    order_id: Optional[str] = None


@router.patch("/{task_id}")
async def update_task(
    task_id: str, data: TaskPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, task_id)
    await _check_may_change(db, current, t)
    new_people = list(data.assignee_ids or []) + ([data.assigned_to_id] if data.assigned_to_id else [])
    if new_people:
        await _check_assign(db, current, [p for p in new_people if p not in (t.assignee_ids or []) and p != t.assigned_to_id])
    prev_assignee, prev_status = t.assigned_to_id, t.status

    for f in ("title", "description", "priority", "assigned_to_id", "client_id", "order_id"):
        v = getattr(data, f)
        if v is not None:
            # an empty link means "unlink" (the job picker's Change → clear)
            setattr(t, f, (v or None) if f in ("assigned_to_id", "client_id", "order_id") else v)
    if data.assignee_ids is not None:
        ids = list(dict.fromkeys(data.assignee_ids))
        # keep the primary owner inside the set (and default it if it fell out)
        if t.assigned_to_id and t.assigned_to_id not in ids:
            t.assigned_to_id = ids[0] if ids else None
        t.assignee_ids = ids
        await _feed(db, current, t, "Assignees updated")
    elif data.assigned_to_id is not None:
        # single-assignee edit keeps the list in step
        cur = list(t.assignee_ids or [])
        if data.assigned_to_id and data.assigned_to_id not in cur:
            t.assignee_ids = [data.assigned_to_id] + cur
    if data.due_at is not None:
        t.due_at = parse_dt(data.due_at, "due_at") if data.due_at else None
    if data.status is not None:
        if data.status not in STATUSES:
            raise HTTPException(422, f"status must be one of {STATUSES}")
        t.status = data.status
        if data.status == "done" and not t.completed_at:
            t.completed_at = now_utc()
            # on-time analysis: no due date means it can't be late
            t.completed_on_time = (t.due_at is None) or (t.completed_at <= t.due_at)
            await _feed(db, current, t, "Task completed"
                        + ("" if t.completed_on_time else " (late)"))
            await _notify(db, current, t, "completed")
            await _pay(db, current, t)
        elif data.status != "done":
            t.completed_at = None
            t.completed_on_time = None

    if data.assigned_to_id is not None and data.assigned_to_id != prev_assignee:
        await _feed(db, current, t, "Assignee changed")
        await _notify(db, current, t, "assigned")
    elif data.status is None or data.status == prev_status:
        await _notify(db, current, t, "changed")

    await db.commit()
    await db.refresh(t)
    return {**_ser(t, await _names(db, current.org_id)),
            "order_number": (await _order_numbers(db, current.org_id, [t.order_id])).get(t.order_id)}


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, task_id)
    await _check_may_change(db, current, t)
    await db.delete(t)
    await db.commit()
    return {"ok": True}


# ── event feed ───────────────────────────────────────────────────────────────
@router.get("/{task_id}/feed")
async def task_feed(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    await _load(db, current, task_id)
    from backend.models import EventFeedItem
    rows = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.org_id == current.org_id, EventFeedItem.entity_type == "task",
        EventFeedItem.entity_id == task_id)
        .order_by(EventFeedItem.created_at.desc()))).scalars().all()
    return {"items": [{
        "id": f.id, "body": f.body, "kind": f.item_type,
        "author_id": f.author_id, "author_name": f.author_name,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    } for f in rows]}


class CommentIn(BaseModel):
    body: str


@router.post("/{task_id}/comments", status_code=201)
async def add_comment(
    task_id: str, data: CommentIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, task_id)
    await _feed(db, current, t, data.body, kind="comment")
    await _notify(db, current, t, "changed")
    await db.commit()
    return {"ok": True}


# ── helpers ──────────────────────────────────────────────────────────────────
def _involves(t: Task, emp_id: str) -> bool:
    return emp_id in (t.assigned_to_id, t.created_by_id) or emp_id in (t.assignee_ids or [])


async def _check_assign(db: AsyncSession, current: Employee, people: list) -> None:
    """Giving work to someone else needs can_assign_staff; anyone may take a task themselves."""
    if any(p and p != current.id for p in people) and not await has_perm(db, current, "can_assign_staff"):
        raise HTTPException(403, "Your role can't assign tasks to other people. Assign it to yourself.")


async def _check_may_change(db: AsyncSession, current: Employee, t: Task) -> None:
    """Edit, complete or delete: the creator, an assignee, or someone who assigns work."""
    if not _involves(t, current.id) and not await has_perm(db, current, "can_assign_staff"):
        raise HTTPException(403, "Only the person who made this task, its assignees or a manager can change it.")


async def _load(db: AsyncSession, current: Employee, task_id: str) -> Task:
    t = (await db.execute(select(Task).where(
        Task.id == task_id, Task.org_id == current.org_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Task not found")
    return t


async def _feed(db: AsyncSession, current: Employee, t: Task, body: str,
                kind: str = "system") -> None:
    from backend.models import EventFeedItem
    db.add(EventFeedItem(org_id=current.org_id, entity_type="task", entity_id=t.id,
                         item_type=kind, body=body,
                         author_id=current.id, author_name=current.name))


async def _notify(db: AsyncSession, current: Employee, t: Task, event: str) -> None:
    """Internal notifications for tasks. Orderry's rule: you are never notified about
    something you did yourself."""
    try:
        from backend.services import notification_rules as nr
        await nr.notify_task(db, org_id=current.org_id, task=t, event=event,
                             actor_id=current.id)
    except Exception:
        pass


async def _pay(db: AsyncSession, current: Employee, t: Task) -> None:
    """Commission for closing a task — build 70's on_time/overdue term conditions
    already model exactly this, so a task_closed rule just reuses them."""
    try:
        from backend.services.payroll import CommissionEngine
        await CommissionEngine(db, current.org_id).accrue_for_task(t)
    except Exception:
        pass
