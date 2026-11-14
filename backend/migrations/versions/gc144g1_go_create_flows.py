"""Tekly Go create/finish flows: idempotency keys, and closing / payments become permissions.

- idempotency_records: one row per (org, Idempotency-Key) so a retried create or payment runs once.
- New permission codes can_close_tickets and can_take_payments. Nobody's access changes:
  * can_close_tickets is granted to every existing explicit role (closing had no permission before).
  * can_take_payments copies each role's can_view_finance (payments were gated by it before).
  * can_create_payment_links is now enforced; it copies can_view_finance for the same reason.
  * can_create_estimates is now enforced; a role missing the key copies can_create_tickets.
  Legacy (non-explicit) roles are deny-wins: a code is granted unless set to False, so the two
  money codes are written as False wherever the role already denies can_view_finance.

Revision ID: gc144g1
Revises: ls144r4
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "gc144g1"
down_revision = "ls144r4"
branch_labels = None
depends_on = None


def _create_idempotency_records() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=80), nullable=False),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("org_id", "key", name="uq_idempotency_org_key"),
    )
    op.create_index("ix_idempotency_records_org_id", "idempotency_records", ["org_id"])
    op.create_index("ix_idempotency_records_created_at", "idempotency_records", ["created_at"])


def upgrade() -> None:
    # The parity line's idem01 creates the same table (same columns); create it only once.
    if not sa.inspect(op.get_bind()).has_table("idempotency_records"):
        _create_idempotency_records()

    from backend.services.permissions import grant_go_codes
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, permissions, perms_explicit FROM roles")).fetchall()
    for rid, raw, explicit in rows:
        perms = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
        new, changed = grant_go_codes(perms, bool(explicit))
        if changed:
            stmt = sa.text("UPDATE roles SET permissions = :p WHERE id = :i").bindparams(
                sa.bindparam("p", type_=sa.JSON()))
            conn.execute(stmt, {"p": new, "i": rid})


def downgrade() -> None:
    # idem01 (the other line) creates and drops the same table: on `downgrade base` whichever of
    # the two runs second finds it already gone (final review P3-1: that used to fail mid-way).
    if not sa.inspect(op.get_bind()).has_table("idempotency_records"):
        return
    op.execute("DROP INDEX IF EXISTS ix_idempotency_records_created_at")
    op.execute("DROP INDEX IF EXISTS ix_idempotency_records_org_id")
    op.drop_table("idempotency_records")
