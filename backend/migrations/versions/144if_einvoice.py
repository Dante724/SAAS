"""144if — e-invoicing fields on invoices (in-house UBL 2.1)."""
from alembic import op
import sqlalchemy as sa

revision = "x2z4b6d8f0h2"
down_revision = "w1y3a5c7e9g1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("invoices", sa.Column("einvoice_status", sa.String(length=12), nullable=False, server_default="none"))
    op.add_column("invoices", sa.Column("einvoice_number", sa.String(length=60), nullable=True))
    op.add_column("invoices", sa.Column("einvoice_format", sa.String(length=20), nullable=True))
    op.add_column("invoices", sa.Column("einvoice_issued_at", sa.DateTime(), nullable=True))
    op.add_column("invoices", sa.Column("einvoice_xml", sa.Text(), nullable=True))


def downgrade():
    for c in ("einvoice_xml", "einvoice_issued_at", "einvoice_format", "einvoice_number", "einvoice_status"):
        op.drop_column("invoices", c)
