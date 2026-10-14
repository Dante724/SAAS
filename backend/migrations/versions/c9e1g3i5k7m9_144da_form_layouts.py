"""144da: form_layouts table (Forms Editor system-field layout per entity/type)

Revision ID: c9e1g3i5k7m9
Revises: b8d0f2h4j6l8
Create Date: 2026-09-05
"""
from alembic import op
import sqlalchemy as sa

revision = "c9e1g3i5k7m9"
down_revision = "b8d0f2h4j6l8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "form_layouts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("entity", sa.String(length=32)),
        sa.Column("type_id", sa.String(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.UniqueConstraint("org_id", "entity", "type_id", name="uq_form_layout"),
    )


def downgrade():
    op.drop_table("form_layouts")
