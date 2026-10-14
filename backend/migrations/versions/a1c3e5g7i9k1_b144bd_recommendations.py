"""b144bd — recommendations (upsell checklist client approves)

Revision ID: a1c3e5b7d9f1
Revises: f0b2d4e6a8c1
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c3e5b7d9f1"
down_revision = "f0b2d4e6a8c1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recommendations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), index=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("kind", sa.String(length=20), server_default="custom"),
        sa.Column("ref_id", sa.String()),
        sa.Column("qty", sa.Float(), server_default="1"),
        sa.Column("unit_price", sa.Float(), server_default="0"),
        sa.Column("media_data", sa.Text()),
        sa.Column("status", sa.String(length=20), server_default="draft", index=True),
        sa.Column("order_line_id", sa.String()),
        sa.Column("author_id", sa.String(), sa.ForeignKey("employees.id")),
        sa.Column("author_name", sa.String(length=200)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("published_at", sa.DateTime()),
        sa.Column("decided_at", sa.DateTime()),
    )
    with op.batch_alter_table("organisations") as batch_op:
        batch_op.add_column(sa.Column("oe_allow_recommend", sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("rec_unread", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("rec_unread")
    with op.batch_alter_table("organisations") as batch_op:
        batch_op.drop_column("oe_allow_recommend")
    op.drop_table("recommendations")
