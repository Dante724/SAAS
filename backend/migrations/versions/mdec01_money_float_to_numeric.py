"""Audit R4: the money-like columns still stored as Float -> exact NUMERIC.

Build 144ar moved the main money columns to NUMERIC(12,2); these were missed. Like those, the ORM
maps them with asdecimal=False for now, so Python still receives floats (no arithmetic change).

  organisations.po_approval_threshold      NUMERIC(12,2)
  organisations.loyalty_point_value        NUMERIC(12,4)  (value of one point: can be under a cent)
  employees.wage_value / hourly_cost_rate / billable_rate        NUMERIC(12,2)
  clients.disc_services_value / disc_materials_value             NUMERIC(12,2)  (a percent or price)
  time_entries.cost_rate / billable_rate   NUMERIC(12,2)  (per-hour rate snapshots)
  coupons.value                            NUMERIC(12,2)  (percent or fixed amount)

Existing values round to the column's scale (Postgres rounds half away from zero). Reversible.

Revision ID: mdec01
Revises: imc01
"""
import sqlalchemy as sa
from alembic import op

revision = "mdec01"
down_revision = "imc01"
branch_labels = None
depends_on = None

NUM2 = sa.Numeric(12, 2, asdecimal=False)
NUM4 = sa.Numeric(12, 4, asdecimal=False)

COLUMNS = {
    "organisations": {"po_approval_threshold": NUM2, "loyalty_point_value": NUM4},
    "employees": {"wage_value": NUM2, "hourly_cost_rate": NUM2, "billable_rate": NUM2},
    "clients": {"disc_services_value": NUM2, "disc_materials_value": NUM2},
    "time_entries": {"cost_rate": NUM2, "billable_rate": NUM2},
    "coupons": {"value": NUM2},
}


def upgrade() -> None:
    for table, cols in COLUMNS.items():
        with op.batch_alter_table(table) as b:
            for col, typ in cols.items():
                b.alter_column(col, type_=typ, existing_type=sa.Float())


def downgrade() -> None:
    for table, cols in COLUMNS.items():
        with op.batch_alter_table(table) as b:
            for col, typ in cols.items():
                b.alter_column(col, type_=sa.Float(), existing_type=typ)
