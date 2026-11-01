"""Join the kits (kit1p01) and inquiries (wsc1) migrations back into one head.

Both branched from opd1 and change different tables (kits: bundles, order_lines, role
permissions; inquiries: leads, clients, api_keys, organisations), so order doesn't matter.
No schema change here.

Revision ID: mkw01
Revises: kit1p01, wsc1
"""

revision = "mkw01"
down_revision = ("kit1p01", "wsc1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
