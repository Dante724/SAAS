"""144gj — status requirement gates (require comment / payment / checklist)

Revision ID: g5i7k9m1o3q5
Revises: j6l8n0p2r4t6
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "g5i7k9m1o3q5"
down_revision = "j6l8n0p2r4t6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("order_statuses") as b:
        b.add_column(sa.Column("require_comment", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("require_payment", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("require_checklist", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("order_statuses") as b:
        b.drop_column("require_checklist")
        b.drop_column("require_payment")
        b.drop_column("require_comment")
