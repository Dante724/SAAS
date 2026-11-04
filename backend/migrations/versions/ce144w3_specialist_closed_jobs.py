"""Existing technician roles stop editing closed jobs (QA wave 3, section E).

A technician on the Specialist template added a line to a delivered, paid job, and the job owed
money again. New roles from the template no longer get can_edit_closed_ticket
(services/permissions.TEMPLATES). This takes it off existing roles too, but only roles that are
still exactly what the template saves: every code the role lists has the template's value, it
denies the template's core codes, and it can make calls (which tells it apart from Partner). A
role someone changed by hand no longer matches and keeps what it has, as do admin, owner,
manager and partner roles. The owner approved updating untouched template roles this way.

Each changed role's previous permissions go to role_permission_backups (made by rt144a1), so
downgrade restores them exactly. Re-running changes nothing: an updated role no longer matches.

Revision ID: ce144w3
Revises: ret01
"""
import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "ce144w3"
down_revision = "ret01"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# Frozen copies: what the Specialist template denied before this change, and the opt-in codes.
OPT_IN = {"can_override_reservation", "can_change_kit_price", "can_use_sense_suggestions",
          "can_approve_agent_sends"}
SPECIALIST_DENY = {
    'can_accrue_payroll', 'can_approve_discount', 'can_change_memberships',
    'can_config_exceptional_commissions', 'can_correct_transactions', 'can_create_bonuses',
    'can_create_credit_notes', 'can_create_expenses', 'can_create_incomes', 'can_create_invoices',
    'can_create_payment_links', 'can_create_penalties', 'can_create_returns', 'can_delete_clients',
    'can_delete_invoices', 'can_delete_orders', 'can_delete_products', 'can_delete_refunds',
    'can_delete_sales', 'can_delete_tickets', 'can_delete_transactions', 'can_edit_invoices',
    'can_edit_issued_invoices', 'can_export_accountant', 'can_export_clients', 'can_export_payroll',
    'can_export_sales', 'can_issue_credit_notes', 'can_make_client_payments', 'can_manage_accounts',
    'can_manage_employees', 'can_manage_membership_plans', 'can_manage_payroll', 'can_manage_roles',
    'can_manage_settings', 'can_manage_terminals', 'can_refund_card_payments',
    'can_retroactive_cash', 'can_see_asset_cost', 'can_see_profit', 'can_set_cost_price',
    'can_set_discount', 'can_set_discount_type', 'can_take_payments', 'can_transfer_money',
    'can_view_balance', 'can_view_cashbox', 'can_view_cashflow', 'can_view_cashflow_any_period',
    'can_view_credit_notes', 'can_view_finance', 'can_view_invoices', 'can_view_payroll',
    'can_view_purchase_price', 'can_view_reports',
}
# Must be present and off: proves the role was cut from the template, not hand-built.
CORE_DENY = {"can_manage_settings", "can_manage_roles", "can_manage_employees",
             "can_view_finance", "can_view_purchase_price"}
CODE = "can_edit_closed_ticket"
BACKUP = "role_permission_backups"


def _template_value(code: str) -> bool:
    return code not in OPT_IN and code not in SPECIALIST_DENY


def is_untouched_specialist(perms: dict) -> bool:
    if not perms or perms.get(CODE) is not True or perms.get("can_make_calls") is not True:
        return False
    if any(perms.get(c) is not False for c in CORE_DENY):
        return False
    return all(v is _template_value(k) for k, v in perms.items())


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
        if rid in done or not is_untouched_specialist(perms):
            continue
        conn.execute(sa.text(f"INSERT INTO {BACKUP} (role_id, migration, org_id, permissions) "
                             "VALUES (:r, :m, :o, :p)").bindparams(sa.bindparam("p", type_=sa.JSON())),
                     {"r": rid, "m": revision, "o": org_id, "p": perms})
        conn.execute(sa.text("UPDATE roles SET permissions = :p WHERE id = :i").bindparams(
            sa.bindparam("p", type_=sa.JSON())), {"p": {**perms, CODE: False}, "i": rid})
        log.info("ce144w3: Specialist role can no longer edit closed jobs (org %s, role %r)", org_id, name)


def downgrade() -> None:
    conn = op.get_bind()
    if not sa.inspect(conn).has_table(BACKUP):
        return
    rows = conn.execute(sa.text(f"SELECT role_id, permissions FROM {BACKUP} WHERE migration = :m"),
                        {"m": revision}).fetchall()
    for rid, raw in rows:
        conn.execute(sa.text("UPDATE roles SET permissions = :p WHERE id = :i").bindparams(
            sa.bindparam("p", type_=sa.JSON())), {"p": _load(raw), "i": rid})
    conn.execute(sa.text(f"DELETE FROM {BACKUP} WHERE migration = :m"), {"m": revision})
