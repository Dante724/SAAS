"""Transfer lines record which serialized units are in the box (QA fix F2).

stock_transfer_lines.serial_ids: the SerializedItem ids on the line, so a phone moved to another
branch is in stock there (and sellable by its IMEI) instead of staying at the old branch;
received_serial_ids: the ones that arrived when a short delivery is recorded. Nullable; older
transfers simply have none.

Revision ID: f2tr01
Revises: rt144a1
"""
import sqlalchemy as sa
from alembic import op

revision = "f2tr01"
down_revision = "rt144a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stock_transfer_lines") as b:
        b.add_column(sa.Column("serial_ids", sa.JSON(), nullable=True))
        b.add_column(sa.Column("received_serial_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("stock_transfer_lines") as b:
        b.drop_column("received_serial_ids")
        b.drop_column("serial_ids")
