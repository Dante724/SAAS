"""Build 86 — Tasks, properly.

Until now a task was a title, an assignee and a due date. Orderry's tasks carry the
context the assignee actually needs — *what, when, where and for whom* — so a task
links to a work order, a client, a lead, a location and a parent task, and has an
event feed for comments and files. Completion records whether it landed **on time**,
which is what makes tasks payable (build 70's `term_condition` machinery).
"""
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.core.paging import LIMIT_CEIL, clamp
from backend.db.database import get_db
from backend.models import (
    TASK_CLOSED,
    TASK_STATUSES,
    Client,
    Employee,
    Order,
    Task,
    normalise_task_priority,
)
from backend.services.auth import get_current_employee, has_perm
from backend.services.dates import (
    day_end_utc,
    day_start_utc,
    now_utc,
    parse_date,
    parse_dt,
    range_edge,
    shop_tzname,
    today_in,
    utc_to_wall,
    wall_to_utc,
)
from backend.services.task_visibility import (
    assigned_sql,
    involves_sql,
    own_day_sql,
    sees_all,
    unassigned_sql,
    visibility_conds,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
log = logging.getLogger("tekly.tasks")

STATUSES = list(TASK_STATUSES)   # todo|in_progress|done|cancelled
END_OF_DAY = time(23, 59, 59)    # a date-only due date is the end of that shop day (range_edge)
MY_DAY_CAP = 100                 # rows per My day list; counts are always the true totals


def _ser(t: Task, names: dict | None = None) -> dict:
    names = names or {}
    overdue = bool(t.due_at and not t.completed_at and t.status not in TASK_CLOSED
                   and t.due_at < now_utc())
    return {
        "id": t.id, "title": t.title, "description": t.description,
        "status": t.status,
        "priority": normalise_task_priority(t.priority),
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
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "archived_at": t.archived_at.isoformat() if t.archived_at else None,
        "source": t.source or "manual", "source_ref": t.source_ref,
    }


async def _names(db: AsyncSession, org_id: str) -> dict:
    return {e.id: e.name for e in (await db.execute(select(Employee).where(
        Employee.org_id == org_id))).scalars().all()}


async def _items(db: AsyncSession, org_id: str, rows) -> list[dict]:
    """List items for `rows` with names, job numbers and client names batch-loaded (three queries
    whatever the row count). The one shape GET /api/tasks and My day both return."""
    names = await _names(db, org_id)
    numbers = await _order_numbers(db, org_id, [t.order_id for t in rows])
    clients = await _client_names(db, org_id, rows)
    return [{**_ser(t, names), "order_number": numbers.get(t.order_id),
             "client_name": clients.get(t.id)} for t in rows]


async def _shop_tz(db: AsyncSession, org_id: str) -> str:
    """The clock a task's day is counted on: the shop's zone, the same rule the reports use for a
    date-only day (dates.shop_tzname with no branch filter; Asia/Kolkata for a new shop, UTC when
    unset or unknown)."""
    return await shop_tzname(db, org_id)


async def _parse_due(db: AsyncSession, current: Employee, value) -> Optional[datetime]:
    """due_at from a request, as the naive UTC instant tasks store (docs/ADR-tz-timestamps.md).

    - a date alone (YYYY-MM-DD): the end of that shop day (23:59:59 shop time, dates.range_edge,
      as the reports read a "To" date);
    - a datetime with an offset ("…Z", "+05:30"): that instant;
    - a datetime without an offset: **shop wall time** (what a datetime-local field, and every Go
      build before the shop-clock fix, sends), converted with that day's offset.
    Empty clears it; anything else is a 400."""
    if not value:
        return None
    v = str(value).strip()
    tz = await _shop_tz(db, current.org_id)
    if len(v) == 10:
        parse_date(v, "due_at")   # a clean 400 for a malformed date
        return range_edge(v, tz, end=True)
    dt = parse_dt(v, "due_at")
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return wall_to_utc(dt, tz)


def _set_due(t: Task, new_due: Optional[datetime]) -> bool:
    """Change the due time. The overdue alert re-arms only when the task is no longer overdue:
    the due time is cleared, or moves later AND into the future (OVD-4). Moving it to a time that
    has already passed keeps the earlier alert, so nobody is pinged again for the same lateness.
    Returns True when the caller must also forget who was told (task_alerts.rearm)."""
    rearm = new_due is None or (new_due > now_utc() and (t.due_at is None or new_due > t.due_at))
    if rearm:
        t.overdue_alerted_at = None
    t.due_at = new_due
    return rearm


def _pushes(db: AsyncSession) -> list:
    """Pushes collected during this request (push.notify(deferred=…)); sent by _send_pushes only
    after the request's final commit, so a push never commits half a request and the blocking
    web-push call never runs inside it."""
    return db.info.setdefault("task_pushes", [])


async def _send_pushes(db: AsyncSession) -> None:
    items = db.info.pop("task_pushes", [])
    if items:
        from backend.services.push import dispatch_deferred
        await dispatch_deferred(db, items)


@router.get("")
async def list_tasks(
    status: Optional[str] = None,
    assigned_to_id: Optional[str] = None,
    order_id: Optional[str] = None,
    client_id: Optional[str] = None,
    mine: bool = False,
    overdue: bool = False,
    q: Optional[str] = Query(None, max_length=200),
    assignee: Optional[str] = None,
    priority: Optional[str] = None,
    archived: bool = False,
    limit: Optional[int] = Query(None, ge=1, le=LIMIT_CEIL),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Tasks sorted the way an assignee works: open before closed, then by due date (no date last).

    Filters (all optional, combinable; unknown parameters are ignored):
      status    one status or a comma list (todo,in_progress)
      q         title, description, linked job number or client name
      assignee  "me" (assigned to me or made by me, like `mine=true`) or an employee id
      priority  0-3 or normal/high/urgent; a comma list is fine
      overdue   due date passed and still open
      archived  true lists deleted (archived) tasks instead, for restore
      limit/offset  a page; `total` is always the full match count
    With no parameters the response keeps its old shape: every live task, plus `statuses`."""
    oc = aliased(Client)
    cond = [Task.org_id == current.org_id,
            Task.archived_at.is_not(None) if archived else Task.archived_at.is_(None),
            *await visibility_conds(db, current)]
    if status:
        cond.append(Task.status.in_([x.strip() for x in status.split(",") if x.strip()]))
    if assigned_to_id:
        cond.append(Task.assigned_to_id == assigned_to_id)
    if order_id:
        cond.append(Task.order_id == order_id)
    if client_id:
        cond.append(Task.client_id == client_id)
    if overdue:
        cond += [Task.completed_at.is_(None), Task.status.notin_(TASK_CLOSED), Task.due_at < now_utc()]
    if mine or (assignee or "").strip().lower() == "me":
        cond.append(involves_sql(current.id))
    elif assignee and assignee.strip():
        cond.append(assigned_sql(assignee.strip()))
    if priority not in (None, ""):
        try:
            levels = sorted({normalise_task_priority(x.strip()) for x in priority.split(",") if x.strip()})
        except ValueError:
            raise HTTPException(422, "priority must be 0-3 or normal/high/urgent")
        cond.append(Task.priority.in_(levels))
    base = (select(Task)
            .outerjoin(Order, and_(Order.id == Task.order_id, Order.org_id == current.org_id))
            .outerjoin(Client, and_(Client.id == Task.client_id, Client.org_id == current.org_id))
            .outerjoin(oc, and_(oc.id == Order.client_id, oc.org_id == current.org_id))
            .where(*cond))
    term = (q or "").strip()
    if term:
        base = base.where(or_(*[col.icontains(term, autoescape=True) for col in (
            Task.title, Task.description, Order.order_number, Client.name, oc.name)]))
    total = (await db.execute(select(func.count()).select_from(
        base.with_only_columns(Task.id).subquery()))).scalar_one()
    ordered = base.order_by(
        case((Task.status.in_(TASK_CLOSED), 1), else_=0),
        case((Task.due_at.is_(None), 1), else_=0), Task.due_at, Task.created_at.desc(), Task.id)
    if limit is not None:
        ordered = ordered.limit(clamp(limit))
    if offset:
        ordered = ordered.offset(offset)
    rows = (await db.execute(ordered)).scalars().all()
    out = {"items": await _items(db, current.org_id, rows), "statuses": STATUSES, "total": total}
    if limit is not None:
        out.update(limit=clamp(limit), offset=offset)
    return out


async def _client_names(db: AsyncSession, org_id: str, rows) -> dict:
    """task id → client name: the task's own client, else the linked job's client."""
    oids = {t.order_id for t in rows if t.order_id and not t.client_id}
    order_client = dict((await db.execute(select(Order.id, Order.client_id).where(
        Order.id.in_(oids), Order.org_id == org_id))).all()) if oids else {}
    cids = {t.client_id for t in rows if t.client_id} | {c for c in order_client.values() if c}
    names = dict((await db.execute(select(Client.id, Client.name).where(
        Client.id.in_(cids), Client.org_id == org_id))).all()) if cids else {}
    return {t.id: names.get(t.client_id or order_client.get(t.order_id)) for t in rows}


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
    priority: Union[int, str] = 0              # 0-3, or the legacy normal/high/urgent
    due_at: Optional[str] = None
    order_id: Optional[str] = None
    client_id: Optional[str] = None
    lead_id: Optional[str] = None
    location_id: Optional[str] = None
    parent_task_id: Optional[str] = None

    @field_validator("priority", mode="before")
    @classmethod
    def _priority(cls, v):
        return normalise_task_priority(v)


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
        result = await _create_task(task, [who] if who else [], who, db, current, source="go_part_request")
    except BaseException:
        await claim.release(db)
        raise
    await claim.finish(db, result)
    return result


async def _create_task(data: TaskIn, ids: list, primary, db: AsyncSession, current: Employee,
                       source: str = "manual"):
    t = Task(
        org_id=current.org_id, title=data.title, description=data.description,
        assigned_to_id=primary, assignee_ids=ids, created_by_id=current.id,
        order_id=data.order_id, client_id=data.client_id, lead_id=data.lead_id,
        location_id=data.location_id or current.location_id,
        parent_task_id=data.parent_task_id, priority=data.priority,
        due_at=await _parse_due(db, current, data.due_at),
        status="todo", source=source)
    db.add(t)
    await db.flush()
    await _feed(db, current, t, f"Task created: {t.title}")
    await _notify(db, current, t, "assigned", recipients=ids)
    await db.commit()
    await _send_pushes(db)
    await db.refresh(t)
    return {**_ser(t, await _names(db, current.org_id)),
            "order_number": (await _order_numbers(db, current.org_id, [t.order_id])).get(t.order_id)}


# ── My day ───────────────────────────────────────────────────────────────────
MY_DAY_BUCKETS = ("catch_up", "today", "upcoming", "later", "done_today")


@router.get("/my-day")
async def my_day(
    assignee: str = Query("me", max_length=64),
    include_pool: bool = False,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """One person's day on the shop's clock (dates.shop_tzname: the shop's zone, as the reports).

    assignee  "me" (default): tasks assigned to me (primary or extra assignee) plus unassigned
              tasks I made (my own to-dos). Your own id is the same as "me".
              "<employee_id>": that person's day, same rule. "all": every live task in the shop.
              Both need can_assign_staff (403 otherwise); an id that isn't an employee of this
              shop is a 404 (another shop's employee looks exactly like a missing one).
    Buckets (open = todo or in_progress, not deleted):
      catch_up    open, due before today's start           oldest due first, then priority
      today       open, due today                          by due time, then priority (high first)
      upcoming    open, due in the 7 days after today      by due time, then priority
      later       open, no due date or due after that      newest first
      done_today  status done, completed today             most recent first
    include_pool=true (with "me" or your own id): also the shop's unassigned tasks, whoever made
              them, so the screen can offer them to pick up (PATCH assigned_to_id=<me> claims
              one). Default false. Pool items are the ones with no assigned_to_id and no
              assignee_ids.
    Each list holds at most 100 items (the GET /api/tasks item shape); `counts` are true totals.
    One query ranks and counts every bucket (window functions); names, job numbers and clients are
    then batch-loaded for all rows at once."""
    tz = await _shop_tz(db, current.org_id)
    today = today_in(tz)
    t0, t1 = day_start_utc(today, tz), day_end_utc(today, tz)
    t8 = day_end_utc(today + timedelta(days=7), tz)

    who = (assignee or "me").strip() or "me"
    cond = [Task.org_id == current.org_id, Task.archived_at.is_(None)]
    if who in ("me", current.id):
        cond.append(or_(own_day_sql(current.id), unassigned_sql()) if include_pool
                    else own_day_sql(current.id))
    elif not await sees_all(db, current):
        raise HTTPException(403, "Only a manager can see someone else's day.")
    elif who != "all":
        known = (await db.execute(select(Employee.id).where(
            Employee.id == who, Employee.org_id == current.org_id))).scalar_one_or_none()
        if not known:
            raise HTTPException(404, "Employee not found")
        cond.append(own_day_sql(who))

    is_open = Task.status.notin_(TASK_CLOSED)
    done_today = and_(Task.status == "done", Task.completed_at >= t0, Task.completed_at < t1)
    bucket = case(
        (and_(is_open, Task.due_at < t0), "catch_up"),
        (and_(is_open, Task.due_at < t1), "today"),
        (and_(is_open, Task.due_at < t8), "upcoming"),
        (is_open, "later"),
        else_="done_today")
    dated = bucket.in_(("catch_up", "today", "upcoming"))
    ranked = (select(
        Task.id.label("id"), bucket.label("bucket"),
        func.row_number().over(partition_by=bucket, order_by=(
            case((dated, Task.due_at)), case((dated, Task.priority), else_=0).desc(),
            case((bucket == "later", Task.created_at)).desc(),
            case((bucket == "done_today", Task.completed_at)).desc(), Task.id)).label("rn"),
        func.count().over(partition_by=bucket).label("total"))
        .where(*cond, or_(is_open, done_today)).subquery())
    found = (await db.execute(
        select(Task, ranked.c.bucket, ranked.c.total).join(ranked, ranked.c.id == Task.id)
        .where(Task.org_id == current.org_id, ranked.c.rn <= MY_DAY_CAP)
        .order_by(ranked.c.bucket, ranked.c.rn))).all()

    counts = dict.fromkeys(MY_DAY_BUCKETS, 0)
    for _t, key, total in found:
        counts[key] = int(total)
    items = await _items(db, current.org_id, [t for t, _k, _n in found])
    out = {"date": today.isoformat(), **{key: [] for key in MY_DAY_BUCKETS}}
    for (_t, key, _n), item in zip(found, items):
        out[key].append(item)
    out["counts"] = counts
    return out


class MoveIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/move-to-today")
async def move_to_today(
    data: MoveIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Bring tasks into today (My day's "move all to today"). 1-500 ids (duplicates ignored),
    else 422.

    Each open task you may change (assigned to you or made by you; any task for a manager) gets
    today's date on the shop's clock. A task keeps its time of day when that time is still ahead
    today; otherwise (already past, no due date, or date-only) it gets the end of today, 23:59:59
    shop time, so a moved task is never overdue straight away. Its overdue alert re-arms only
    when the new due time is in the future. Everything else is left alone and listed in `skipped`: ids that are unknown, in another
    shop, hidden from you, not yours to change, done, cancelled or deleted (one list, so a hidden
    task looks exactly like a missing one). All in one transaction. One feed line per task; instead
    of one "Task updated" per task, each other person involved gets a single alert for the move.

    Response: {"moved": n, "skipped": [ids]}"""
    ids = list(dict.fromkeys(i for i in data.ids if i))
    cond = [Task.org_id == current.org_id, Task.id.in_(ids), Task.archived_at.is_(None),
            Task.status.notin_(TASK_CLOSED)]
    if not await sees_all(db, current):
        cond.append(involves_sql(current.id))
    rows = (await db.execute(select(Task).where(*cond).order_by(Task.id))).scalars().all()
    moved_ids = {t.id for t in rows}
    skipped = [i for i in ids if i not in moved_ids]
    if not rows:
        return {"moved": 0, "skipped": skipped}
    tz = await _shop_tz(db, current.org_id)
    today = today_in(tz)
    end_of_today = wall_to_utc(datetime.combine(today, END_OF_DAY), tz)
    now = now_utc()
    from backend.services.notification_rules import task_people
    from backend.services.task_alerts import rearm
    told: dict[str, list[Task]] = {}
    rearmed = []
    for t in rows:
        new_due = end_of_today
        if t.due_at:
            kept = wall_to_utc(datetime.combine(today, utc_to_wall(t.due_at, tz).time()), tz)
            if kept > now:      # its time of day is still ahead today: keep it
                new_due = kept
        if _set_due(t, new_due):
            rearmed.append(t.id)
        await _feed(db, current, t, "Due date moved to today")
        for person in task_people(t) | ({t.created_by_id} if t.created_by_id else set()):
            if person != current.id:
                told.setdefault(person, []).append(t)
    await rearm(db, current.org_id, rearmed)
    for person, tasks in told.items():
        await _notify_moved(db, current, person, tasks)
    await db.commit()
    await _send_pushes(db)
    return {"moved": len(rows), "skipped": skipped}


async def _notify_moved(db: AsyncSession, current: Employee, person: str, tasks: list) -> None:
    """One alert per person for a move-to-today batch: the usual "Task updated" when it is one
    task, else one summary that opens the first of them. Never the person who moved them."""
    try:
        if len(tasks) == 1:
            from backend.services import notification_rules as nr
            await nr.notify_task_bell(db, org_id=current.org_id, task=tasks[0], event="changed",
                                      actor_id=current.id, recipients={person}, deferred=_pushes(db))
            return
        from backend.models import Employee as _Emp
        from backend.services.push import notify as push_notify
        active = (await db.execute(select(_Emp.id).where(
            _Emp.id == person, _Emp.org_id == current.org_id, _Emp.active == True))).scalar_one_or_none()  # noqa: E712
        if active:
            await push_notify(db, org_id=current.org_id, employee_id=person, kind="task.changed",
                              title="Tasks moved to today",
                              body=f"{current.name} moved {len(tasks)} tasks to today",
                              deep_link=f"/tasks?task={tasks[0].id}", commit=False,
                              deferred=_pushes(db))
    except Exception:  # noqa: BLE001 — logged; the move still succeeds
        log.warning("move-to-today notification failed (employee=%s)", person, exc_info=True)


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
        Task.parent_task_id == t.id, Task.org_id == current.org_id,
        Task.archived_at.is_(None), *await visibility_conds(db, current)))).scalars().all()]
    return d


class TaskPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[Union[int, str]] = None   # 0-3, or the legacy normal/high/urgent
    assigned_to_id: Optional[str] = None
    assignee_ids: Optional[list[str]] = None   # 144ex: full technician set (replaces existing)
    due_at: Optional[str] = None
    client_id: Optional[str] = None
    order_id: Optional[str] = None

    @field_validator("priority", mode="before")
    @classmethod
    def _priority(cls, v):
        return None if v is None else normalise_task_priority(v)


@router.patch("/{task_id}")
async def update_task(
    task_id: str, data: TaskPatch,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, task_id)
    if not _is_self_claim(t, data, current):
        await _check_may_change(db, current, t)
    if t.archived_at is not None:
        raise HTTPException(409, "This task was deleted. Restore it before changing it.")
    if data.status is not None and data.status not in STATUSES:
        raise HTTPException(422, f"status must be one of {STATUSES}")
    new_people = list(data.assignee_ids or []) + ([data.assigned_to_id] if data.assigned_to_id else [])
    if new_people:
        await _check_assign(db, current, [p for p in new_people if p not in (t.assignee_ids or []) and p != t.assigned_to_id])
    prev_assignee, prev_status = t.assigned_to_id, t.status
    from backend.services.notification_rules import task_people
    prev_people = task_people(t)

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
    if data.due_at is not None and _set_due(t, await _parse_due(db, current, data.due_at)):
        from backend.services.task_alerts import rearm
        await rearm(db, current.org_id, [t.id])
    if data.status is not None:
        t.status = data.status
        if data.status == "done" and (prev_status != "done" or not t.completed_at):
            t.completed_at = now_utc()
            # on-time analysis: no due date means it can't be late
            t.completed_on_time = (t.due_at is None) or (t.completed_at <= t.due_at)
            await _feed(db, current, t, "Task completed"
                        + ("" if t.completed_on_time else " (late)"))
            await _notify(db, current, t, "completed")
            await _pay(db, current, t)
        elif data.status == "cancelled" and prev_status != "cancelled":
            t.completed_at, t.completed_on_time = now_utc(), None
            await _feed(db, current, t, "Task cancelled")
        elif data.status not in TASK_CLOSED:
            t.completed_at = None
            t.completed_on_time = None
        if prev_status == "done" and data.status != "done":
            # reopened or cancelled: take back what closing it paid, per person (closing it again
            # pays the people assigned then, under <key>@n, so each nets exactly one payout)
            await _unpay(db, current, t, "reopened" if data.status != "cancelled" else "cancelled")

    if data.assigned_to_id is not None and data.assigned_to_id != prev_assignee:
        await _feed(db, current, t, "Assignee changed")
    # Alerts: people who were just added hear "assigned" (once, never again on later edits);
    # other edits are a "changed", which the bell collapses while one is still unread.
    added = task_people(t) - prev_people
    if added and t.overdue_alerted_at is not None:
        t.overdue_alerted_at = None   # the overdue sweep alerts the newcomers (only them) next tick
    if added:
        await _notify(db, current, t, "assigned", recipients=added)
    elif data.status is None or data.status == prev_status:
        await _notify(db, current, t, "changed")

    await db.commit()
    await _send_pushes(db)
    await db.refresh(t)
    return {**_ser(t, await _names(db, current.org_id)),
            "order_number": (await _order_numbers(db, current.org_id, [t.order_id])).get(t.order_id)}


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Soft delete: the task leaves every list but keeps its feed, timer time and payroll
    links; POST /{id}/restore brings it back."""
    t = await _load(db, current, task_id)
    await _check_may_change(db, current, t)
    if t.archived_at is None:
        t.archived_at = now_utc()
        await _feed(db, current, t, "Task deleted")
        if t.status == "done":
            await _unpay(db, current, t, "deleted")
        await db.commit()
    return {"ok": True}


@router.post("/{task_id}/restore")
async def restore_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    t = await _load(db, current, task_id)
    await _check_may_change(db, current, t)
    if t.archived_at is not None:
        if t.source_ref and t.status not in TASK_CLOSED:
            from backend.services.auto_tasks import open_task_for
            if await open_task_for(db, t.org_id, t.source_ref) is not None:
                raise HTTPException(409, "An open copy of this automatic task already exists.")
        t.archived_at = None
        await _feed(db, current, t, "Task restored")
        if t.status == "done":
            await _pay(db, current, t)   # deleting it took the payout back; restoring pays again
        await db.commit()
        await db.refresh(t)
    return {**_ser(t, await _names(db, current.org_id)),
            "order_number": (await _order_numbers(db, current.org_id, [t.order_id])).get(t.order_id)}


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
    await _send_pushes(db)
    return {"ok": True}


# ── helpers ──────────────────────────────────────────────────────────────────
def _involves(t: Task, emp_id: str) -> bool:
    return emp_id in (t.assigned_to_id, t.created_by_id) or emp_id in (t.assignee_ids or [])


async def _check_assign(db: AsyncSession, current: Employee, people: list) -> None:
    """Giving work to someone else needs can_assign_staff; anyone may take a task themselves."""
    if any(p and p != current.id for p in people) and not await has_perm(db, current, "can_assign_staff"):
        raise HTTPException(403, "Your role can't assign tasks to other people. Assign it to yourself.")


def _is_self_claim(t: Task, data, current: Employee) -> bool:
    """Anyone may pick up an unassigned task (the shop's pool): a PATCH that only makes the caller
    its assignee, optionally with a status, needs no other right. Everything else still goes
    through _check_may_change."""
    from backend.services.notification_rules import task_people
    if task_people(t) or t.archived_at is not None:
        return False
    sent = data.model_dump(exclude_unset=True)
    if not sent or set(sent) - {"assigned_to_id", "assignee_ids", "status"}:
        return False
    if "assigned_to_id" in sent and sent["assigned_to_id"] != current.id:
        return False
    if "assignee_ids" in sent and set(sent["assignee_ids"] or []) != {current.id}:
        return False
    return "assigned_to_id" in sent or "assignee_ids" in sent


async def _check_may_change(db: AsyncSession, current: Employee, t: Task) -> None:
    """Edit, complete or delete: the creator, an assignee, or someone who assigns work."""
    if not _involves(t, current.id) and not await has_perm(db, current, "can_assign_staff"):
        raise HTTPException(403, "Only the person who made this task, its assignees or a manager can change it.")


async def _load(db: AsyncSession, current: Employee, task_id: str) -> Task:
    """This shop's task, if the caller may see it (task_visibility); 404 otherwise, never 403,
    so a hidden task's existence isn't revealed."""
    t = (await db.execute(select(Task).where(
        Task.id == task_id, Task.org_id == current.org_id,
        *await visibility_conds(db, current)))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Task not found")
    return t


async def _feed(db: AsyncSession, current: Employee, t: Task, body: str,
                kind: str = "system") -> None:
    from backend.models import EventFeedItem
    db.add(EventFeedItem(org_id=current.org_id, entity_type="task", entity_id=t.id,
                         item_type=kind, body=body,
                         author_id=current.id, author_name=current.name))


async def _notify(db: AsyncSession, current: Employee, t: Task, event: str,
                  recipients=None) -> None:
    """Internal notifications for tasks: staff bell + push, plus any email/SMS task rules.
    Orderry's rule: you are never notified about something you did yourself. A failure is
    logged and never fails the request."""
    try:
        from backend.services import notification_rules as nr
        await nr.notify_task(db, org_id=current.org_id, task=t, event=event,
                             actor_id=current.id, recipients=recipients, deferred=_pushes(db))
    except Exception:  # noqa: BLE001
        log.warning("task notification failed (task=%s event=%s)", t.id, event, exc_info=True)


async def _unpay(db: AsyncSession, current: Employee, t: Task, reason: str) -> None:
    """Reverse every person's still-open task_closed commission for this task (reopen, cancel,
    delete). Failures are logged; the status change still succeeds."""
    try:
        from backend.services.payroll import CommissionEngine
        await CommissionEngine(db, current.org_id).reverse_for_task(t, reason)
    except Exception:  # noqa: BLE001
        log.exception("task commission reversal failed (task=%s)", t.id)


async def _pay(db: AsyncSession, current: Employee, t: Task) -> None:
    """Commission for closing a task — build 70's on_time/overdue term conditions
    already model exactly this, so a task_closed rule just reuses them."""
    try:
        from backend.services.payroll import CommissionEngine
        await CommissionEngine(db, current.org_id).accrue_for_task(t)
    except Exception:  # noqa: BLE001 — logged; completing the task still succeeds
        log.exception("task commission accrual failed (task=%s)", t.id)
