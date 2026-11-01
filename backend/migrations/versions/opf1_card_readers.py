"""F2/F3 — card readers (Stripe Terminal, Square Terminal) for the POS and work orders.

- payment_terminals: the shop's registered readers (provider, reader id, label, location).
- terminal_charges: each charge attempt on a reader, settled once into the cash system.
- payment_webhook_events: provider event ids already applied, so redeliveries are no-ops.
- payments.provider_ref: the provider's id for a payment taken on a reader.
- uq_payment_terminal_live_reader: a live reader belongs to one shop (sandbox devices are shared).

Revision ID: opf1
Revises: opb1
"""
import sqlalchemy as sa
from alembic import op

revision = "opf1"
down_revision = "opb1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_terminals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("reader_id", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("location_id", sa.String(), nullable=True),
        sa.Column("provider_location", sa.String(length=120), nullable=True),
        sa.Column("device_type", sa.String(length=60), nullable=True),
        sa.Column("test_mode", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], name="fk_payment_terminals_org_id_organisations"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], name="fk_payment_terminals_location_id_locations",
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_payment_terminals"),
        sa.UniqueConstraint("org_id", "provider", "reader_id", name="uq_payment_terminal_reader"),
    )
    op.create_index("ix_payment_terminals_org_id", "payment_terminals", ["org_id"])
    op.create_index("ix_payment_terminals_location_id", "payment_terminals", ["location_id"])
    op.create_index("uq_payment_terminal_live_reader", "payment_terminals", ["provider", "reader_id"], unique=True,
                    sqlite_where=sa.text("test_mode = 0"), postgresql_where=sa.text("NOT test_mode"))

    op.create_table(
        "terminal_charges",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("terminal_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_ref", sa.String(length=120), nullable=True),
        sa.Column("provider_payment_id", sa.String(length=120), nullable=True),
        sa.Column("order_id", sa.String(), nullable=True),
        sa.Column("sale_id", sa.String(), nullable=True),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2, asdecimal=False), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("payment_id", sa.String(), nullable=True),
        sa.Column("refunded_amount", sa.Numeric(precision=12, scale=2, asdecimal=False), nullable=False),
        sa.Column("created_by_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], name="fk_terminal_charges_org_id_organisations"),
        sa.ForeignKeyConstraint(["terminal_id"], ["payment_terminals.id"],
                                name="fk_terminal_charges_terminal_id_payment_terminals"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_terminal_charges_order_id_orders"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], name="fk_terminal_charges_sale_id_sales"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name="fk_terminal_charges_account_id_accounts"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], name="fk_terminal_charges_payment_id_payments"),
        sa.ForeignKeyConstraint(["created_by_id"], ["employees.id"], name="fk_terminal_charges_created_by_id_employees"),
        sa.PrimaryKeyConstraint("id", name="pk_terminal_charges"),
        sa.UniqueConstraint("payment_id", name="uq_terminal_charges_payment_id"),
    )
    for col in ("org_id", "terminal_id", "provider_ref", "order_id", "sale_id", "created_at"):
        op.create_index(f"ix_terminal_charges_{col}", "terminal_charges", [col])

    op.create_table(
        "payment_webhook_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], name="fk_payment_webhook_events_org_id_organisations"),
        sa.PrimaryKeyConstraint("id", name="pk_payment_webhook_events"),
        sa.UniqueConstraint("provider", "event_id", name="uq_payment_webhook_event"),
    )
    op.create_index("ix_payment_webhook_events_org_id", "payment_webhook_events", ["org_id"])

    with op.batch_alter_table("payments") as b:
        b.add_column(sa.Column("provider_ref", sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payments") as b:
        b.drop_column("provider_ref")
    op.drop_index("ix_payment_webhook_events_org_id", table_name="payment_webhook_events")
    op.drop_table("payment_webhook_events")
    for col in ("org_id", "terminal_id", "provider_ref", "order_id", "sale_id", "created_at"):
        op.drop_index(f"ix_terminal_charges_{col}", table_name="terminal_charges")
    op.drop_table("terminal_charges")
    op.drop_index("uq_payment_terminal_live_reader", table_name="payment_terminals")
    op.drop_index("ix_payment_terminals_location_id", table_name="payment_terminals")
    op.drop_index("ix_payment_terminals_org_id", table_name="payment_terminals")
    op.drop_table("payment_terminals")
