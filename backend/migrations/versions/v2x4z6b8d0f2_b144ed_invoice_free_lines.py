"""Build 144ed — free-form invoice lines + invoice comment.

Adds kind/source_id/sort_order to invoice_lines so an invoice can carry
hand-added catalog/manual lines (Orderry "Add labors, services, products"),
and a comment column on invoices.

Revision ID: v2x4z6b8d0f2
Revises: u1w3y5a7c9e1
"""
from alembic import op
import sqlalchemy as sa

revision = "v2x4z6b8d0f2"
down_revision = "u1w3y5a7c9e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoice_lines") as b:
        b.add_column(sa.Column("kind", sa.String(length=16), server_default="custom", nullable=False))
        b.add_column(sa.Column("source_id", sa.String(), nullable=True))
        b.add_column(sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False))
    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("comment", sa.String(length=1000), server_default="", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("invoices") as b:
        b.drop_column("comment")
    with op.batch_alter_table("invoice_lines") as b:
        b.drop_column("sort_order")
        b.drop_column("source_id")
        b.drop_column("kind")
