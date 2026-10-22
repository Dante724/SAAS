"""Unique status key per pipeline (launch QA B2).

ensure_seeded used to check-then-insert, so a new shop's parallel first-load requests each seeded
a copy of a pipeline (estimate x2, purchase_order/transfer x3 seen). Bare-key status lookups then
raised MultipleResultsFound and creating an estimate 500'd for that shop for good.

- De-duplicate order_statuses on (org_id, record_type, key): keep the active row, then the lowest
  sort_order, then the lowest id; repoint status_transitions and feedback_settings at the keeper
  and delete the rest. Orders reference statuses by key, so they need no change.
- Add the unique index uq_order_statuses_org_rt_key.

Revision ID: os144q5
Revises: vd144q4
"""
import sqlalchemy as sa
from alembic import op

revision = "os144q5"
down_revision = "vd144q4"
branch_labels = None
depends_on = None


def _dedupe(conn) -> None:
    rows = conn.execute(sa.text(
        "SELECT id, org_id, record_type, key, active, sort_order FROM order_statuses "
        "WHERE org_id IS NOT NULL")).fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r.org_id, r.record_type, r.key), []).append(r)
    for dupes in groups.values():
        if len(dupes) < 2:
            continue
        dupes.sort(key=lambda r: (0 if r.active else 1, r.sort_order or 0, r.id))
        keep, drop = dupes[0].id, [r.id for r in dupes[1:]]
        for old in drop:
            p = {"keep": keep, "old": old}
            conn.execute(sa.text("UPDATE status_transitions SET from_status_id = :keep WHERE from_status_id = :old"), p)
            conn.execute(sa.text("UPDATE status_transitions SET to_status_id = :keep WHERE to_status_id = :old"), p)
            conn.execute(sa.text("UPDATE feedback_settings SET trigger_status_id = :keep WHERE trigger_status_id = :old"), p)
            conn.execute(sa.text("DELETE FROM order_statuses WHERE id = :old"), p)
    # Repointing can leave a transition that now duplicates another (or loops to itself).
    conn.execute(sa.text("DELETE FROM status_transitions WHERE from_status_id = to_status_id"))
    conn.execute(sa.text(
        "DELETE FROM status_transitions WHERE id NOT IN (SELECT MIN(id) FROM status_transitions "
        "GROUP BY org_id, from_status_id, to_status_id, COALESCE(role_id, ''))"))


def upgrade() -> None:
    _dedupe(op.get_bind())
    op.create_index("uq_order_statuses_org_rt_key", "order_statuses", ["org_id", "record_type", "key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_order_statuses_org_rt_key", table_name="order_statuses")
