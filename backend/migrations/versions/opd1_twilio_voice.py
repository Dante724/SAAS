"""D1 — Twilio voice: calls.provider_sid (the provider's call id, e.g. Twilio CallSid),
unique per org so a retried webhook can never log the same call twice.

`calls.direction` already exists (A7), so only provider_sid is added.

Revision ID: opd1
Revises: opa1
"""
import sqlalchemy as sa
from alembic import op

revision = "opd1"
down_revision = "opa1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calls") as b:
        b.add_column(sa.Column("provider_sid", sa.String(length=64), nullable=True))
    op.create_index("uq_calls_org_provider_sid", "calls", ["org_id", "provider_sid"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_calls_org_provider_sid", table_name="calls")
    with op.batch_alter_table("calls") as b:
        b.drop_column("provider_sid")
