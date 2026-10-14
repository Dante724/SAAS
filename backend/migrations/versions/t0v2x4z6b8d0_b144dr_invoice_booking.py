"""b144dr — invoice loop + bookings-without-assignee + scheduled-on-track

- organisations: oe_show_scheduled, book_show_employee_step,
  auto_draft_invoice_on_close, default_invoice_display
- invoices: payer_client_id, display_mode, auto_drafted
- bookings.employee_id -> nullable (create unassigned bookings)

Revision ID: t0v2x4z6b8d0
Revises: s9u1w3y5a7c9
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa

revision = "t0v2x4z6b8d0"
down_revision = "s9u1w3y5a7c9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("oe_show_scheduled", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("book_show_employee_step", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("auto_draft_invoice_on_close", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("default_invoice_display", sa.String(length=12), nullable=False, server_default="detailed"))

    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("payer_client_id", sa.String(), sa.ForeignKey("clients.id"), nullable=True))
        b.add_column(sa.Column("display_mode", sa.String(length=12), nullable=False, server_default="detailed"))
        b.add_column(sa.Column("auto_drafted", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("bookings") as b:
        b.alter_column("employee_id", existing_type=sa.String(), nullable=True)


def downgrade():
    with op.batch_alter_table("bookings") as b:
        b.alter_column("employee_id", existing_type=sa.String(), nullable=False)
    with op.batch_alter_table("invoices") as b:
        b.drop_column("auto_drafted")
        b.drop_column("display_mode")
        b.drop_column("payer_client_id")
    with op.batch_alter_table("organisations") as b:
        b.drop_column("default_invoice_display")
        b.drop_column("auto_draft_invoice_on_close")
        b.drop_column("book_show_employee_step")
        b.drop_column("oe_show_scheduled")
