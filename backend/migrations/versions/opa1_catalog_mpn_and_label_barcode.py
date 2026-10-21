"""Team A (Orderry parity) — product MPN and a per-template barcode source.

- parts.mpn (manufacturer part number) + uq_parts_org_mpn_active (org_id, lower(mpn)) for
  active products: one active product per MPN per org, case-insensitive, and the scan index.
- label_templates / document_templates gain barcode_field (barcode|sku|code|mpn) and
  barcode_types ({field: code128|ean13|upca|qr}) so a label can encode the MPN, the SKU, etc.

Revision ID: opa1
Revises: opf1
"""
import sqlalchemy as sa
from alembic import op

revision = "opa1"
down_revision = "opf1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("parts") as b:
        b.add_column(sa.Column("mpn", sa.String(64), nullable=True))
    # mpn is new, so there are no duplicates to trip over; skipped defensively if a re-run finds some.
    dup = op.get_bind().execute(sa.text(
        "SELECT org_id, lower(mpn) FROM parts WHERE active AND mpn IS NOT NULL "
        "GROUP BY org_id, lower(mpn) HAVING count(*) > 1")).fetchall()
    if dup:
        print(f"opa1: NOT adding uq_parts_org_mpn_active; {len(dup)} duplicate active MPN(s)")
    else:
        op.create_index("uq_parts_org_mpn_active", "parts", ["org_id", sa.text("lower(mpn)")], unique=True,
                        sqlite_where=sa.text("active = 1 AND mpn IS NOT NULL"),
                        postgresql_where=sa.text("active AND mpn IS NOT NULL"))
    for table in ("label_templates", "document_templates"):
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column("barcode_field", sa.String(12), nullable=True))
            b.add_column(sa.Column("barcode_types", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in ("document_templates", "label_templates"):
        with op.batch_alter_table(table) as b:
            b.drop_column("barcode_types")
            b.drop_column("barcode_field")
    if "uq_parts_org_mpn_active" in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("parts")}:
        op.drop_index("uq_parts_org_mpn_active", table_name="parts")
    with op.batch_alter_table("parts") as b:
        b.drop_column("mpn")
