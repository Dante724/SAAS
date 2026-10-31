// Settings registry — single source of truth for the Settings nav, the search
// index and the category grouping (build 144dy redesign). Kept separate from the
// 3,600-line SettingsHub so it is trivially unit-testable (see settingsRegistry.test.ts)
// and so the one data model drives nav + search without duplication.

import { FEATURES } from "../../lib/features";

export type SectionId =
  | "general" | "team" | "locations" | "statuses" | "checklists" | "forms"
  | "industry" | "pricing" | "finance" | "warehouses" | "directories"
  | "templates" | "feedback" | "public" | "notifications" | "integrations"
  | "ecommerce" | "sso" | "approvals" | "memberships";

const ALL_NAV: { id: SectionId; label: string; icon: string }[] = [
  { id: "general", label: "General", icon: "tune" },
  { id: "team", label: "Team & roles", icon: "group" },
  { id: "locations", label: "Locations", icon: "storefront" },
  { id: "statuses", label: "Statuses", icon: "flag" },
  { id: "checklists", label: "Checklists", icon: "fact_check" },
  { id: "forms", label: "Forms & fields", icon: "fact_check" },
  { id: "industry", label: "Industry & assets", icon: "smartphone" },
  { id: "pricing", label: "Pricing", icon: "attach_money" },
  { id: "finance", label: "Finance & numbering", icon: "account_balance" },
  { id: "memberships", label: "Memberships", icon: "workspace_premium" },
  { id: "warehouses", label: "Inventory & warehouses", icon: "warehouse" },
  { id: "directories", label: "Directories", icon: "description" },
  { id: "templates", label: "Document templates", icon: "description" },
  { id: "feedback", label: "Customer feedback", icon: "reviews" },
  { id: "public", label: "Public pages", icon: "open_in_new" },
  { id: "notifications", label: "Notifications", icon: "notifications" },
  { id: "integrations", label: "Integrations", icon: "link" },
  { id: "ecommerce", label: "E-commerce", icon: "shopping_cart" },
  { id: "sso", label: "Single sign-on", icon: "lock" },
  { id: "approvals", label: "Approvals", icon: "fact_check" },
];

// E-commerce stays hidden until its sync is real (see lib/features).
export const NAV = ALL_NAV.filter((n) => n.id !== "ecommerce" || FEATURES.ecommerce);

export const CATS: { id: string; label: string; icon: string }[] = [
  { id: "workspace", label: "Workspace", icon: "tune" },
  { id: "workorders", label: "Work orders", icon: "flag" },
  { id: "money", label: "Money", icon: "payments" },
  { id: "inventory", label: "Inventory", icon: "warehouse" },
  { id: "comms", label: "Customers & comms", icon: "notifications" },
  { id: "connections", label: "Connections", icon: "link" },
];

export const SEC_CAT: Record<SectionId, string> = {
  general: "workspace", team: "workspace", locations: "workspace", sso: "workspace",
  statuses: "workorders", checklists: "workorders", forms: "workorders", industry: "workorders", approvals: "workorders",
  pricing: "money", finance: "money", memberships: "money",
  warehouses: "inventory",
  public: "comms", notifications: "comms", templates: "comms", feedback: "comms", directories: "comms",
  integrations: "connections", ecommerce: "connections",
};

// keywords power search (so "invoice number", "reminder", "add tech" all land)
export const SEC_KW: Record<SectionId, string> = {
  general: "name timezone currency language region industry logo profile sense tekly sense suggestions smart margin learning",
  team: "staff employee invite role permission rbac mobile access payroll add tech person",
  locations: "branch address hours location shop",
  sso: "sso saml oauth login security 2fa session single sign on",
  statuses: "status pipeline stage workflow color triggers invoice notification",
  checklists: "checklist diagnostic qa inspection steps",
  forms: "form custom field intake order type",
  industry: "asset device template vin imei serial category industry",
  approvals: "approval discount refund threshold limit",
  pricing: "price rate labour labor tax vat discount price list",
  finance: "invoice number estimate numbering sequence prefix payment accounting quickbooks finance",
  warehouses: "inventory warehouse bin stock reorder moq supplier",
  public: "public online appointment booking page link branding slot window",
  notifications: "notification sms email reminder rule appointment status",
  templates: "template document estimate invoice receipt label print",
  feedback: "feedback review nps rating survey",
  memberships: "membership plan subscription member renewal recurring monthly yearly season grace lapse checkout offer",
  directories: "directory document export knowledge base files",
  integrations: "integration connect api webhook stripe quickbooks email",
  ecommerce: "ecommerce shop product sync online store shopify",
};

// Setup guide: each step is checked against real data (SetupChecklist loadSetupState), with a
// rough time to finish. `go` is the settings section to open, or an app route for actions.
export type SetupStep = { id: string; t: string; d: string; min: number; go: SectionId | `/${string}`; skippable?: boolean };
export const SETUP: SetupStep[] = [
  { id: "shop", t: "Add your shop details", d: "Name, phone and address for receipts and texts", min: 2, go: "general" },
  { id: "tax", t: "Set up sales tax", d: "So invoices charge the right tax", min: 2, go: "finance", skippable: true },
  { id: "team", t: "Invite your team", d: "Give each tech their own sign-in", min: 2, go: "team", skippable: true },
  { id: "payments", t: "Get paid online", d: "Card payments on invoices and the tracking page", min: 5, go: "integrations", skippable: true },
  { id: "texts", t: "Turn on text messages", d: "Customers get updates by text, not just email", min: 5, go: "integrations", skippable: true },
  { id: "booking", t: "Take bookings online", d: "Share a link customers can book from", min: 3, go: "public", skippable: true },
  { id: "first", t: "Check in your first repair", d: "Try the whole flow with a real job", min: 3, go: "/orders" },
];

/** Search the settings index by label + keywords. Empty query → no results. */
export function searchSections(query: string): typeof NAV {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return NAV.filter((n) => n.label.toLowerCase().includes(q) || (SEC_KW[n.id] || "").includes(q));
}
