"""ii144b — every barcode type: one product number in any format is one barcode.

- product_barcodes.match_key: the code's match key (backend/services/scan_codes.match_key — GTIN-14 for
  any GTIN form, UPC-E expanded, the (01) of a GS1 string; the code itself otherwise). Uniqueness moves
  from the raw text to the key, so `036000291452` (UPC-A) on one product and `0036000291452` (EAN-13) on
  another is a clash, and a scan of either form finds the product.
- storage_bins.barcode_type: how a bin label prints its code.
- stock_item_batches.lot_number: the GS1 (10) batch/lot read from a label at intake.

**Pre-flight:** codes that are the same product number in different forms on two active products are
listed per shop and the upgrade stops, changing nothing. Fix the listed products, then run it again.

The key function is imported from the app (as dp144q1 / rp144p8 import app code): it is the definition
the app itself looks codes up with, so the stored keys and the lookups can't drift apart.

Revision ID: ii144b01
Revises: ii144a01
"""
import sqlalchemy as sa
from alembic import op

revision = "ii144b01"
down_revision = "ii144a01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from backend.services.scan_codes import match_key
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT pb.id, pb.org_id, pb.barcode, pb.barcode_type, pb.active, p.name, pb.part_id, pb.is_main "
        "FROM product_barcodes pb "
        "JOIN parts p ON p.id = pb.part_id")).fetchall()
    keys, seen, dupes, same_part = {}, {}, [], []
    for rid, org, code, kind, active, name, part_id, is_main in rows:
        k = match_key(code, kind) or code
        keys[rid] = k
        if active:
            other = seen.get((org, k))
            if other and other[2] == part_id:
                # one product holding the same number twice (UPC-A and EAN-13): keep one, lose nothing
                same_part.append(rid if not is_main else other[3])
                if is_main:
                    seen[(org, k)] = (name, code, part_id, rid)
                continue
            if other:
                dupes.append(f"  shop {org}: “{other[0]}” has {other[1]} and “{name}” has {code} — the same product number")
            seen.setdefault((org, k), (name, code, part_id, rid))
    if dupes:
        raise RuntimeError(
            "ii144b01: these barcodes are the same product number written two ways (UPC-A and EAN-13, or a "
            "UPC-E and its UPC-A) on two products. Remove or change one of each pair, then run the upgrade again:\n"
            + "\n".join(dupes[:200]))

    for rid in same_part:
        conn.execute(sa.text("DELETE FROM product_barcodes WHERE id = :i"), {"i": rid})
        keys.pop(rid, None)
    with op.batch_alter_table("product_barcodes") as b:
        b.add_column(sa.Column("match_key", sa.String(length=100), nullable=True))
    for rid, k in keys.items():
        conn.execute(sa.text("UPDATE product_barcodes SET match_key = :k WHERE id = :i"), {"k": k, "i": rid})
    op.drop_index("uq_product_barcodes_org_code", table_name="product_barcodes")
    with op.batch_alter_table("product_barcodes") as b:
        b.alter_column("match_key", existing_type=sa.String(length=100), nullable=False)
    op.create_index("uq_product_barcodes_org_key", "product_barcodes", ["org_id", "match_key"], unique=True,
                    sqlite_where=sa.text("active = 1"), postgresql_where=sa.text("active"))

    with op.batch_alter_table("storage_bins") as b:
        b.add_column(sa.Column("barcode_type", sa.String(length=12), nullable=True))
    with op.batch_alter_table("stock_item_batches") as b:
        b.add_column(sa.Column("lot_number", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("stock_item_batches") as b:
        b.drop_column("lot_number")
    with op.batch_alter_table("storage_bins") as b:
        b.drop_column("barcode_type")
    op.drop_index("uq_product_barcodes_org_key", table_name="product_barcodes")
    op.create_index("uq_product_barcodes_org_code", "product_barcodes", ["org_id", "barcode"], unique=True,
                    sqlite_where=sa.text("active = 1"), postgresql_where=sa.text("active"))
    with op.batch_alter_table("product_barcodes") as b:
        b.drop_column("match_key")
