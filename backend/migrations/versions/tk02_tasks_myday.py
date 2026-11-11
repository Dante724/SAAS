"""Tasks: overdue alerts, shared commission, My day indexes.

tasks.overdue_alerted_at            when the sweep last finished alerting this task's assignees for its
                                    current due time (null = still to do). Moving due_at later, or
                                    adding an assignee, clears it.
task_overdue_alerts                 one row per (task, employee) already told about the current overdue
                                    spell; the unique pair is the once-per-person guard.
organisations.task_commission_mode  split | each_full | first_only; null reads as split.
ix_tasks_org_live_due               (org_id, archived_at, status, due_at): list, My day buckets, counts.
ix_tasks_org_completed              (org_id, completed_at): My day's "done today".
ix_tasks_org_assignee / _creator    (org_id, assigned_to_id) / (org_id, created_by_id): "mine" arms.
ix_tasks_assignee_ids_gin           Postgres only: GIN on (assignee_ids::jsonb) jsonb_path_ops, for
                                    "assignee_ids contains me".
ix_tasks_overdue_pending            (due_at) partial: open, not deleted, not yet alerted.

Backlog: tasks already overdue when this runs are stamped as alerted, so deploy day doesn't send
alerts for old work. Only tasks that become overdue afterwards alert.

Indexes are built inside the migration transaction, not CONCURRENTLY: tasks is a small table
(thousands of rows per shop) and every build takes well under a second.

Additive only; downgrade drops exactly these.

Revision ID: tk02
Revises: tk01
"""
import sqlalchemy as sa
from alembic import op

revision = "tk02"
down_revision = "tk01"
branch_labels = None
depends_on = None

PENDING_WHERE = ("archived_at IS NULL AND overdue_alerted_at IS NULL AND due_at IS NOT NULL "
                 "AND status NOT IN ('done', 'cancelled')")


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("overdue_alerted_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("task_commission_mode", sa.String(12), nullable=True))

    # Timestamps are naive UTC (docs/ADR-tz-timestamps.md): compare with UTC "now" on both engines.
    now = "(now() AT TIME ZONE 'utc')" if pg else "CURRENT_TIMESTAMP"
    op.execute(f"UPDATE tasks SET overdue_alerted_at = {now} "
               f"WHERE due_at IS NOT NULL AND due_at < {now} AND archived_at IS NULL "
               "AND status NOT IN ('done', 'cancelled')")

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
    pg = op.get_bind().dialect.name == "postgresql"
    if pg:
        op.execute("DROP INDEX IF EXISTS ix_tasks_assignee_ids_gin")
    op.drop_index("ix_tasks_overdue_pending", table_name="tasks")
    op.drop_index("ix_tasks_org_creator", table_name="tasks")
    op.drop_index("ix_tasks_org_assignee", table_name="tasks")
    op.drop_index("ix_tasks_org_completed", table_name="tasks")
    op.drop_index("ix_tasks_org_live_due", table_name="tasks")
    op.drop_index("ix_task_overdue_alerts_org_id", table_name="task_overdue_alerts")
    op.drop_table("task_overdue_alerts")
    with op.batch_alter_table("organisations") as b:
        b.drop_column("task_commission_mode")
    with op.batch_alter_table("tasks") as b:
        b.drop_column("overdue_alerted_at")
