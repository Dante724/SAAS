"""Tasks: overdue alerts, shared commission, My day indexes, due times to UTC.

tasks.overdue_alerted_at            when the sweep last finished alerting this task's assignees for its
                                    current due time (null = still to do). Moving due_at later into the
                                    future, or adding an assignee, clears it.
task_overdue_alerts                 one row per (task, employee) already told about the current overdue
                                    spell; the unique pair is the once-per-person guard.
organisations.task_commission_mode  split | each_full | first_only; null reads as split.
ix_tasks_org_live_due               (org_id, archived_at, status, due_at): list, My day buckets, counts.
ix_tasks_org_completed              (org_id, completed_at): My day's "done today".
ix_tasks_org_assignee / _creator    (org_id, assigned_to_id) / (org_id, created_by_id): "mine" arms.
ix_tasks_assignee_ids_gin           Postgres only: GIN on (assignee_ids::jsonb) jsonb_path_ops, for
                                    "assignee_ids contains me".
ix_tasks_overdue_pending            (due_at) partial: open, not deleted, not yet alerted.

Data, in this order:
1. Due times typed by people become UTC. Until now the web Tasks screen and Tekly Go sent the
   shop's wall time with no offset (a datetime-local field) and it was stored as if it were UTC,
   so in an IST shop a task due 18:00 was stored as 18:00 and read as 23:30. Every `source =
   'manual'` task (the only kind people type; automations, inquiries and missed calls already
   wrote UTC) with a due time is converted from its shop's wall time (Organisation.timezone, that
   day's own offset) to UTC. A due time at exactly 00:00:00 came from a bare date, which the old
   code stored as the start of that day: it becomes the end of that shop day (23:59:59 local),
   what a date-only due means from now on. 23:59:59 converts like any other wall time.
2. Backlog: open tasks already overdue are stamped as alerted, and each current assignee gets a
   task_overdue_alerts row, so deploy day sends nothing for old work and adding someone later
   alerts only them.

Indexes are built inside the migration transaction, not CONCURRENTLY: tasks is a small table
(thousands of rows per shop) and every build takes well under a second.

Downgrade drops what was added and converts the manual due times back to wall time (end-of-day
values stay 23:59:59 wall time).

Revision ID: tk02
Revises: nob01
"""
import json
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision = "tk02"
down_revision = "nob01"
branch_labels = None
depends_on = None

PENDING_WHERE = ("archived_at IS NULL AND overdue_alerted_at IS NULL AND due_at IS NOT NULL "
                 "AND status NOT IN ('done', 'cancelled')")
END_OF_DAY = time(23, 59, 59)


def _zone(name):
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 — an unknown zone reads as UTC, as dates.zone() does
        return timezone.utc


def _wall_to_utc(dt: datetime, z) -> datetime:
    return dt.replace(tzinfo=z).astimezone(timezone.utc).replace(tzinfo=None)


def _utc_to_wall(dt: datetime, z) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(z).replace(tzinfo=None)


def _manual_dues(bind):
    q = sa.text("SELECT t.id, t.due_at, o.timezone FROM tasks t JOIN organisations o ON o.id = t.org_id "
                "WHERE t.source = 'manual' AND t.due_at IS NOT NULL").columns(
        sa.column("id", sa.String()), sa.column("due_at", sa.DateTime()), sa.column("timezone", sa.String()))
    return bind.execute(q).all()


def _set_due(bind, rows) -> None:
    upd = sa.text("UPDATE tasks SET due_at = :d WHERE id = :i").bindparams(
        sa.bindparam("d", type_=sa.DateTime()), sa.bindparam("i", type_=sa.String()))
    for tid, due in rows:
        bind.execute(upd, {"d": due, "i": tid})


def _due_to_utc(bind) -> None:
    out = []
    for tid, due, tzname in _manual_dues(bind):
        z = _zone(tzname)
        if due.time() == time.min:              # a bare date, stored as the start of that day
            due = datetime.combine(due.date(), END_OF_DAY)
        out.append((tid, _wall_to_utc(due, z)))
    _set_due(bind, out)


def _due_to_wall(bind) -> None:
    _set_due(bind, [(tid, _utc_to_wall(due, _zone(tzname))) for tid, due, tzname in _manual_dues(bind)])


def _record_backlog_assignees(bind, stamped_at: datetime) -> None:
    """Every current assignee of a task stamped by this migration counts as already told."""
    q = sa.text("SELECT id, org_id, assigned_to_id, assignee_ids FROM tasks "
                "WHERE overdue_alerted_at = :s").bindparams(sa.bindparam("s", type_=sa.DateTime())).columns(
        sa.column("id", sa.String()), sa.column("org_id", sa.String()),
        sa.column("assigned_to_id", sa.String()), sa.column("assignee_ids", sa.JSON()))
    tasks = bind.execute(q, {"s": stamped_at}).all()
    if not tasks:
        return
    staff = {(r[0], r[1]) for r in bind.execute(sa.text("SELECT id, org_id FROM employees")).all()}
    ins = sa.text("INSERT INTO task_overdue_alerts (id, org_id, task_id, employee_id, created_at) "
                  "VALUES (:id, :o, :t, :e, :c)").bindparams(sa.bindparam("c", type_=sa.DateTime()))
    for tid, org_id, primary, extra in tasks:
        if isinstance(extra, str):
            extra = json.loads(extra or "null")
        people = {p for p in [primary, *(extra or [])] if p and (p, org_id) in staff}
        for emp in sorted(people):
            bind.execute(ins, {"id": str(uuid.uuid4()), "o": org_id, "t": tid, "e": emp, "c": stamped_at})


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("overdue_alerted_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("task_commission_mode", sa.String(12), nullable=True))

    op.create_table(
        "task_overdue_alerts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("employee_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "employee_id", name="uq_task_overdue_alerts_task_employee"),
    )
    op.create_index("ix_task_overdue_alerts_org_id", "task_overdue_alerts", ["org_id"])

    _due_to_utc(bind)

    # Timestamps are naive UTC (docs/ADR-tz-timestamps.md). One fixed instant marks this
    # migration's stamps, so the assignees of exactly those tasks can be recorded.
    stamped_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0) - timedelta(seconds=1)
    bind.execute(sa.text(
        "UPDATE tasks SET overdue_alerted_at = :s WHERE due_at IS NOT NULL AND due_at < :s "
        "AND archived_at IS NULL AND status NOT IN ('done', 'cancelled')").bindparams(
        sa.bindparam("s", type_=sa.DateTime())), {"s": stamped_at})
    _record_backlog_assignees(bind, stamped_at)

    op.create_index("ix_tasks_org_live_due", "tasks", ["org_id", "archived_at", "status", "due_at"])
    op.create_index("ix_tasks_org_completed", "tasks", ["org_id", "completed_at"])
    op.create_index("ix_tasks_org_assignee", "tasks", ["org_id", "assigned_to_id"])
    op.create_index("ix_tasks_org_creator", "tasks", ["org_id", "created_by_id"])
    op.create_index("ix_tasks_overdue_pending", "tasks", ["due_at"],
                    postgresql_where=sa.text(PENDING_WHERE), sqlite_where=sa.text(PENDING_WHERE))
    if pg:
        op.execute("CREATE INDEX ix_tasks_assignee_ids_gin ON tasks "
                   "USING gin ((assignee_ids::jsonb) jsonb_path_ops)")


def downgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    if pg:
        op.execute("DROP INDEX IF EXISTS ix_tasks_assignee_ids_gin")
    op.drop_index("ix_tasks_overdue_pending", table_name="tasks")
    op.drop_index("ix_tasks_org_creator", table_name="tasks")
    op.drop_index("ix_tasks_org_assignee", table_name="tasks")
    op.drop_index("ix_tasks_org_completed", table_name="tasks")
    op.drop_index("ix_tasks_org_live_due", table_name="tasks")
    _due_to_wall(bind)
    op.drop_index("ix_task_overdue_alerts_org_id", table_name="task_overdue_alerts")
    op.drop_table("task_overdue_alerts")
    with op.batch_alter_table("organisations") as b:
        b.drop_column("task_commission_mode")
    with op.batch_alter_table("tasks") as b:
        b.drop_column("overdue_alerted_at")
