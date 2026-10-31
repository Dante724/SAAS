"""Tekly Go messaging: WhatsApp consent, and a record of texts/WhatsApps sent from a phone.

- clients.consent_whatsapp: nullable (None = not asked). Separate from consent_phone because
  agreeing to texts isn't agreeing to WhatsApp.
- message_handoffs: what a staff member opened in their phone's SMS/WhatsApp app from Tekly Go,
  and whether they said it was sent.

Revision ID: gc144g2
Revises: gc144g1
"""
import sqlalchemy as sa
from alembic import op

revision = "gc144g2"
down_revision = "gc144g1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("clients") as b:
        b.add_column(sa.Column("consent_whatsapp", sa.Boolean(), nullable=True))
    op.create_table(
        "message_handoffs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("client_id", sa.String(), sa.ForeignKey("clients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("employees.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("to_number", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=12), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_message_handoffs_org_id", "message_handoffs", ["org_id"])
    op.create_index("ix_message_handoffs_order_id", "message_handoffs", ["order_id"])
    op.create_index("ix_message_handoffs_created_at", "message_handoffs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_message_handoffs_created_at", table_name="message_handoffs")
    op.drop_index("ix_message_handoffs_order_id", table_name="message_handoffs")
    op.drop_index("ix_message_handoffs_org_id", table_name="message_handoffs")
    op.drop_table("message_handoffs")
    with op.batch_alter_table("clients") as b:
        b.drop_column("consent_whatsapp")
