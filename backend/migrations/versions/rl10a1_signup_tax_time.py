"""Launch stream A (B2, B4, B6): sign-up, tax and time.

* sales.tax, sale_lines.tax_rate / tax_mode / tax: POS sales store the tax they charged.
* Locations left on the default "UTC" take their company's timezone (every location used to be
  created on UTC, so reminders and bookings ran on the wrong clock).
* A company's default tax joins its empty sales stack ("tickets_sales"), so it is charged.
* A company with no cash account at all gets a Cash drawer and a Card account per location, so
  it can take its first payment.

Revision ID: rl10a1
Revises: rl10m1
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "rl10a1"
down_revision = "rl10m1"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("sales", "tax"):
        with op.batch_alter_table("sales") as b:
            b.add_column(sa.Column("tax", sa.Numeric(12, 2, asdecimal=False), nullable=False, server_default="0"))
    with op.batch_alter_table("sale_lines") as b:
        if not _has_column("sale_lines", "tax_rate"):
            b.add_column(sa.Column("tax_rate", sa.Float(), nullable=False, server_default="0"))
        if not _has_column("sale_lines", "tax_mode"):
            b.add_column(sa.Column("tax_mode", sa.String(10), nullable=False, server_default="imposed"))
        if not _has_column("sale_lines", "tax"):
            b.add_column(sa.Column("tax", sa.Numeric(12, 2, asdecimal=False), nullable=False, server_default="0"))

    bind = op.get_bind()

    # B6: locations on the old UTC default follow their company's clock.
    bind.execute(sa.text(
        "UPDATE locations SET timezone = (SELECT o.timezone FROM organisations o WHERE o.id = locations.org_id) "
        "WHERE (timezone IS NULL OR timezone = 'UTC') AND EXISTS (SELECT 1 FROM organisations o "
        "WHERE o.id = locations.org_id AND o.timezone IS NOT NULL AND o.timezone <> 'UTC')"))

    # B4: a default tax on an empty sales stack goes onto it.
    rows = bind.execute(sa.text(
        "SELECT t.org_id, t.id FROM taxes t WHERE t.is_default = :yes AND t.active = :yes "
        "AND NOT EXISTS (SELECT 1 FROM tax_rules r WHERE r.org_id = t.org_id AND r.doc_family = 'tickets_sales') "
        "ORDER BY t.org_id, t.created_at"), {"yes": True}).fetchall()
    seen = set()
    rules = []
    for org_id, tax_id in rows:
        if org_id in seen:
            continue
        seen.add(org_id)
        rules.append({"id": str(uuid.uuid4()), "org_id": org_id, "doc_family": "tickets_sales",
                      "tax_id": tax_id, "priority": 0})
    if rules:
        op.bulk_insert(sa.table("tax_rules", sa.column("id"), sa.column("org_id"), sa.column("doc_family"),
                                sa.column("tax_id"), sa.column("priority")), rules)

    # B2: a company with no active account gets a Cash drawer and a Card account per location.
    locs = bind.execute(sa.text(
        "SELECT l.id, l.org_id, l.name FROM locations l WHERE l.active = :yes "
        "AND NOT EXISTS (SELECT 1 FROM accounts a WHERE a.org_id = l.org_id AND a.active = :yes) "
        "ORDER BY l.org_id, l.created_at, l.id"), {"yes": True}).fetchall()
    now = datetime.utcnow()
    first_seen = set()
    accounts = []
    for loc_id, org_id, name in locs:
        suffix = "" if org_id not in first_seen else f" ({name})"
        first_seen.add(org_id)
        for option, label, icon in (("cash", "Cash drawer", "payments"), ("card", "Card", "credit_card")):
            accounts.append({"id": str(uuid.uuid4()), "org_id": org_id, "location_id": loc_id,
                             "name": f"{label}{suffix}"[:120], "icon": icon, "payment_option": option,
                             "is_company": False, "is_virtual": False, "balance": 0, "fee_pct": 0,
                             "fee_fixed": 0, "active": True, "created_at": now})
    if accounts:
        op.bulk_insert(sa.table(
            "accounts", sa.column("id"), sa.column("org_id"), sa.column("location_id"), sa.column("name"),
            sa.column("icon"), sa.column("payment_option"), sa.column("is_company", sa.Boolean),
            sa.column("is_virtual", sa.Boolean), sa.column("balance"), sa.column("fee_pct"),
            sa.column("fee_fixed"), sa.column("active", sa.Boolean), sa.column("created_at", sa.DateTime)),
            accounts)


def downgrade() -> None:
    # The backfilled timezones, stack entries and accounts are real shop data now; leave them.
    with op.batch_alter_table("sale_lines") as b:
        b.drop_column("tax")
        b.drop_column("tax_mode")
        b.drop_column("tax_rate")
    with op.batch_alter_table("sales") as b:
        b.drop_column("tax")
