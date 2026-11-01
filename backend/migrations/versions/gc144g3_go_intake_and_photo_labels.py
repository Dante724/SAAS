"""Tekly Go tablet intake and photo labels.

- orders.intake_photo_labels: {blob_id: intake|before|after|damage}, what each job photo shows.
- organisations.dropoff_terms: the shop's own drop-off wording the customer signs at intake
  (empty = the built-in text).

Revision ID: gc144g3
Revises: gc144g2
"""
import sqlalchemy as sa
from alembic import op

revision = "gc144g3"
down_revision = "gc144g2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as b:
        b.add_column(sa.Column("intake_photo_labels", sa.JSON(), nullable=True))
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("dropoff_terms", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.drop_column("dropoff_terms")
    with op.batch_alter_table("orders") as b:
        b.drop_column("intake_photo_labels")
