"""organisations.doc_brand_color_on: print the brand colour on documents (opt-in).

Settings › Documents turns it on when the owner picks a colour there. Shops that set
brand_primary_color only for the white-label portal keep printing exactly as before.

Revision ID: sds01
Revises: idg02
"""
import sqlalchemy as sa
from alembic import op

revision = "sds01"
down_revision = "idg02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("organisations")}
    if "doc_brand_color_on" not in have:
        op.add_column("organisations", sa.Column("doc_brand_color_on", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.drop_column("doc_brand_color_on")
