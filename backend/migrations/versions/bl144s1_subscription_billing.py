"""Subscription billing: plan, billing period, trial end, billing mode (stripe | custom), the
founding-customer price lock and Stripe ids on organisations, and billing_events (processed
Stripe Billing webhook ids, for idempotency).

Existing shops: plan "starter" becomes "shop", "pro" stays, anything else (the old default
"trial") becomes "pro"; every shop still on a trial gets a fresh 14-day trial from today, so
nobody is locked out the moment this ships.

Revision ID: bl144s1
Revises: mlaunch01
"""
import sqlalchemy as sa
from alembic import op

revision = "bl144s1"
down_revision = "mlaunch01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("billing_period", sa.String(length=10), nullable=True,
                               server_default="month"))
        b.add_column(sa.Column("trial_ends_at", sa.Date(), nullable=True))
        b.add_column(sa.Column("past_due_since", sa.Date(), nullable=True))
        b.add_column(sa.Column("stripe_customer_id", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("stripe_subscription_item_id", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("stripe_seat_item_id", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("billing_period_end", sa.Date(), nullable=True))
        b.add_column(sa.Column("cancel_at_period_end", sa.Boolean(), nullable=True,
                               server_default=sa.false()))
        b.add_column(sa.Column("billing_event_at", sa.Integer(), nullable=True))
        b.add_column(sa.Column("billing_mode", sa.String(length=10), nullable=True,
                               server_default="stripe"))
        b.add_column(sa.Column("billing_note", sa.Text(), nullable=True))
        b.add_column(sa.Column("stripe_price_id", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("price_locked_until", sa.Date(), nullable=True))
        b.create_unique_constraint("uq_organisations_stripe_customer_id", ["stripe_customer_id"])
        b.create_index("ix_organisations_stripe_subscription_id", ["stripe_subscription_id"])

    op.execute("UPDATE organisations SET plan = 'shop' WHERE plan = 'starter'")
    op.execute("UPDATE organisations SET plan = 'pro' "
               "WHERE plan IS NULL OR plan NOT IN ('solo', 'shop', 'pro')")
    trial_end = "CURRENT_DATE + 14" if op.get_bind().dialect.name == "postgresql" \
        else "date('now', '+14 days')"
    op.execute(f"UPDATE organisations SET trial_ends_at = {trial_end} "
               "WHERE (sub_status IS NULL OR sub_status = 'trial') AND trial_ends_at IS NULL")

    op.create_table(
        "billing_events",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_billing_events_org_id", "billing_events", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_billing_events_org_id", table_name="billing_events")
    op.drop_table("billing_events")
    with op.batch_alter_table("organisations") as b:
        b.drop_index("ix_organisations_stripe_subscription_id")
        b.drop_constraint("uq_organisations_stripe_customer_id", type_="unique")
        for col in ("price_locked_until", "stripe_price_id", "billing_note", "billing_mode",
                    "billing_event_at", "cancel_at_period_end", "billing_period_end",
                    "stripe_seat_item_id", "stripe_subscription_item_id", "stripe_subscription_id", "stripe_customer_id",
                    "past_due_since", "trial_ends_at", "billing_period"):
            b.drop_column(col)
