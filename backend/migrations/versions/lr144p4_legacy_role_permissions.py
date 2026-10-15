"""Phase 4 — convert old-format role permissions to real permission codes.

Early roles stored permissions per area, e.g. {"orders": "write", "finance": "none"}.
Enforcement (require_perm / has_perm / role_allows) only blocks a permission code that is
explicitly False, so those roles looked restricted in the data but had full access.

This migration rewrites each such role to real codes:
  * a legacy area set to "read" denies that area's change/delete codes;
  * a legacy area set to anything else except "write" ("none", false, ...) also denies its
    view codes;
  * "write" (or true) denies nothing;
  * keys that are not real codes are dropped. Enforcement already ignored them, so dropping
    one never changes what a role can do.
The mapping below is frozen on purpose (no imports from the app). It only ever adds denials:
a real code that is already False stays False, and a code a legacy area denies becomes False
even if it was True.

Roles that are exactly {"all": "write"} (the owner role registration creates) are left as they
are: full access.

Idempotent: a converted role holds only real codes with boolean values, so a second run
changes nothing.

Revision ID: lr144p4
Revises: rl10a1
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "lr144p4"
down_revision = "rl10a1"
branch_labels = None
depends_on = None


# Every real permission code at the time of writing (services/permissions.py CATALOG).
REAL_CODES = frozenset({
    # General
    "can_make_client_payments", "can_view_purchase_price", "can_set_discount", "can_set_discount_type",
    "can_set_cost_price", "can_see_profit", "display_in_managers", "display_in_assignees", "can_send_sms",
    "can_see_asset_cost", "can_config_exceptional_commissions", "can_view_own_compensation",
    # Workflows
    "can_create_tickets", "can_view_comeback_alerts", "can_create_estimates", "can_transfer_tickets",
    "can_delete_tickets", "can_assign_staff", "can_view_client_info", "can_view_asset_info",
    "can_view_item_prices", "can_view_invoices_tab", "can_edit_closed_ticket", "can_manage_shared_filters",
    "can_add_custom_service", "can_add_custom_product", "can_add_without_stock", "can_create_payment_links",
    "can_add_services", "can_add_products", "can_approve_discount",
    # Inventory
    "can_manage_inventory", "can_create_purchase_orders", "can_reserve_stock", "can_override_reservation",
    "can_receive_inventory_alerts",
    # Catalog
    "can_view_labors", "can_edit_labors", "can_delete_labors", "can_convert_product_to_asset",
    "can_convert_asset_to_product", "can_create_products", "can_edit_products", "can_delete_products",
    "can_track_serials", "can_manage_bundles",
    # Sales
    "can_create_sales", "can_delete_sales", "can_sell_services", "can_edit_sale_price", "can_export_sales",
    # Invoices
    "can_view_invoices", "can_create_invoices", "can_edit_invoices", "can_delete_invoices",
    "can_edit_issued_invoices",
    # Finance
    "can_create_incomes", "can_transfer_money", "can_create_expenses", "can_retroactive_cash",
    "can_view_cashflow", "can_view_cashflow_any_period", "can_view_cashbox", "can_export_accountant",
    "can_correct_transactions", "can_delete_transactions", "can_manage_accounts", "can_view_balance",
    "can_create_returns", "can_delete_refunds",
    # Payroll
    "can_view_payroll", "can_accrue_payroll", "can_create_penalties", "can_create_bonuses",
    "can_export_payroll",
    # Settings
    "can_manage_settings", "can_manage_roles", "can_manage_employees",
    # Checklists
    "can_manage_checklist_templates", "can_create_checklist", "can_edit_checklist", "can_delete_checklist",
    # Reports & data
    "can_view_reports", "can_export_clients", "can_delete_clients",
    # Administration
    "can_view_finance", "can_manage_payroll", "can_manage_staff", "can_delete_orders", "can_recommend",
    "can_publish_recommend", "can_reply_customer",
    # Added on release/1.0 (jobs, payments, credit notes, calls, chats, Tekly Sense, payroll).
    # A role may already hold these as explicit denials; dropping them would widen access.
    "can_close_tickets", "can_take_payments", "can_take_card_payments", "can_refund_card_payments",
    "can_manage_terminals", "can_view_credit_notes", "can_create_credit_notes",
    "can_issue_credit_notes", "can_create_clients", "can_make_calls", "can_manage_chats",
    "can_use_sense_suggestions", "can_approve_agent_sends", "can_approve_rec_for_customer",
    "can_view_own_payroll",
})

# legacy area -> (codes denied when the area is "read", extra codes denied when it is "none").
# "none" denies both tuples.
LEGACY_AREA_DENIALS = {
    "orders": (
        ("can_create_tickets", "can_create_estimates", "can_transfer_tickets", "can_delete_tickets",
         "can_delete_orders", "can_edit_closed_ticket", "can_assign_staff", "can_add_services",
         "can_add_products", "can_add_custom_service", "can_add_custom_product", "can_set_discount",
         "can_approve_discount", "can_close_tickets"),
        (),  # there is no "view work orders" code; "none" denies what "read" denies
    ),
    "clients": (
        ("can_create_clients", "can_delete_clients", "can_export_clients"),
        ("can_view_client_info",),
    ),
    "inventory": (
        ("can_manage_inventory", "can_create_purchase_orders", "can_override_reservation",
         "can_create_products", "can_edit_products", "can_delete_products", "can_add_without_stock",
         "can_manage_bundles", "can_track_serials"),
        ("can_reserve_stock", "can_view_purchase_price"),
    ),
    "finance": (
        ("can_create_incomes", "can_transfer_money", "can_create_expenses", "can_retroactive_cash",
         "can_correct_transactions", "can_delete_transactions", "can_manage_accounts",
         "can_create_returns", "can_delete_refunds", "can_create_invoices", "can_edit_invoices",
         "can_delete_invoices", "can_edit_issued_invoices", "can_make_client_payments",
         "can_create_payment_links", "can_accrue_payroll", "can_create_penalties", "can_create_bonuses",
         "can_manage_payroll", "can_take_payments", "can_take_card_payments",
         "can_refund_card_payments", "can_create_credit_notes", "can_issue_credit_notes"),
        ("can_view_finance", "can_view_credit_notes", "can_view_cashflow", "can_view_cashflow_any_period", "can_view_cashbox",
         "can_view_balance", "can_export_accountant", "can_view_invoices", "can_view_invoices_tab",
         "can_view_payroll", "can_export_payroll", "can_see_profit", "can_view_purchase_price"),
    ),
    "staff": (
        ("can_manage_employees", "can_manage_roles", "can_manage_staff"),
        (),
    ),
    "settings": (
        ("can_manage_settings", "can_manage_roles"),
        (),
    ),
    "reports": (
        ("can_export_clients", "can_export_sales", "can_export_accountant", "can_export_payroll"),
        ("can_view_reports",),
    ),
}

# Non-boolean values on a real code that mean "denied". Any other non-boolean value on a real
# code was already treated as allowed, so it is dropped (allowed either way).
_DENY_STRINGS = {"false", "no", "none", "off", "deny", "0"}


def _level(value) -> str:
    """Legacy area value -> "write" | "read" | "none". Anything unrecognised is "none"."""
    if value is True:
        return "write"
    if isinstance(value, str) and value.strip().lower() in ("write", "read"):
        return value.strip().lower()
    return "none"


def _denials_for(area: str, level: str) -> set:
    if level == "write" or area not in LEGACY_AREA_DENIALS:
        return set()
    read_codes, none_codes = LEGACY_AREA_DENIALS[area]
    return set(read_codes) | (set(none_codes) if level == "none" else set())


def convert(perms):
    """Return the converted permissions dict, or None when the role needs no change."""
    if isinstance(perms, str):
        try:
            perms = json.loads(perms)
        except ValueError:
            return None
    if not isinstance(perms, dict) or not perms:
        return None
    if perms == {"all": "write"}:
        return None   # owner role: full access, by design
    out = {}
    denied = set()
    for key, value in perms.items():
        if key in REAL_CODES:
            if isinstance(value, bool):
                out[key] = value
            elif value == 0 or (isinstance(value, str) and value.strip().lower() in _DENY_STRINGS):
                out[key] = False
        elif key == "all":
            level = _level(value)
            for area in LEGACY_AREA_DENIALS:
                denied |= _denials_for(area, level)
        else:
            denied |= _denials_for(key, _level(value))
    for code in denied:
        out[code] = False
    return None if out == perms else out


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("roles"):
        return
    roles = sa.table("roles", sa.column("id", sa.String), sa.column("permissions", sa.JSON))
    for role_id, perms in bind.execute(sa.select(roles.c.id, roles.c.permissions)).all():
        new = convert(perms)
        if new is not None:
            bind.execute(roles.update().where(roles.c.id == role_id).values(permissions=new))


def downgrade() -> None:
    # No-op. The old format granted full access regardless of what it said, so restoring it
    # would only widen access; the converted roles are valid on the previous revision too.
    pass
