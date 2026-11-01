"""Join release/1.0 (head rl10a1) into roadmap/orderry-parity (head mkw01).

Parity's only migrations beyond release are the kits ones (kit1p01, joined in mkw01): kit columns
on order_lines and bundles plus three kit permissions. Release's adds (sessions, admin panel, Go,
inventory identity, webhooks, tax, …) touch other tables and columns, so order doesn't matter.
No schema change here.

Revision ID: syp01
Revises: mkw01, rl10a1
"""

revision = "syp01"
down_revision = ("mkw01", "rl10a1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
