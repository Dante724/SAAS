"""144fs: tags + taggables (unified labels across entities · B6)

Revision ID: i5k7m9o1q3s5
Revises: h4j6l8n0p2r4
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "i5k7m9o1q3s5"
down_revision = "h4j6l8n0p2r4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("color", sa.String(20), server_default="#64748b"),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "taggables",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("tag_id", sa.String(), sa.ForeignKey("tags.id"), index=True),
        sa.Column("entity_type", sa.String(20), index=True),
        sa.Column("entity_id", sa.String(), index=True),
        sa.UniqueConstraint("tag_id", "entity_type", "entity_id", name="uq_taggable"),
    )


def downgrade():
    op.drop_table("taggables")
    op.drop_table("tags")
