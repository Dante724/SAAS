"""RBAC v2 — the permission catalog (grounded in the Orderry roles research).

Backward compatible: Role.permissions stays a flat {perm_key: bool} dict and
require_perm keeps default-allow semantics (deny only on explicit False). This
module adds a *structured catalog* for the UI (grouped, searchable) plus role
templates and a location-scope helper. No behaviour change for existing roles.
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
}

# All valid codes (flat), for validation.
ALL_CODES = {code for group in CATALOG.values() for code, _ in group}

# Seed templates (deny-lists per template; anything not listed = allowed).
TEMPLATES = {
    "Full access": {},                       # nothing denied
    "Manager": {"can_delete_transactions": False, "can_manage_roles": False},
    "Partner": {"can_manage_settings": False, "can_manage_roles": False,
                "can_manage_employees": False},
    "Specialist": {"can_manage_settings": False, "can_manage_roles": False,
                   "can_manage_employees": False, "can_view_purchase_price": False,
                   "can_see_profit": False, "can_delete_tickets": False,
                   "can_delete_invoices": False, "can_view_cashflow": False},
}


def role_allows(role, perm_key: str) -> bool:
    """Deny-wins check against a flat {code: bool} permissions dict. Default-allow
    for unknown/omitted keys (preserves legacy behaviour)."""
    perms = (role.permissions or {}) if role else {}
    return perms.get(perm_key) is not False


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
