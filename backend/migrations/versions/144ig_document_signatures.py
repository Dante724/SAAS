"""144ig — tamper-evident client e-signatures (estimate + work order)."""
from alembic import op
import sqlalchemy as sa

revision = "y3a5c7e9g1i3"
down_revision = "x2z4b6d8f0h2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_signatures",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), index=True),
        sa.Column("doc_type", sa.String(length=20), nullable=False, server_default="estimate"),
        sa.Column("decision", sa.String(length=10), nullable=False, server_default="accept"),
        sa.Column("signer_name", sa.String(length=200), nullable=True),
        sa.Column("signature_png", sa.Text(), nullable=True),
        sa.Column("doc_hash", sa.String(length=64), nullable=True),
        sa.Column("doc_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("signer_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("signed_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("document_signatures")
