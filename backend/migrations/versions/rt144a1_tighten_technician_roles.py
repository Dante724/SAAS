"""Existing technician roles stop seeing money (QA fix A, owner-approved 1 Nov 2026).

New roles from the Specialist (technician) template already deny cash balances, invoices and
credit notes, discounts and taking payments (services/permissions._NOT_FOR_STAFF). This brings
existing technician roles in line — but only roles that are still exactly what the old template
gave. Roles don't record which template they came from, so "still the template" is the test:
on every code the role lists, its value equals the old Specialist template's, and it denies the
template's core codes (settings, roles, staff, finance, cost price). A role someone edited —
granting can_view_finance, say — no longer matches and is left alone, as are admin, owner and
manager roles (they grant settings).

Each changed role's previous permissions are kept in role_permission_backups, so downgrade
restores them exactly. Re-running changes nothing: a tightened role no longer matches.

Revision ID: rt144a1
Revises: bl144s1
"""
import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "rt144a1"
down_revision = "bl144s1"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# Frozen copies: the template as it was before this change, and what this change turns off.
OPT_IN = {"can_override_reservation", "can_change_kit_price",
          "can_use_sense_suggestions", "can_approve_agent_sends"}
OLD_SPECIALIST_DENY = {
    "can_manage_settings", "can_manage_roles", "can_manage_employees",
    "can_view_finance", "can_view_cashflow", "can_view_cashflow_any_period",
    "can_transfer_money", "can_delete_transactions", "can_correct_transactions",
    "can_retroactive_cash", "can_manage_accounts",
    "can_view_payroll", "can_manage_payroll", "can_accrue_payroll",
    "can_export_payroll", "can_create_bonuses", "can_create_penalties",
    "can_config_exceptional_commissions",
    "can_view_reports", "can_export_accountant", "can_export_clients", "can_export_sales",
    "can_delete_clients", "can_delete_orders", "can_delete_sales",
    "can_delete_refunds", "can_delete_products", "can_delete_invoices",
    "can_delete_tickets", "can_issue_credit_notes",
    "can_refund_card_payments", "can_manage_terminals",
    "can_view_purchase_price", "can_see_profit", "can_set_cost_price", "can_see_asset_cost",
    "can_take_payments", "can_create_payment_links",
    "can_manage_membership_plans", "can_change_memberships",
}
# Must be present and off: proves the role was cut from the template, not hand-built.
CORE_DENY = {"can_manage_settings", "can_manage_roles", "can_manage_employees",
             "can_view_finance", "can_view_purchase_price"}
TIGHTEN = {
    "can_view_balance", "can_view_cashbox",
    "can_view_invoices", "can_create_invoices", "can_edit_invoices", "can_edit_issued_invoices",
    "can_view_credit_notes", "can_create_credit_notes",
    "can_create_incomes", "can_create_expenses", "can_create_returns",
    "can_approve_discount", "can_set_discount", "can_set_discount_type",
    "can_make_client_payments",
}
BACKUP = "role_permission_backups"


def _old_template_value(code: str) -> bool:
    return code not in OPT_IN and code not in OLD_SPECIALIST_DENY


def is_untouched_technician(perms: dict) -> bool:
    if not perms or any(perms.get(c) is not False for c in CORE_DENY):
        return False
    return all(v is _old_template_value(k) for k, v in perms.items())


def _load(raw) -> dict:
    return raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})


def upgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table(BACKUP):
        op.create_table(
            BACKUP,
            sa.Column("role_id", sa.String(), primary_key=True),
            sa.Column("migration", sa.String(40), primary_key=True),
            sa.Column("org_id", sa.String()),
            sa.Column("permissions", sa.JSON()),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
    done = {r[0] for r in conn.execute(sa.text(f"SELECT role_id FROM {BACKUP} WHERE migration = :m"),
                                       {"m": revision}).fetchall()}
    rows = conn.execute(sa.text("SELECT id, org_id, name, permissions FROM roles WHERE perms_explicit = :t"),
                        {"t": True}).fetchall()
    for rid, org_id, name, raw in rows:
        perms = _load(raw)
        if rid in done or not is_untouched_technician(perms):
            continue
        if not any(perms.get(c) is True for c in TIGHTEN):
            continue
        new = {**perms, **{c: False for c in TIGHTEN}}
        conn.execute(sa.text(f"INSERT INTO {BACKUP} (role_id, migration, org_id, permissions) "
                             "VALUES (:r, :m, :o, :p)").bindparams(sa.bindparam("p", type_=sa.JSON())),
                     {"r": rid, "m": revision, "o": org_id, "p": perms})
        conn.execute(sa.text("UPDATE roles SET permissions = :p WHERE id = :i").bindparams(
            sa.bindparam("p", type_=sa.JSON())), {"p": new, "i": rid})
        log.info("rt144a1: technician role tightened (org %s, role %r)", org_id, name)


def downgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table(BACKUP):
        return
    rows = conn.execute(sa.text(f"SELECT role_id, permissions FROM {BACKUP} WHERE migration = :m"),
                        {"m": revision}).fetchall()
    for rid, raw in rows:
        conn.execute(sa.text("UPDATE roles SET permissions = :p WHERE id = :i").bindparams(
            sa.bindparam("p", type_=sa.JSON())), {"p": _load(raw), "i": rid})
    op.drop_table(BACKUP)
