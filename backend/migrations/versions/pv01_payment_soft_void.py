"""Payments are voided, never deleted (audit DB-11).

payments.refund_id     the Refund a kind='refund' row was written for, so voiding that refund
                       finds exactly its own rows (they were matched on order, cashbox and paid_at,
                       which also caught another refund written in the same second). No FK:
                       refunds.payment_id already points at payments, and SQLite can't create the
                       cycle. Indexed (ix_payments_refund_id).
payments.voided_at     when the row was voided; NULL = live. Every ORM read leaves voided rows out
                       (backend/db/soft_void.py).
payments.voided_by_id  who voided it (employees.id).

All three are nullable with no default, so on Postgres the ADD COLUMNs are catalogue-only; the
FK is added NOT VALID and validated separately (no scan under the ALTER's lock); the index is
built CONCURRENTLY (IF NOT EXISTS, so a half-finished run is safe to retry). Existing
rows keep refund_id NULL: a refund voided later falls back to its own payment_id (see
services/invoice_money.refund_rows). Downgrade drops the three columns (rows voided meanwhile
would then count as live again; delete them first if that matters).

Revision ID: pv01
Revises: corg01
"""
import sqlalchemy as sa
from alembic import op

revision = "pv01"
down_revision = "corg01"
branch_labels = None
depends_on = None

FK = "fk_payments_voided_by_id_employees"
IX = "ix_payments_refund_id"


def upgrade() -> None:
    pg = op.get_bind().dialect.name == "postgresql"
    with op.batch_alter_table("payments") as b:
        b.add_column(sa.Column("refund_id", sa.String(), nullable=True))
        b.add_column(sa.Column("voided_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("voided_by_id", sa.String(), nullable=True))
        if not pg:
            b.create_foreign_key(FK, "employees", ["voided_by_id"], ["id"])
    if pg:
        # NOT VALID: adding the FK doesn't scan payments under the ALTER's lock. VALIDATE then
        # checks existing rows (all NULL here) in its own transaction, taking only SHARE UPDATE
        # EXCLUSIVE, so payment writes go on while it runs.
        op.execute(f"ALTER TABLE payments ADD CONSTRAINT {FK} FOREIGN KEY (voided_by_id) "
                   f"REFERENCES employees (id) NOT VALID")
        with op.get_context().autocommit_block():
            op.execute(f"ALTER TABLE payments VALIDATE CONSTRAINT {FK}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {IX} ON payments (refund_id)")
    else:
        op.execute(f"CREATE INDEX IF NOT EXISTS {IX} ON payments (refund_id)")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {IX}")
    else:
        op.execute(f"DROP INDEX IF EXISTS {IX}")
    with op.batch_alter_table("payments") as b:
        b.drop_constraint(FK, type_="foreignkey")
        b.drop_column("voided_by_id")
        b.drop_column("voided_at")
        b.drop_column("refund_id")
