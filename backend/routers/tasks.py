"""Build 86 — Tasks, properly.

Until now a task was a title, an assignee and a due date. Orderry's tasks carry the
context the assignee actually needs — *what, when, where and for whom* — so a task
links to a work order, a client, a lead, a location and a parent task, and has an
event feed for comments and files. Completion records whether it landed **on time**,
which is what makes tasks payable (build 70's `term_condition` machinery).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.services.dates import parse_dt
from backend.models import Client, Employee, Order, Task
from backend.services.auth import get_current_employee
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
    if mine:
        q = q.where(or_(Task.assigned_to_id == current.id, Task.created_by_id == current.id))
    if overdue:
        q = q.where(Task.completed_at.is_(None), Task.due_at < now_utc())
    rows = (await db.execute(q)).scalars().all()

    far = datetime.max
    rows.sort(key=lambda t: (t.completed_at is not None, t.due_at or far))
    names = await _names(db, current.org_id)
    return {"items": [_ser(t, names) for t in rows], "statuses": STATUSES}


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
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    ids = list(dict.fromkeys(data.assignee_ids or []))   # de-dupe, keep order
    primary = data.assigned_to_id or (ids[0] if ids else None)
    if primary and primary not in ids:
        ids = [primary] + ids
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
    return _ser(t, await _names(db, current.org_id))


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, task_id)
    d = _ser(t, await _names(db, current.org_id))
    if t.order_id:
        o = (await db.execute(select(Order).where(Order.id == t.order_id))).scalar_one_or_none()
        d["order_number"] = o.order_number if o else None
    if t.client_id:
        c = (await db.execute(select(Client).where(Client.id == t.client_id))).scalar_one_or_none()
        d["client_name"] = c.name if c else None
    d["subtasks"] = [_ser(x) for x in (await db.execute(select(Task).where(
        Task.parent_task_id == t.id))).scalars().all()]
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
    prev_assignee, prev_status = t.assigned_to_id, t.status

    for f in ("title", "description", "priority", "assigned_to_id", "client_id", "order_id"):
        v = getattr(data, f)
        if v is not None:
            setattr(t, f, v)
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
    return _ser(t, await _names(db, current.org_id))


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, task_id)
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
