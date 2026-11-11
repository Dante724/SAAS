"""idempotency_records.claim_token: who holds an Idempotency-Key right now.

A retry that takes over a dead request's key (services/idempotency) gets a new token, and the
original's commits only go through while the token is still its own. Before, the compare used
created_at, which the app wrote with its own clock; created_at is now written by the database, so
the 2-minute staleness is measured on one clock. Rows written before this have no token; the
takeover handles them (token IS NULL).

Revision ID: idg02
Revises: idg01
"""
import sqlalchemy as sa
from alembic import op

revision = "idg02"
down_revision = "idg01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("idempotency_records")}
    if "claim_token" not in have:
        op.add_column("idempotency_records", sa.Column("claim_token", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("idempotency_records") as b:
        b.drop_column("claim_token")
