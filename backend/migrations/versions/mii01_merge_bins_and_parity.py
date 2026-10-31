"""Join the bins/passport/stocktake chain (ii144c01) and the parity head (mkw01) into one head.

ii144a–c change stock, bins, serialized units and stocktakes; mkw01's side changes kits,
inquiries and the rest of parity. They touch different columns, so order doesn't matter.
No schema change here.

Revision ID: mii01
Revises: ii144c01, mkw01
"""

revision = "mii01"
down_revision = ("ii144c01", "mkw01")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
