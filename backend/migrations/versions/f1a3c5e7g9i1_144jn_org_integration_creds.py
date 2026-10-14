"""144jn — per-org integration credentials (multi-tenant provider keys)

Each shop stores its OWN Stripe/Square/SumUp/Twilio/SMTP/Meta keys here,
encrypted at rest, instead of sharing one global env set.

Revision ID: f1a3c5e7g9i1
Revises: e9g1i3k5m7o9
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "f1a3c5e7g9i1"
down_revision = "e9g1i3k5m7o9"
branch_labels = None
depends_on = None


def _has_table(bind, table):
    insp = sa.inspect(bind)
    try:
        return table in insp.get_table_names()
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    if _has_table(bind, "org_integration_credentials"):
        return
    op.create_table(
        "org_integration_credentials",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True, nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("data_enc", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("org_id", "provider", name="uq_org_integration_cred"),
    )


def downgrade():
    bind = op.get_bind()
    if _has_table(bind, "org_integration_credentials"):
        op.drop_table("org_integration_credentials")
