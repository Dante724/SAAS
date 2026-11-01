"""Several items per job, phase 2 — split an item into its own job; recommendations per item.

Adds orders.split_from_order_id (the job an item was split off from) and
recommendations.order_item_id (which item a recommendation is for; null = the whole job).
Additive and nullable: nothing reads them unless the shop uses several items per job.

Revision ID: mb144p6
Revises: ma144p5
"""
import sqlalchemy as sa
from alembic import op

revision = "mb144p6"
down_revision = "ma144p5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as b:
        b.add_column(sa.Column("split_from_order_id", sa.String(), nullable=True))
        b.create_foreign_key("fk_orders_split_from_order_id_orders", "orders", ["split_from_order_id"], ["id"])
        b.create_index("ix_orders_split_from_order_id", ["split_from_order_id"])

    with op.batch_alter_table("recommendations") as b:
        b.add_column(sa.Column("order_item_id", sa.String(), nullable=True))
        b.create_foreign_key("fk_recommendations_order_item_id_order_items", "order_items", ["order_item_id"], ["id"])
        b.create_index("ix_recommendations_order_item_id", ["order_item_id"])


def downgrade() -> None:
    with op.batch_alter_table("recommendations") as b:
        b.drop_index("ix_recommendations_order_item_id")
        b.drop_constraint("fk_recommendations_order_item_id_order_items", type_="foreignkey")
        b.drop_column("order_item_id")
    with op.batch_alter_table("orders") as b:
        b.drop_index("ix_orders_split_from_order_id")
        b.drop_constraint("fk_orders_split_from_order_id_orders", type_="foreignkey")
        b.drop_column("split_from_order_id")
