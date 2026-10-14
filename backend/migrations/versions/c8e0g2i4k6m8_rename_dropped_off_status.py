"""144ek — rename the default WO status "Dropped off" → "Not collected".

The status whose key is `dropped` lives in the terminal `dropped` group (the
cancelled/declined/abandoned bucket: it opens the payment window, counts as closed in
reports, is excluded from overdue, and releases reserved stock). Its default display name
"Dropped off" read like intake (a customer dropping a device off to START a repair), so an
owner could set a fresh device to it and silently mark the order terminal.

Data-only, and deliberately narrow: it renames ONLY rows that still carry the exact old
default (key='dropped' AND name='Dropped off'), so any org that renamed it is left untouched.
"""
from alembic import op

revision = "c8e0g2i4k6m8"
down_revision = "b7d9f1h3j5l7"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE order_statuses SET name = 'Not collected' "
        "WHERE key = 'dropped' AND name = 'Dropped off'"
    )


def downgrade():
    op.execute(
        "UPDATE order_statuses SET name = 'Dropped off' "
        "WHERE key = 'dropped' AND name = 'Not collected'"
    )
