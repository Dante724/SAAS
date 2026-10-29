"""Memberships (docs/SPEC_memberships.md): plans, their included items, memberships on assets,
billing periods and usage; system tags; shop settings; the four membership permissions.

Revision ID: mb145a1
Revises: rl10a1
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "mb145a1"
down_revision = "rl10a1"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _add(table: str, col: sa.Column) -> None:
    if not _has_column(table, col.name):
        with op.batch_alter_table(table) as b:
            b.add_column(col)


def upgrade() -> None:
    for col in (
        sa.Column("membership_spread_revenue", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("membership_grace_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("membership_lapse_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("membership_renewal_notice_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("membership_checkout_prompt", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("membership_prompt_threshold_pct", sa.Float(), nullable=False, server_default="25"),
        sa.Column("membership_prompt_snooze_days", sa.Integer(), nullable=False, server_default="90"),
    ):
        _add("organisations", col)
    _add("clients", sa.Column("membership_offer_snoozed_until", sa.Date(), nullable=True))
    _add("tags", sa.Column("system", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add("tags", sa.Column("source", sa.String(80), nullable=True))
    if not _has_column("order_lines", "membership_id"):
        _add("order_lines", sa.Column("membership_id", sa.String(), nullable=True))
        op.create_index("ix_order_lines_membership_id", "order_lines", ["membership_id"])

    if not _has_table("membership_plans"):
        op.create_table(
            "membership_plans",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("template_key", sa.String(30), nullable=True),
            sa.Column("asset_group_ids", sa.JSON(), nullable=True),
            sa.Column("price_monthly", sa.Numeric(12, 2), nullable=True),
            sa.Column("price_yearly", sa.Numeric(12, 2), nullable=True),
            sa.Column("term", sa.String(10), nullable=False, server_default="rolling"),
            sa.Column("season_start", sa.String(5), nullable=True),
            sa.Column("season_end", sa.String(5), nullable=True),
            sa.Column("presale_days", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("late_join", sa.String(10), nullable=False, server_default="prorated"),
            sa.Column("color", sa.String(20), nullable=False, server_default="#0059ba"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_membership_plans_org_id", "membership_plans", ["org_id"])

    if not _has_table("membership_plan_items"):
        op.create_table(
            "membership_plan_items",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
            sa.Column("plan_id", sa.String(), sa.ForeignKey("membership_plans.id"), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False, server_default="service"),
            sa.Column("service_id", sa.String(), sa.ForeignKey("services.id"), nullable=True),
            sa.Column("label", sa.String(200), nullable=False, server_default=""),
            sa.Column("qty", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("pct", sa.Float(), nullable=False, server_default="0"),
            sa.Column("applies_to", sa.String(10), nullable=False, server_default="labour"),
            sa.Column("window_start", sa.String(5), nullable=True),
            sa.Column("window_end", sa.String(5), nullable=True),
            sa.Column("rollover", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index("ix_membership_plan_items_org_id", "membership_plan_items", ["org_id"])
        op.create_index("ix_membership_plan_items_plan_id", "membership_plan_items", ["plan_id"])

    if not _has_table("memberships"):
        op.create_table(
            "memberships",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
            sa.Column("plan_id", sa.String(), sa.ForeignKey("membership_plans.id"), nullable=False),
            sa.Column("client_id", sa.String(), sa.ForeignKey("clients.id"), nullable=False),
            sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), nullable=True),
            sa.Column("billing", sa.String(10), nullable=False, server_default="yearly"),
            sa.Column("price", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("status", sa.String(12), nullable=False, server_default="active"),
            sa.Column("term_start", sa.Date(), nullable=False),
            sa.Column("term_end", sa.Date(), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("renews", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("manager_id", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
            sa.Column("sold_by_id", sa.String(), sa.ForeignKey("employees.id"), nullable=True),
            sa.Column("ended_on", sa.Date(), nullable=True),
            sa.Column("ended_reason", sa.String(20), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        for c in ("org_id", "plan_id", "client_id", "device_id"):
            op.create_index(f"ix_memberships_{c}", "memberships", [c])
        op.create_index("ix_memberships_org_status_end", "memberships", ["org_id", "status", "period_end"])

    if not _has_table("membership_assets"):
        op.create_table(
            "membership_assets",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
            sa.Column("membership_id", sa.String(), sa.ForeignKey("memberships.id"), nullable=False),
            sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), nullable=False),
            sa.UniqueConstraint("membership_id", "device_id", name="uq_membership_asset"),
        )
        for c in ("org_id", "membership_id", "device_id"):
            op.create_index(f"ix_membership_assets_{c}", "membership_assets", [c])

    if not _has_table("membership_periods"):
        op.create_table(
            "membership_periods",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
            sa.Column("membership_id", sa.String(), sa.ForeignKey("memberships.id"), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("invoice_id", sa.String(), sa.ForeignKey("invoices.id"), nullable=True),
            sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=True),
            sa.Column("due_on", sa.Date(), nullable=False),
            sa.Column("paid_at", sa.DateTime(), nullable=True),
            sa.Column("nudges", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        for c in ("org_id", "membership_id", "invoice_id", "order_id"):
            op.create_index(f"ix_membership_periods_{c}", "membership_periods", [c])

    if not _has_table("membership_usage"):
        op.create_table(
            "membership_usage",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
            sa.Column("membership_id", sa.String(), sa.ForeignKey("memberships.id"), nullable=False),
            sa.Column("plan_item_id", sa.String(), sa.ForeignKey("membership_plan_items.id"), nullable=True),
            sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), nullable=True),
            sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=True),
            sa.Column("order_line_id", sa.String(), nullable=True),
            sa.Column("qty", sa.Float(), nullable=False, server_default="1"),
            sa.Column("value", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("term_start", sa.Date(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("order_line_id", name="uq_membership_usage_line"),
        )
        for c in ("org_id", "membership_id", "order_id"):
            op.create_index(f"ix_membership_usage_{c}", "membership_usage", [c])

    # Roles: nobody gains or loses access they'd expect (see grant_membership_codes).
    from backend.services.permissions import grant_membership_codes
    conn = op.get_bind()
    for rid, raw, explicit in conn.execute(sa.text("SELECT id, permissions, perms_explicit FROM roles")).fetchall():
        perms = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
        new, changed = grant_membership_codes(perms, bool(explicit))
        if changed:
            conn.execute(sa.text("UPDATE roles SET permissions = :p WHERE id = :i").bindparams(
                sa.bindparam("p", type_=sa.JSON())), {"p": new, "i": rid})


def downgrade() -> None:
    for t in ("membership_usage", "membership_periods", "membership_assets", "memberships",
              "membership_plan_items", "membership_plans"):
        if _has_table(t):
            op.drop_table(t)
