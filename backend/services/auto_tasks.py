"""Automatic tasks: created by an event, never duplicated, closed when the event is over.

Generalises the missed-call pattern (leads.py: one open "Call back" task per inquiry, closed when
the inquiry becomes a job). Every automatic task carries a `source` and a `source_ref`:

    order:<id>:status:<key>:rule:<id>   automation "status changed → create task"
    order:<id>:overdue:rule:<id>        automation "work order overdue → create task"
    lead:<id>                           missed call → call back

The partial unique index uq_tasks_open_source_ref allows one OPEN, non-archived task per
(org_id, source_ref), so `ensure_task` is safe under repeats, hourly sweeps and races: it looks for
the open task first and, if it loses an insert race, returns the winner's row.
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import TASK_CLOSED, TASK_SOURCES, Task
from backend.services.dates import now_utc

log = logging.getLogger("tekly.tasks")


def _open_with_ref(org_id: str, source_ref: str):
    return select(Task).where(Task.org_id == org_id, Task.source_ref == source_ref,
                              Task.archived_at.is_(None), Task.status.notin_(TASK_CLOSED))


async def open_task_for(db: AsyncSession, org_id: str, source_ref: str) -> Optional[Task]:
    """The open, non-archived task holding `source_ref`, if any."""
    return (await db.execute(_open_with_ref(org_id, source_ref).limit(1))).scalar_one_or_none()


async def ensure_task(db: AsyncSession, *, org_id: str, source: str, source_ref: str, title: str,
                      description: Optional[str] = None, assigned_to_id: Optional[str] = None,
                      assignee_ids: Optional[list] = None, order_id: Optional[str] = None,
                      client_id: Optional[str] = None, lead_id: Optional[str] = None,
                      location_id: Optional[str] = None, priority=0, due_at: Optional[datetime] = None,
                      created_by_id: Optional[str] = None, notify: bool = True) -> tuple[Task, bool]:
    """The open task for `source_ref`, creating it when there is none. Returns (task, created).

    Flushes but never commits (the caller owns the transaction). `notify` alerts the assignees
    through the staff bell + push when a task is created (no actor: nobody is skipped)."""
    if source not in TASK_SOURCES:
        raise ValueError(f"unknown task source {source!r}")
    if not source_ref:
        raise ValueError("an automatic task needs a source_ref")
    found = (await db.execute(_open_with_ref(org_id, source_ref).limit(1))).scalar_one_or_none()
    if found is not None:
        return found, False
    ids = list(dict.fromkeys([i for i in (assignee_ids or []) if i]))
    primary = assigned_to_id or (ids[0] if ids else None)
    if primary and primary not in ids:
        ids = [primary] + ids
    t = Task(org_id=org_id, title=title[:500], description=description, assigned_to_id=primary,
             assignee_ids=ids, order_id=order_id, client_id=client_id, lead_id=lead_id,
             location_id=location_id, priority=priority, due_at=due_at, created_by_id=created_by_id,
             status="todo", source=source, source_ref=source_ref[:200])
    try:
        async with db.begin_nested():
            db.add(t)
            await db.flush()
    except IntegrityError:
        # Someone else created it between our read and our insert: theirs is the task.
        winner = (await db.execute(_open_with_ref(org_id, source_ref).limit(1))).scalar_one_or_none()
        if winner is None:
            raise
        return winner, False
    if notify and ids:
        from backend.services.notification_rules import notify_task_bell
        try:
            await notify_task_bell(db, org_id=org_id, task=t, event="assigned", actor_id=None,
                                   recipients=ids)
        except Exception:  # noqa: BLE001 — an alert must never undo the task
            log.warning("task notification failed for automatic task %s", t.id, exc_info=True)
    return t, True


async def resolve_tasks(db: AsyncSession, *, org_id: str, source_ref: Optional[str] = None,
                        source_ref_prefix: Optional[str] = None, status: str = "done") -> int:
    """Close every open automatic task whose source_ref equals `source_ref` or starts with
    `source_ref_prefix`. `status` is "done" (the work happened) or "cancelled" (it no longer needs
    doing). Returns how many were closed. Flushes nothing itself; the caller commits."""
    if status not in TASK_CLOSED:
        raise ValueError(f"resolve status must be one of {TASK_CLOSED}")
    if not (source_ref or source_ref_prefix):
        raise ValueError("give source_ref or source_ref_prefix")
    q = select(Task).where(Task.org_id == org_id, Task.archived_at.is_(None),
                           Task.status.notin_(TASK_CLOSED))
    if source_ref:
        q = q.where(Task.source_ref == source_ref)
    else:
        esc = source_ref_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = q.where(Task.source_ref.like(f"{esc}%", escape="\\"))
    rows = (await db.execute(q)).scalars().all()
    now = now_utc()
    for t in rows:
        t.status = status
        t.completed_at = now
        if status == "done":
            t.completed_on_time = (t.due_at is None) or (now <= t.due_at)
    return len(rows)
