"""WS-C — inquiries everywhere.

- lead_types: inquiry types with their own respond-by time.
- leads: type, device (brand/model/serial), custom fields, missed-call merging (call_count,
  last_call_at), an idempotency key unique per shop (Tekly Go offline create) and updated_at.
- clients: structured address (street/city/region/postcode/country). The existing single
  `address` line is left exactly as it is, so nothing already on documents changes.
- organisations.public_employee_name: widened to 16 for the new "first_initial" mode (the column
  itself comes from ojj1, which added it with full | first | custom).
- api_keys.rate_limit_per_min: per-key limit (null = default).

Additive only: no column is dropped or rewritten.

Re-chained after whh1 when claude/ws-c-inquiries was merged onto api/harden-and-docs, so the
history stays linear: ... opd1 → ojj1 → apih1 → whh1 → wsc1.

Revision ID: wsc1
Revises: whh1
"""
import sqlalchemy as sa
from alembic import op

revision = "wsc1"
down_revision = "whh1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_types",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("sla_minutes", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_lead_types_org_id", "lead_types", ["org_id"])

    with op.batch_alter_table("leads") as b:
        b.add_column(sa.Column("lead_type_id", sa.String(), nullable=True))
        b.add_column(sa.Column("device_brand", sa.String(length=100), nullable=True))
        b.add_column(sa.Column("device_model", sa.String(length=100), nullable=True))
        b.add_column(sa.Column("serial", sa.String(length=100), nullable=True))
        b.add_column(sa.Column("custom_data", sa.JSON(), nullable=True))
        b.add_column(sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("last_call_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("idempotency_key", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        b.create_foreign_key("fk_leads_lead_type_id", "lead_types", ["lead_type_id"], ["id"])
    op.create_index("uq_leads_org_idempotency", "leads", ["org_id", "idempotency_key"], unique=True)
    op.create_index("ix_leads_org_created", "leads", ["org_id", "created_at"])

    with op.batch_alter_table("clients") as b:
        b.add_column(sa.Column("addr_street", sa.String(length=200), nullable=True))
        b.add_column(sa.Column("addr_city", sa.String(length=120), nullable=True))
        b.add_column(sa.Column("addr_region", sa.String(length=120), nullable=True))
        b.add_column(sa.Column("addr_postcode", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("addr_country", sa.String(length=2), nullable=True))

    with op.batch_alter_table("organisations") as b:
        b.alter_column("public_employee_name", existing_type=sa.String(length=10), type_=sa.String(length=16),
                       existing_nullable=False, existing_server_default="full")

    with op.batch_alter_table("api_keys") as b:
        b.add_column(sa.Column("rate_limit_per_min", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as b:
        b.drop_column("rate_limit_per_min")
    # "first_initial" doesn't exist before wsc1 and doesn't fit the narrower column.
    op.execute("UPDATE organisations SET public_employee_name = 'first' WHERE public_employee_name = 'first_initial'")
    with op.batch_alter_table("organisations") as b:
        b.alter_column("public_employee_name", existing_type=sa.String(length=16), type_=sa.String(length=10),
                       existing_nullable=False, existing_server_default="full")
    with op.batch_alter_table("clients") as b:
        for c in ("addr_country", "addr_postcode", "addr_region", "addr_city", "addr_street"):
            b.drop_column(c)
    op.drop_index("ix_leads_org_created", table_name="leads")
    op.drop_index("uq_leads_org_idempotency", table_name="leads")
    with op.batch_alter_table("leads") as b:
        b.drop_constraint("fk_leads_lead_type_id", type_="foreignkey")
        for c in ("updated_at", "idempotency_key", "last_call_at", "call_count", "custom_data",
                  "serial", "device_model", "device_brand", "lead_type_id"):
            b.drop_column(c)
    op.drop_index("ix_lead_types_org_id", table_name="lead_types")
    op.drop_table("lead_types")
