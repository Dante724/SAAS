"""A job's lines come back in a fixed order: order_lines.created_at (re-review of #231).

order_lines had no column to order by, and the job's lines were read with no ORDER BY, so their
order was whatever the database returned: insertion order by luck on SQLite, heap order on Postgres
(which changes when a line is updated). dbi01's (org_id, id) unique index made SQLite's planner read
the lines in id (random UUID) order, which made it visible: the job screen, documents and "bill
the first line" could pick lines in a different order from one read to the next.

1. Add order_lines.created_at (nullable) and give it a server default, CURRENT_TIMESTAMP, so lines
   the previous image inserts during the deploy (it doesn't know the column) are stamped too. On
   Postgres both are catalog-only changes (instant).
2. Backfill existing lines in batches of BATCH jobs, keeping the order they show in today: the job's
   created_at plus one microsecond per line, in physical order (ctid on Postgres, rowid on SQLite).
   Each batch commits on its own on Postgres (autocommit). Idempotent: only NULL rows are touched.

The model orders Order.lines by (created_at, id). Downgrade drops the column.

Revision ID: dbi03
Revises: dbi02
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "dbi03"
down_revision = "dbi02"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.dbi03")

BATCH = 1000          # jobs per backfill statement


def _has_column(bind) -> bool:
    return "created_at" in {c["name"] for c in sa.inspect(bind).get_columns("order_lines")}


def _backfill(bind, pg: bool) -> int:
    phys = "ctid" if pg else "rowid"
    offset = ("o.created_at + x.rn * interval '1 microsecond'" if pg
              else "datetime(o.created_at, '+' || x.rn || ' seconds')")   # dev only: 1 s steps
    done, last = 0, ""
    while True:
        ids = [r[0] for r in bind.execute(sa.text(
            "SELECT DISTINCT order_id FROM order_lines WHERE created_at IS NULL AND order_id > :lo "
            f"ORDER BY order_id LIMIT {BATCH}"), {"lo": last}).fetchall()]
        if not ids:
            return done
        lo, hi = ids[0], ids[-1]
        done += bind.execute(sa.text(
            f"UPDATE order_lines SET created_at = (SELECT {offset} FROM orders o, "
            f"(SELECT id, row_number() OVER (PARTITION BY order_id ORDER BY {phys}) AS rn FROM order_lines "
            f" WHERE order_id >= :lo AND order_id <= :hi) x "
            f"WHERE o.id = order_lines.order_id AND x.id = order_lines.id) "
            "WHERE created_at IS NULL AND order_id >= :lo AND order_id <= :hi"), {"lo": lo, "hi": hi}).rowcount or 0
        last = hi


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    if pg:
        with op.get_context().autocommit_block():
            if not _has_column(bind):
                op.execute("ALTER TABLE order_lines ADD COLUMN created_at TIMESTAMP WITHOUT TIME ZONE")
            op.execute("ALTER TABLE order_lines ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP")
            n = _backfill(bind, pg=True)
    else:
        if not _has_column(bind):
            # SQLite: ADD COLUMN with a non-constant default isn't allowed; the app always sets it,
            # and a dev database never runs the previous image against it
            op.add_column("order_lines", sa.Column("created_at", sa.DateTime(), nullable=True))
        n = _backfill(bind, pg=False)
    if n:
        log.info("dbi03: %s job line(s) got a created_at in their current order", n)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        if bind.dialect.name == "postgresql":
            op.execute("ALTER TABLE order_lines DROP COLUMN created_at")
        else:
            with op.batch_alter_table("order_lines") as b:
                b.drop_column("created_at")
