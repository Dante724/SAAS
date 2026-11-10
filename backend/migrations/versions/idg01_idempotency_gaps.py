"""Idempotency gaps (architecture review, Medium).

1. idempotency_records.request_hash: what the key was first used for (path ids + body), so the
   same key sent with a different request is refused instead of replaying the first answer.
   idempotency_records.committed_at: set in the same transaction as the work's first commit, so a
   request whose answer was lost after its money committed keeps its key (no second posting), and
   only a request that never committed can have its key taken over once it looks dead.
2. One open charge per card reader, and per order / sale across readers, enforced by the
   database: partial unique indexes on terminal_charges(terminal_id), (order_id) and (sale_id)
   WHERE status = 'pending' (services/terminal.OPEN). The start check alone was check-then-insert,
   so two taps at once could send two card charges.

Existing data first: duplicates would make a unique index fail. A reader runs one payment at a
time, and an order or sale needs one charge, so of each group of pending charges only the newest
stays open. The older ones are marked failed with a "Superseded" note (never deleted: they are
money records) and logged. Network calls to the card provider can't safely run inside a
migration, so the provider side is handled by the app: services/terminal.reconcile_superseded
(every 10 minutes, for 7 days) cancels each one at the provider where it can and polls it, so a
capture that still happened is booked and flagged as a possible double payment.

The migration can be run again after a failed run: the columns are only added when missing, an
INVALID index left by a failed CONCURRENTLY build is dropped first, and indexes use IF NOT EXISTS.
On Postgres the indexes are built CONCURRENTLY (autocommit block) so charges keep being taken
during the deploy; if a new duplicate slips in while one builds, the data is resolved again and
the build retried once.

Revision ID: idg01
Revises: trg01
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "idg01"
down_revision = "trg01"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.idg01")

# Index name -> the column that may have one pending charge. Matches models.TerminalCharge.
INDEXES = {
    "uq_terminal_charges_open_per_reader": "terminal_id",
    "uq_terminal_charges_open_per_order": "order_id",
    "uq_terminal_charges_open_per_sale": "sale_id",
}
INDEX = "uq_terminal_charges_open_per_reader"
# Same text as services/terminal.SUPERSEDED_NOTE (the reconcile sweep finds them by it).
NOTE = ("Superseded: a newer charge was sent while this one was still open "
        "(one open charge per reader, order and sale, idg01). Check the provider before charging again.")


def _duplicates(column: str):
    return sa.text(f"""
        SELECT id, terminal_id, order_id, sale_id, org_id, amount, created_at FROM terminal_charges c
        WHERE status = 'pending' AND c.{column} IS NOT NULL AND EXISTS (
            SELECT 1 FROM terminal_charges n
            WHERE n.{column} = c.{column} AND n.status = 'pending'
              AND (n.created_at > c.created_at OR (n.created_at = c.created_at AND n.id > c.id)))
    """)


def _resolve(bind, column: str = "terminal_id") -> int:
    rows = bind.execute(_duplicates(column)).fetchall()
    for r in rows:
        log.warning("idg01: terminal charge %s (reader %s, order %s, sale %s, shop %s, amount %s, %s) was a "
                    "second open charge on its %s; marked failed", r.id, r.terminal_id, r.order_id, r.sale_id,
                    r.org_id, r.amount, r.created_at, column)
        bind.execute(sa.text("UPDATE terminal_charges SET status = 'failed', error = :note "
                             "WHERE id = :id AND status = 'pending'"), {"id": r.id, "note": NOTE})
    return len(rows)


def _resolve_all(bind) -> int:
    return sum(_resolve(bind, col) for col in INDEXES.values())


def _create_sql(name: str, column: str, concurrently: bool) -> str:
    how = "CONCURRENTLY " if concurrently else ""
    return (f"CREATE UNIQUE INDEX {how}IF NOT EXISTS {name} "
            f"ON terminal_charges ({column}) WHERE status = 'pending'")


def _add_missing_columns(bind) -> None:
    have = {c["name"] for c in sa.inspect(bind).get_columns("idempotency_records")}
    if "request_hash" not in have:
        op.add_column("idempotency_records", sa.Column("request_hash", sa.String(64), nullable=True))
    if "committed_at" not in have:
        op.add_column("idempotency_records", sa.Column("committed_at", sa.DateTime(), nullable=True))


def upgrade() -> None:
    bind = op.get_bind()
    _add_missing_columns(bind)
    n = 0
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for name, column in INDEXES.items():
                # A run that died mid-build leaves an INVALID index that IF NOT EXISTS would keep.
                if bind.execute(sa.text("SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                                        "WHERE c.relname = :n AND NOT i.indisvalid"), {"n": name}).first():
                    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
                n += _resolve(bind, column)
                try:
                    op.execute(_create_sql(name, column, True))
                except sa.exc.DBAPIError:
                    # A duplicate arrived while the index was building: Postgres leaves it INVALID.
                    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
                    n += _resolve(bind, column)
                    op.execute(_create_sql(name, column, True))
    else:
        n = _resolve_all(bind)
        for name, column in INDEXES.items():
            op.execute(_create_sql(name, column, False))
    if n:
        log.warning("idg01: %d duplicate open terminal charge(s) marked failed (none deleted)", n)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for name in INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    else:
        for name in INDEXES:
            op.execute(f"DROP INDEX IF EXISTS {name}")
    with op.batch_alter_table("idempotency_records") as b:
        b.drop_column("committed_at")
        b.drop_column("request_hash")
