"""Tasks phase 0 foundations.

tasks
  * status: every value outside todo|in_progress|done|cancelled becomes "todo" (automations wrote
    "open", which no screen understood).
  * priority: text -> INTEGER. "low"/"normal"/""/NULL -> 0, "high" -> 1, "urgent" -> 2,
    "critical" -> 3, digit strings keep their number (clamped to 0-3), anything else -> 0.
  * new: updated_at, archived_at (soft delete), source (manual|automation|inquiry|missed_call|
    go_part_request, default manual), source_ref.
  * backfill: tasks linked to an inquiry get source "inquiry"; one open task per inquiry (any duplicates
    stay without a ref) gets source_ref "lead:<id>" so the missed-call flow keeps finding it.
  * partial unique index uq_tasks_open_source_ref on (org_id, source_ref) while the task is open
    and not archived: an automatic task can never be created twice.
notification_logs.expect_status   the job status a delayed message was queued for.
automation_rules.activated_at     when the rule last went live (backfilled from created_at).
automation_fires                  one row per (time-based rule, subject episode) already fired.

Downgrade restores the previous shape. priority goes back to text ("0", "1", ...); the status
data fix ("open" -> "todo") and the source backfill are not undone (the old code reads "todo" fine).

Revision ID: tk01
Revises: nn01
"""
import sqlalchemy as sa
from alembic import op

revision = "tk01"
down_revision = "nn01"
branch_labels = None
depends_on = None

OPEN_WHERE = "source_ref IS NOT NULL AND archived_at IS NULL AND status NOT IN ('done', 'cancelled')"

PRIORITY_TO_DIGITS = """
UPDATE tasks SET priority = CASE
    WHEN priority IS NULL THEN '0'
    WHEN lower(trim(priority)) IN ('', 'low', 'normal') THEN '0'
    WHEN lower(trim(priority)) = 'high' THEN '1'
    WHEN lower(trim(priority)) = 'urgent' THEN '2'
    WHEN lower(trim(priority)) = 'critical' THEN '3'
    WHEN trim(priority) IN ('0', '1', '2', '3') THEN trim(priority)
    WHEN trim(priority) IN ('4', '5', '6', '7', '8', '9') THEN '3'
    ELSE '0' END
"""


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    # ── data fixes first, while priority is still text ──
    op.execute("UPDATE tasks SET status = 'todo' WHERE status IS NULL "
               "OR status NOT IN ('todo', 'in_progress', 'done', 'cancelled')")
    op.execute(PRIORITY_TO_DIGITS)

    with op.batch_alter_table("tasks") as b:
        # type first, default second: Postgres refuses a text default it would have to cast
        b.alter_column("priority", existing_type=sa.String(20), type_=sa.Integer(),
                       existing_nullable=False,
                       **({"postgresql_using": "priority::integer"} if pg else {}))
        b.alter_column("priority", existing_type=sa.Integer(), existing_nullable=False,
                       server_default="0")
        b.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("source", sa.String(30), nullable=False, server_default="manual"))
        b.add_column(sa.Column("source_ref", sa.String(200), nullable=True))

    op.execute("UPDATE tasks SET updated_at = created_at WHERE updated_at IS NULL")
    op.execute("UPDATE tasks SET source = 'inquiry' WHERE lead_id IS NOT NULL")
    op.execute(
        "UPDATE tasks SET source_ref = 'lead:' || lead_id WHERE id IN ("
        " SELECT max(id) FROM tasks WHERE lead_id IS NOT NULL"
        " AND status NOT IN ('done', 'cancelled') GROUP BY org_id, lead_id)")

    op.create_index("uq_tasks_open_source_ref", "tasks", ["org_id", "source_ref"], unique=True,
                    postgresql_where=sa.text(OPEN_WHERE), sqlite_where=sa.text(OPEN_WHERE))

    with op.batch_alter_table("notification_logs") as b:
        b.add_column(sa.Column("expect_status", sa.String(60), nullable=True))

    with op.batch_alter_table("automation_rules") as b:
        b.add_column(sa.Column("activated_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE automation_rules SET activated_at = created_at WHERE activated_at IS NULL")

    op.create_table(
        "automation_fires",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"]),
        sa.ForeignKeyConstraint(["rule_id"], ["automation_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "subject_ref", name="uq_automation_fires_rule_subject"),
    )
    op.create_index("ix_automation_fires_org_id", "automation_fires", ["org_id"])


def downgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    op.drop_index("ix_automation_fires_org_id", table_name="automation_fires")
    op.drop_table("automation_fires")
    with op.batch_alter_table("automation_rules") as b:
        b.drop_column("activated_at")
    with op.batch_alter_table("notification_logs") as b:
        b.drop_column("expect_status")
    op.drop_index("uq_tasks_open_source_ref", table_name="tasks")
    with op.batch_alter_table("tasks") as b:
        b.drop_column("source_ref")
        b.drop_column("source")
        b.drop_column("archived_at")
        b.drop_column("updated_at")
        b.alter_column("priority", existing_type=sa.Integer(), existing_nullable=False,
                       server_default=None)
        b.alter_column("priority", existing_type=sa.Integer(), type_=sa.String(20),
                       existing_nullable=False,
                       **({"postgresql_using": "priority::text"} if pg else {}))
