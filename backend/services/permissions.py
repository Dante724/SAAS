"""RBAC v2 — the permission catalog (grounded in the Orderry roles research).

Role.permissions is a flat {perm_key: bool} dict. Roles are explicit allow-lists
(perms_explicit): a code is granted only when set to True, so new codes start off.
Legacy roles are snapshotted to an allow-list that grants exactly what they granted
before (see make_explicit), so existing roles don't change.
"""
import json

# module -> [(code, label), ...]. Codes are stable; labels are for the UI.
CATALOG = {
    "General": [
        ("can_make_client_payments", "Make payments due to client's balance"),
        ("can_view_purchase_price", "View the purchase price of products"),
        ("can_set_discount", "Set discount in documents"),
        ("can_set_discount_type", "Set the discount type"),
        ("can_set_cost_price", "Set the cost price in documents"),
        ("can_see_profit", "See estimated profit"),
        ("display_in_managers", "Display in the Managers list"),
        ("display_in_assignees", "Display in the Assignee list"),
        ("can_send_sms", "Send SMS"),
        ("can_see_asset_cost", "See an asset cost"),
        ("can_config_exceptional_commissions", "Configure exceptional commissions"),
        ("can_view_own_compensation", "View their own compensation"),
    ],
    "Workflows": [
        ("can_create_tickets", "Create tickets"),
        ("can_view_comeback_alerts", "See repeat-visit (comeback) alerts on work orders"),
        ("can_create_estimates", "Create estimates"),
        ("can_transfer_tickets", "Transfer tickets"),
        ("can_delete_tickets", "Delete tickets"),
        ("can_assign_staff", "Assign managers and technicians"),
        ("can_view_client_info", "View the client info"),
        ("can_view_asset_info", "View the asset info"),
        ("can_view_item_prices", "View prices of labors, services, products"),
        ("can_view_invoices_tab", "View the Invoices and payments tab"),
        ("can_edit_closed_ticket", "Edit the closed ticket"),
        ("can_manage_shared_filters", "Create, edit and delete shared filters"),
        ("can_add_custom_service", "Add custom services outside Catalog"),
        ("can_add_custom_product", "Add custom products outside Catalog"),
        ("can_add_services", "Add services to a job"),
        ("can_add_products", "Add products and parts to a job"),
        ("can_approve_discount", "Approve discounts above the shop's limit"),
        ("can_add_without_stock", "Add products without deduction"),
        ("can_create_payment_links", "Create payment links"),
    ],
    "Inventory": [
        ("can_manage_inventory", "Manage inventory (stock, POs, receiving)"),
        ("can_create_purchase_orders", "Create purchase orders"),
        ("can_reserve_stock", "Reserve stock for a job"),
        ("can_override_reservation", "Use stock reserved for another job (admin override)"),
        ("can_receive_inventory_alerts", "Receive low-stock & job-unblocked alerts"),
    ],
    "Catalog": [
        ("can_view_labors", "View labors, services, categories"),
        ("can_edit_labors", "Edit labors, services, categories"),
        ("can_delete_labors", "Delete labors, services, categories"),
        ("can_convert_product_to_asset", "Convert a serialized product into an asset"),
        ("can_convert_asset_to_product", "Convert an asset into a catalog product"),
        ("can_create_products", "Create new products"),
        ("can_edit_products", "Edit products"),
        ("can_delete_products", "Delete products"),
        ("can_track_serials", "Enable serial tracking for existing products"),
        ("can_manage_bundles", "Create/edit/delete bundles"),
    ],
    "Sales": [
        ("can_create_sales", "Create sales"),
        ("can_delete_sales", "Delete sales"),
        ("can_sell_services", "Sell services"),
        ("can_edit_sale_price", "Edit price"),
        ("can_export_sales", "Export the sales"),
    ],
    "Invoices": [
        ("can_view_invoices", "View invoices"),
        ("can_create_invoices", "Create invoices"),
        ("can_edit_invoices", "Edit invoices"),
        ("can_delete_invoices", "Delete invoices"),
        ("can_edit_issued_invoices", "Edit issued invoices"),
    ],
    "Finance": [
        ("can_create_incomes", "Create incomes"),
        ("can_transfer_money", "Transfer money"),
        ("can_create_expenses", "Create expenses"),
        ("can_retroactive_cash", "Create cash entries retroactively"),
        ("can_view_cashflow", "View the cash flow"),
        ("can_view_cashflow_any_period", "View the cash flow for any period"),
        ("can_view_cashbox", "View the cash drawer / cashboxes"),
        ("can_export_accountant", "Export the accountant pack (CSV)"),
        ("can_correct_transactions", "Correct account transactions"),
        ("can_delete_transactions", "Delete account transactions"),
        ("can_manage_accounts", "Create, edit and delete accounts"),
        ("can_view_balance", "See balance"),
        ("can_create_returns", "Create returns"),
        ("can_delete_refunds", "Delete refunds"),
    ],
    "Payroll": [
        ("can_view_payroll", "View payroll calculation"),
        ("can_accrue_payroll", "Accrue payroll"),
        ("can_create_penalties", "Create penalties"),
        ("can_create_bonuses", "Create bonuses"),
        ("can_export_payroll", "Export the payroll"),
    ],
    "Settings": [
        ("can_manage_settings", "Manage company settings"),
        ("can_manage_roles", "Manage roles and permissions"),
        ("can_manage_employees", "Manage employees"),
    ],
    "Checklists": [
        ("can_manage_checklist_templates", "Manage checklist templates"),
        ("can_create_checklist", "Add checklists to work orders"),
        ("can_edit_checklist", "Fill in / edit checklists"),
        ("can_delete_checklist", "Delete checklists from work orders"),
    ],
    # Security phase 2: codes the API already enforced but the role editor couldn't set
    # (rbac._validate rejected them), plus reports/client-data codes.
    "Reports & data": [
        ("can_view_reports", "Run and export reports (incl. custom reports)"),
        ("can_export_clients", "Export the client list"),
        ("can_delete_clients", "Delete and merge clients"),
    ],
    "Administration": [
        ("can_view_finance", "Use finance: invoices, payments, cash shifts"),
        ("can_manage_payroll", "Manage payroll runs and schedules"),
        ("can_manage_staff", "Manage staff schedules and resources"),
        ("can_delete_orders", "Delete and archive work orders"),
        ("can_recommend", "Create recommendations on work orders"),
        ("can_publish_recommend", "Send recommendations to clients"),
        ("can_approve_rec_for_customer", "Approve recommendations on the customer's behalf (by phone)"),
        ("can_reply_customer", "Reply to customers from the inbox"),
    ],
}

# All valid codes (flat), for validation.
ALL_CODES = {code for group in CATALOG.values() for code, _ in group}

# Every catalogue code is enforced somewhere; tests/test_permission_catalog.py keeps it that way.

# Seed templates (deny-lists per template; anything not listed = allowed).
# Money, payroll, exports and destructive deletes are off for anyone who isn't running the
# shop: a technician or outside partner must not pay themselves, move money or bulk-export
# customers just because a newer code defaulted to "on".
_NOT_FOR_STAFF = {
    "can_view_finance": False, "can_view_cashflow": False, "can_view_cashflow_any_period": False,
    "can_transfer_money": False, "can_delete_transactions": False, "can_correct_transactions": False,
    "can_retroactive_cash": False, "can_manage_accounts": False,
    "can_view_payroll": False, "can_manage_payroll": False, "can_accrue_payroll": False,
    "can_export_payroll": False, "can_create_bonuses": False, "can_create_penalties": False,
    "can_config_exceptional_commissions": False,
    "can_view_reports": False, "can_export_accountant": False, "can_export_clients": False,
    "can_export_sales": False,
    "can_delete_clients": False, "can_delete_orders": False, "can_delete_sales": False,
    "can_delete_refunds": False, "can_delete_products": False, "can_delete_invoices": False,
    "can_delete_tickets": False,
    "can_view_purchase_price": False, "can_see_profit": False, "can_set_cost_price": False,
    "can_see_asset_cost": False,
}
TEMPLATES = {
    "Full access": {},                       # nothing denied
    "Manager": {"can_delete_transactions": False, "can_manage_roles": False},
    "Partner": {"can_manage_settings": False, "can_manage_roles": False,
                "can_manage_employees": False, **_NOT_FOR_STAFF},
    "Specialist": {"can_manage_settings": False, "can_manage_roles": False,
                   "can_manage_employees": False, **_NOT_FOR_STAFF},
}


# Codes that were always opt-in (only an explicit True grants them), even for legacy roles.
OPT_IN = {"can_override_reservation"}


def role_allows(role, perm_key: str) -> bool:
    """The one permission check. No role (the owner) = everything.

    Explicit roles (perms_explicit) are default-deny: only a code set to True is granted, so
    a permission added later stays off until someone turns it on. Legacy roles keep the old
    deny-wins rule (granted unless set to False) until they're snapshotted."""
    if not role:
        return True
    perms = role.permissions or {}
    if not getattr(role, "perms_explicit", False) and perms.get("all") == "write":
        return True               # the shop creator's legacy "admin" role: everything
    if getattr(role, "perms_explicit", False) or perm_key in OPT_IN:
        return perms.get(perm_key) is True
    return perms.get(perm_key) is not False


def effective_permissions(role) -> dict:
    """{code: bool} for every catalogue code, as role_allows sees it."""
    return {code: role_allows(role, code) for code in sorted(ALL_CODES)}


def explicit_permissions(role) -> dict:
    """Snapshot a role into an allow-list that grants exactly what it grants today."""
    return effective_permissions(role)


def full_access_permissions() -> dict:
    """Every code granted, opt-in ones included (the shop creator's admin role)."""
    return {code: True for code in ALL_CODES}


def make_explicit(role) -> bool:
    """Convert a legacy role in place (idempotent). Returns True if it changed."""
    if getattr(role, "perms_explicit", False):
        return False
    role.permissions = explicit_permissions(role)
    role.perms_explicit = True
    return True


def new_role_permissions(template: str | None = None, overrides: dict | None = None) -> dict:
    """Permissions for a role created now: every current code on (except the opt-in ones),
    then the template's denies, then what the editor set."""
    perms = {code: code not in OPT_IN for code in ALL_CODES}
    perms.update(TEMPLATES.get(template or "", {}))
    perms.update({k: bool(v) for k, v in (overrides or {}).items()})
    return perms


def in_location_scope(role, location_id) -> bool:
    """True if a role may act at a location. 'all' scope or no location => allowed."""
    if not role or getattr(role, "location_scope", "all") != "selected":
        return True
    if not location_id:
        return False
    try:
        return location_id in json.loads(role.location_ids or "[]")
    except Exception:
        return False
