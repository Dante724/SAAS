"""Idempotency gaps (architecture review, Medium).

1. idempotency_records.request_hash: what the key was first used for (path ids + body), so the
   same key sent with a different request is refused instead of replaying the first answer.
2. One open charge per card reader, enforced by the database: a partial unique index on
   terminal_charges(terminal_id) WHERE status = 'pending' (services/terminal.OPEN). The start
   check alone was check-then-insert, so two taps at once could send two card charges.

Existing data first: a reader that already holds more than one pending charge would make the
unique index fail. A reader runs one payment at a time, so every pending charge on it except the
newest was already replaced on the reader. Those older ones are marked failed with a note (never
deleted: they are money records; if the provider still reports one captured, the webhook / poll
books it and flags a possible double payment, as for any failed charge). Each one is logged.

On Postgres the index is built CONCURRENTLY (autocommit block) so charges keep being taken during
the deploy. If a new duplicate slips in while it builds, the invalid index is dropped, the data is
resolved again and the build retried once.

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

INDEX = "uq_terminal_charges_open_per_reader"
NOTE = ("Superseded: a newer charge was sent to this reader while this one was still open "
        "(one open charge per reader, idg01). Check the provider before charging again.")

_DUPLICATES = sa.text("""
    SELECT id, terminal_id, org_id, amount, created_at FROM terminal_charges c
    WHERE status = 'pending' AND EXISTS (
        SELECT 1 FROM terminal_charges n
        WHERE n.terminal_id = c.terminal_id AND n.status = 'pending'
          AND (n.created_at > c.created_at OR (n.created_at = c.created_at AND n.id > c.id)))
""")


def _resolve(bind) -> int:
    rows = bind.execute(_DUPLICATES).fetchall()
    for r in rows:
        log.warning("idg01: terminal charge %s (reader %s, shop %s, amount %s, %s) was a second open "
                    "charge on its reader; marked failed", r.id, r.terminal_id, r.org_id, r.amount, r.created_at)
        bind.execute(sa.text("UPDATE terminal_charges SET status = 'failed', error = :note "
                             "WHERE id = :id AND status = 'pending'"), {"id": r.id, "note": NOTE})
    return len(rows)


def upgrade() -> None:
    op.add_column("idempotency_records", sa.Column("request_hash", sa.String(64), nullable=True))
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        create = (f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} "
                  "ON terminal_charges (terminal_id) WHERE status = 'pending'")
        with op.get_context().autocommit_block():
            # A run that died mid-build leaves an INVALID index that IF NOT EXISTS would keep.
            if bind.execute(sa.text("SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                                    "WHERE c.relname = :n AND NOT i.indisvalid"), {"n": INDEX}).first():
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
            n = _resolve(bind)
            try:
                op.execute(create)
            except sa.exc.DBAPIError:
                # A duplicate arrived while the index was building: Postgres leaves it INVALID.
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
                n += _resolve(bind)
                op.execute(create)
    else:
        n = _resolve(bind)
        op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} "
                   "ON terminal_charges (terminal_id) WHERE status = 'pending'")
    if n:
        log.warning("idg01: %d duplicate open terminal charge(s) marked failed (none deleted)", n)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
    else:
        op.execute(f"DROP INDEX IF EXISTS {INDEX}")
    with op.batch_alter_table("idempotency_records") as b:
        b.drop_column("request_hash")
