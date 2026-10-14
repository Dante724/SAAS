"""144fv: telephony_config + employees.extension (B7)

Revision ID: j6l8n0p2r4t6
Revises: i5k7m9o1q3s5
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "j6l8n0p2r4t6"
down_revision = "i5k7m9o1q3s5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employees", sa.Column("extension", sa.String(20), nullable=True))
    op.create_table(
        "telephony_config",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True, unique=True),
        sa.Column("provider", sa.String(20), server_default="simulated"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false()),
        sa.Column("caller_id_number", sa.String(40), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )


def downgrade():
    op.drop_table("telephony_config")
    op.drop_column("employees", "extension")
