"""Several items per job (multi-asset) — tools / equipment / device shops only.

Adds organisations.multi_asset_jobs (off), the order_items table (one row per item a customer
left on a job, org- and order-scoped) and order_lines.order_item_id (which item a line is for;
null = the whole job). Additive: with the setting off nothing reads these.

Revision ID: ma144p5
Revises: nt144p4
"""
import sqlalchemy as sa
from alembic import op

revision = "ma144p5"
down_revision = "nt144p4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("multi_asset_jobs", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "order_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id", name="fk_order_items_org_id_organisations"), nullable=False),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id", name="fk_order_items_order_id_orders"), nullable=False),
        sa.Column("asset_id", sa.String(), sa.ForeignKey("devices.id", name="fk_order_items_asset_id_devices"), nullable=True),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("complaint", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="open"),
        sa.Column("collected_at", sa.DateTime(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", "asset_id", name="uq_order_items_order_asset"),
    )
    op.create_index("ix_order_items_org_id", "order_items", ["org_id"])
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_asset_id", "order_items", ["asset_id"])

    with op.batch_alter_table("order_lines") as b:
        b.add_column(sa.Column("order_item_id", sa.String(), nullable=True))
        b.create_foreign_key("fk_order_lines_order_item_id_order_items", "order_items", ["order_item_id"], ["id"])
        b.create_index("ix_order_lines_order_item_id", ["order_item_id"])


def downgrade() -> None:
    with op.batch_alter_table("order_lines") as b:
        b.drop_index("ix_order_lines_order_item_id")
        b.drop_constraint("fk_order_lines_order_item_id_order_items", type_="foreignkey")
        b.drop_column("order_item_id")
    op.drop_index("ix_order_items_asset_id", table_name="order_items")
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_index("ix_order_items_org_id", table_name="order_items")
    op.drop_table("order_items")
    with op.batch_alter_table("organisations") as b:
        b.drop_column("multi_asset_jobs")
