"""ii144a — inventory identity foundation (council 2026-10-25, "Now" items 1–4).

1. Stock rows per bin: duplicate stock_levels rows that share their full key (same product and bin,
   or same product, location and warehouse tag with no bin) are merged — quantities added, nothing
   lost — then two unique indexes keep it that way. Bins get the warehouse they belong to (their
   location's default product warehouse).
2. Uniqueness, per shop:
   - product_barcodes (new): every barcode a product answers to, backfilled from parts.barcode;
   - SKU among active products, case-insensitive;
   - bin name per warehouse (case-insensitive) and bin code, among active bins;
   - serial among units in stock or reserved on a job (upper-case, spaces ignored).
3. Settings columns for the code generator: SKU template, bin template, barcode prefix.

**Pre-flight:** before anything changes, existing duplicates are listed per shop and the upgrade
stops with that list. Nothing is merged or renamed automatically — the owner decides which product
keeps a code. Fix the listed rows, then run the upgrade again.

Revision ID: ii144a01
Revises: aul3
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "ii144a01"
down_revision = "aul3"
branch_labels = None
depends_on = None

_LIVE = "status IN ('in_stock', 'allocated_to_order')"


def _is_pg(conn) -> bool:
    return conn.dialect.name == "postgresql"


def _true(conn) -> str:
    return "true" if _is_pg(conn) else "1"


def _duplicates(conn) -> list[str]:
    t = _true(conn)
    checks = [
        ("SKU", f"SELECT org_id, lower(sku), count(*) FROM parts WHERE active = {t} AND sku IS NOT NULL "
                "AND sku <> '' GROUP BY org_id, lower(sku) HAVING count(*) > 1"),
        ("barcode", f"SELECT org_id, trim(barcode), count(*) FROM parts WHERE active = {t} AND barcode IS NOT NULL "
                    "AND trim(barcode) <> '' GROUP BY org_id, trim(barcode) HAVING count(*) > 1"),
        ("bin name", f"SELECT org_id, lower(name), count(*) FROM storage_bins WHERE active = {t} "
                     "GROUP BY org_id, location_id, coalesce(warehouse_id, ''), lower(name) HAVING count(*) > 1"),
        ("bin code", f"SELECT org_id, barcode, count(*) FROM storage_bins WHERE active = {t} AND barcode IS NOT NULL "
                     "GROUP BY org_id, barcode HAVING count(*) > 1"),
        ("serial in stock", "SELECT org_id, upper(replace(serial_number, ' ', '')), count(*) FROM serialized_items "
                            f"WHERE {_LIVE} GROUP BY org_id, upper(replace(serial_number, ' ', '')) HAVING count(*) > 1"),
    ]
    names = {r[0]: r[1] for r in conn.execute(sa.text("SELECT id, name FROM organisations")).fetchall()}
    out = []
    for what, sql in checks:
        for org_id, value, n in conn.execute(sa.text(sql)).fetchall():
            out.append(f"  {names.get(org_id, org_id)} ({org_id}): {what} “{value}” is on {n} records")
    return out


def _backfill_bin_warehouses(conn) -> None:
    t = _true(conn)
    conn.execute(sa.text(
        "UPDATE storage_bins SET warehouse_id = (SELECT w.id FROM warehouses w WHERE w.location_id = "
        f"storage_bins.location_id AND w.kind = 'product' AND w.is_default = {t} ORDER BY w.created_at LIMIT 1) "
        "WHERE warehouse_id IS NULL"))


def _merge_stock_rows(conn) -> None:
    """Rows that share the full key are one row split by the old bug: add them into the oldest."""
    for where, key in (("bin_id IS NOT NULL", "part_id, bin_id"),
                       ("bin_id IS NULL", "part_id, location_id, coalesce(warehouse_id, '')")):
        groups = conn.execute(sa.text(
            f"SELECT {key}, count(*) FROM stock_levels WHERE {where} GROUP BY {key} HAVING count(*) > 1")).fetchall()
        for g in groups:
            if len(g) == 3:
                rows = conn.execute(sa.text(
                    "SELECT id, qty_available, qty_reserved FROM stock_levels WHERE part_id = :p AND bin_id = :b "
                    "ORDER BY id"), {"p": g[0], "b": g[1]}).fetchall()
            else:
                rows = conn.execute(sa.text(
                    "SELECT id, qty_available, qty_reserved FROM stock_levels WHERE part_id = :p AND location_id = :l "
                    "AND coalesce(warehouse_id, '') = :w AND bin_id IS NULL ORDER BY id"),
                    {"p": g[0], "l": g[1], "w": g[2]}).fetchall()
            keep = rows[0][0]
            avail = sum(float(r[1] or 0) for r in rows)
            res = sum(float(r[2] or 0) for r in rows)
            conn.execute(sa.text("UPDATE stock_levels SET qty_available = :a, qty_reserved = :r WHERE id = :i"),
                         {"a": avail, "r": res, "i": keep})
            for r in rows[1:]:
                conn.execute(sa.text("DELETE FROM stock_levels WHERE id = :i"), {"i": r[0]})


def upgrade() -> None:
    conn = op.get_bind()
    _backfill_bin_warehouses(conn)     # first, so bin names are compared within their warehouse
    dupes = _duplicates(conn)
    if dupes:
        raise RuntimeError(
            "ii144a01: these codes are used more than once in the same shop. Each must be unique before "
            "the database can enforce it. Change or archive the extra records, then run the upgrade again:\n"
            + "\n".join(dupes[:200]) + (f"\n  … and {len(dupes) - 200} more" if len(dupes) > 200 else ""))

    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("inv_sku_template", sa.String(length=40), nullable=True))
        b.add_column(sa.Column("inv_bin_template", sa.String(length=40), nullable=True))
        b.add_column(sa.Column("inv_barcode_prefix", sa.String(length=2), nullable=True))

    op.create_table(
        "product_barcodes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("part_id", sa.String(), nullable=False),
        sa.Column("barcode", sa.String(length=100), nullable=False),
        sa.Column("barcode_type", sa.String(length=12), nullable=True),
        sa.Column("is_main", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], name="fk_product_barcodes_org_id_organisations"),
        sa.ForeignKeyConstraint(["part_id"], ["parts.id"], name="fk_product_barcodes_part_id_parts",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_product_barcodes"),
    )
    op.create_index("ix_product_barcodes_org_id", "product_barcodes", ["org_id"])
    op.create_index("ix_product_barcodes_part_id", "product_barcodes", ["part_id"])

    rows = conn.execute(sa.text(
        "SELECT id, org_id, trim(barcode), barcode_type, active FROM parts "
        "WHERE barcode IS NOT NULL AND trim(barcode) <> '' AND org_id IS NOT NULL")).fetchall()
    if rows:
        tbl = sa.table("product_barcodes", *(sa.column(c) for c in (
            "id", "org_id", "part_id", "barcode", "barcode_type", "is_main", "active", "created_at")))
        stamp = datetime.utcnow()
        op.bulk_insert(tbl, [{"id": str(uuid.uuid4()), "org_id": o, "part_id": p, "barcode": bc,
                              "barcode_type": bt, "is_main": True, "active": bool(a) if a is not None else True,
                              "created_at": stamp} for p, o, bc, bt, a in rows])

    _merge_stock_rows(conn)

    op.create_index("uq_product_barcodes_org_code", "product_barcodes", ["org_id", "barcode"], unique=True,
                    sqlite_where=sa.text("active = 1"), postgresql_where=sa.text("active"))
    op.create_index("uq_parts_org_sku_active", "parts", ["org_id", sa.text("lower(sku)")], unique=True,
                    sqlite_where=sa.text("active = 1 AND sku IS NOT NULL AND sku <> ''"),
                    postgresql_where=sa.text("active AND sku IS NOT NULL AND sku <> ''"))
    op.create_index("uq_storage_bins_name", "storage_bins",
                    ["location_id", sa.text("coalesce(warehouse_id, '')"), sa.text("lower(name)")], unique=True,
                    sqlite_where=sa.text("active = 1"), postgresql_where=sa.text("active"))
    op.create_index("uq_storage_bins_barcode", "storage_bins", ["org_id", "barcode"], unique=True,
                    sqlite_where=sa.text("active = 1 AND barcode IS NOT NULL"),
                    postgresql_where=sa.text("active AND barcode IS NOT NULL"))
    op.create_index("uq_serialized_items_live_serial", "serialized_items",
                    ["org_id", sa.text("upper(replace(serial_number, ' ', ''))")], unique=True,
                    sqlite_where=sa.text(_LIVE), postgresql_where=sa.text(_LIVE))
    op.create_index("uq_stock_levels_part_bin", "stock_levels", ["part_id", "bin_id"], unique=True,
                    sqlite_where=sa.text("bin_id IS NOT NULL"), postgresql_where=sa.text("bin_id IS NOT NULL"))
    op.create_index("uq_stock_levels_part_loc_unbinned", "stock_levels",
                    ["part_id", "location_id", sa.text("coalesce(warehouse_id, '')")], unique=True,
                    sqlite_where=sa.text("bin_id IS NULL"), postgresql_where=sa.text("bin_id IS NULL"))


def downgrade() -> None:
    for name, table in (("uq_stock_levels_part_loc_unbinned", "stock_levels"),
                        ("uq_stock_levels_part_bin", "stock_levels"),
                        ("uq_serialized_items_live_serial", "serialized_items"),
                        ("uq_storage_bins_barcode", "storage_bins"),
                        ("uq_storage_bins_name", "storage_bins"),
                        ("uq_parts_org_sku_active", "parts")):
        op.drop_index(name, table_name=table)
    op.drop_index("uq_product_barcodes_org_code", table_name="product_barcodes")
    op.drop_index("ix_product_barcodes_part_id", table_name="product_barcodes")
    op.drop_index("ix_product_barcodes_org_id", table_name="product_barcodes")
    op.drop_table("product_barcodes")
    with op.batch_alter_table("organisations") as b:
        b.drop_column("inv_barcode_prefix")
        b.drop_column("inv_bin_template")
        b.drop_column("inv_sku_template")
