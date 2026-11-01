"""Launch blocker C2 — one status row per key in each pipeline.

A new shop's first page load fires several requests that each ran ensure_seeded's
check-then-insert, so the default statuses were inserted two to ten times over. The copies
then made bare status lookups raise MultipleResultsFound, and every later estimate was a 500.

- Backfill a missing record_type from is_estimate.
- Collapse each (org_id, record_type, key) group to one row — the active one, lowest
  sort_order — repointing status_transitions and feedback_settings at the row kept.
- Add UNIQUE(org_id, record_type, key) and an org_id index; seeding is now insert-or-ignore.

Revision ID: ls144r1
Revises: os144q5

Merged after os144q5 (security/phase-4's version of the same fix), which already de-duplicated
and added uq_order_statuses_org_rt_key as a unique index. This revision swaps that index for the
unique constraint the model declares; on a database without os144q5 it runs the full fix.
"""
import sqlalchemy as sa
from alembic import op

revision = "ls144r1"
down_revision = "os144q5"
branch_labels = None
depends_on = None

# Columns that point at order_statuses.id.
_REFS = (("status_transitions", "from_status_id"), ("status_transitions", "to_status_id"),
         ("feedback_settings", "trigger_status_id"))


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE order_statuses SET record_type = CASE WHEN is_estimate THEN 'estimate' ELSE 'work_order' END "
        "WHERE record_type IS NULL"))
    rows = conn.execute(sa.text(
        "SELECT id, org_id, record_type, key FROM order_statuses "
        "ORDER BY org_id, record_type, key, active DESC, sort_order, id")).fetchall()
    keep, dupes = {}, []
    for sid, org_id, rt, key in rows:
        group = (org_id, rt, key)
        if group in keep:
            dupes.append((sid, keep[group]))
        else:
            keep[group] = sid
    for dup, kept in dupes:
        for table, col in _REFS:
            conn.execute(sa.text(f"UPDATE {table} SET {col} = :kept WHERE {col} = :dup"),
                         {"kept": kept, "dup": dup})
        conn.execute(sa.text("DELETE FROM order_statuses WHERE id = :dup"), {"dup": dup})

    if "uq_order_statuses_org_rt_key" in {i["name"] for i in sa.inspect(conn).get_indexes("order_statuses")}:
        op.drop_index("uq_order_statuses_org_rt_key", table_name="order_statuses")
    with op.batch_alter_table("order_statuses") as b:
        b.create_unique_constraint("uq_order_statuses_org_rt_key", ["org_id", "record_type", "key"])
    op.create_index("ix_order_statuses_org_id", "order_statuses", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_order_statuses_org_id", table_name="order_statuses")
    with op.batch_alter_table("order_statuses") as b:
        b.drop_constraint("uq_order_statuses_org_rt_key", type_="unique")
