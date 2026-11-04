"""Import centre: import batches (audit + undo) and remembered column mappings.

Revision ID: imc01
Revises: aqp01
"""
import sqlalchemy as sa
from alembic import op

revision = "imc01"
down_revision = "aqp01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("employees.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("undo", sa.JSON(), nullable=True),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_import_batches_org_kind", "import_batches", ["org_id", "kind", "created_at"])
    op.create_table(
        "import_mappings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("supplier_id", sa.String(), sa.ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=True),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_import_mappings_org_kind", "import_mappings", ["org_id", "kind", "supplier_id"])


def downgrade() -> None:
    op.drop_index("ix_import_mappings_org_kind", table_name="import_mappings")
    op.drop_table("import_mappings")
    op.drop_index("ix_import_batches_org_kind", table_name="import_batches")
    op.drop_table("import_batches")
