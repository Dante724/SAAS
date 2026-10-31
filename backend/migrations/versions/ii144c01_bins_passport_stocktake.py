"""ii144c — bins, unit passport and stocktake depth (council 2026-10-25, items 9–11).

- warehouses.default_bin_id: where stock received without a bin lands.
- serialized_items.supplier_id / supplier_warranty_until: the unit passport. Units that came in on a
  purchase order get their supplier from it.
- serial_events: a unit's history (received, moved, used on a job, sold, written off, …).
- stocktakes.category_id / blind / by_bin, and stocktake_serials so one serial counts once.

Revision ID: ii144c01
Revises: ii144b01
"""
import sqlalchemy as sa
from alembic import op

revision = "ii144c01"
down_revision = "ii144b01"
branch_labels = None
depends_on = None


def _live_serial_index() -> None:
    """SQLite's batch rebuild drops the expression indexes it can't reflect; put this one back."""
    # (SQLite only; a rebuild happens only when the change needs one, so the index may still be there)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_serialized_items_live_serial ON serialized_items "
               "(org_id, upper(replace(serial_number, ' ', ''))) "
               "WHERE status IN ('in_stock', 'allocated_to_order')")


def _add(table: str, col: sa.Column, fk: tuple | None = None) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table) as b:     # a table-level FK keeps its ON DELETE on SQLite
            b.add_column(col)
            if fk:
                name, ref_table, ondelete = fk
                b.create_foreign_key(name, ref_table, [col.name], ["id"], ondelete=ondelete)
        if table == "serialized_items":
            _live_serial_index()
        return
    op.add_column(table, col)
    if fk:
        name, ref_table, ondelete = fk
        op.create_foreign_key(name, table, ref_table, [col.name], ["id"], ondelete=ondelete)


def _drop(table: str, cols: list[str], fks: list[str]) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table) as b:
            for fk in fks:
                b.drop_constraint(fk, type_="foreignkey")
            for c in cols:
                b.drop_column(c)
        if table == "serialized_items":
            _live_serial_index()
        return
    for fk in fks:
        op.drop_constraint(fk, table, type_="foreignkey")
    for c in cols:
        op.drop_column(table, c)


def upgrade() -> None:
    _add("warehouses", sa.Column("default_bin_id", sa.String(), nullable=True),
         ("fk_warehouses_default_bin", "storage_bins", "SET NULL"))
    _add("serialized_items", sa.Column("supplier_id", sa.String(), nullable=True),
         ("fk_serialized_items_supplier", "suppliers", "SET NULL"))
    _add("serialized_items", sa.Column("supplier_warranty_until", sa.Date(), nullable=True))
    op.execute("UPDATE serialized_items SET supplier_id = (SELECT po.supplier_id FROM purchase_orders po "
               "WHERE po.id = serialized_items.po_id) WHERE po_id IS NOT NULL")

    op.create_table(
        "serial_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("item_id", sa.String(), sa.ForeignKey("serialized_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("ref_type", sa.String(length=30), nullable=True),
        sa.Column("ref_id", sa.String(), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("location_id", sa.String(), sa.ForeignKey("locations.id"), nullable=True),
        sa.Column("bin_id", sa.String(), sa.ForeignKey("storage_bins.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_serial_events_org_id", "serial_events", ["org_id"])
    op.create_index("ix_serial_events_item_created", "serial_events", ["item_id", "created_at"])

    _add("stocktakes", sa.Column("category_id", sa.String(), nullable=True),
         ("fk_stocktakes_category", "categories", "SET NULL"))
    _add("stocktakes", sa.Column("blind", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add("stocktakes", sa.Column("by_bin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "stocktake_serials",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("stocktake_id", sa.String(), sa.ForeignKey("stocktakes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_id", sa.String(), sa.ForeignKey("stocktake_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("serial_key", sa.String(length=120), nullable=False),
        sa.Column("serialized_item_id", sa.String(), sa.ForeignKey("serialized_items.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("uq_stocktake_serials_key", "stocktake_serials", ["stocktake_id", "serial_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_stocktake_serials_key", table_name="stocktake_serials")
    op.drop_table("stocktake_serials")
    _drop("stocktakes", ["by_bin", "blind", "category_id"], ["fk_stocktakes_category"])
    op.drop_index("ix_serial_events_item_created", table_name="serial_events")
    op.drop_index("ix_serial_events_org_id", table_name="serial_events")
    op.drop_table("serial_events")
    _drop("serialized_items", ["supplier_warranty_until", "supplier_id"], ["fk_serialized_items_supplier"])
    _drop("warehouses", ["default_bin_id"], ["fk_warehouses_default_bin"])
