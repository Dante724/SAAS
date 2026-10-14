"""build 144ai — asset (device) photo

Revision ID: c1e3f5a7b9d2
Revises: b8d0f2a4c6e9
Create Date: 2026-08-07

Adds an optional image_data (data URL) column to devices so an asset photo can be
shown on the board card, in the work-order list (thumb + hover), and general info.
"""
import sqlalchemy as sa
from alembic import op

revision = "c1e3f5a7b9d2"
down_revision = "b8d0f2a4c6e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("image_data", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("image_data")
