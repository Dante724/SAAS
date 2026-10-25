import { useEffect, useRef, useState } from "react";
import DeviceList from "../../components/DeviceList";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api } from "../../api/client";
import TemplateEditorDrawer from "./TemplateEditorDrawer";
import DirectoriesManager from "./DirectoriesManager";
import FormCustomizer, { type FormKey } from "../../components/FormCustomizer";
import { resetTerminology } from "../../lib/useTerminology";
import { confirmDialog, alertDialog, promptDialog } from "../../lib/dialog";
import { useMe } from "../../context/Me";
import { NAV, CATS, SEC_CAT, searchSections, type SectionId } from "./settingsRegistry";
import { SetupChecklist } from "./SetupChecklist";
import { StatusList, StatusEditor } from "./StatusesSettings";
import GeneralSettings from "./GeneralSettings";
import CustomerMessages from "./NotificationsPage";
import PublicPagesSettings from "./PublicPagesSettings";
import { PricingPage, InventoryPage } from "./PricingInventory";
import FinanceNumbering from "./FinanceNumbering";
import InvoiceSettings from "./InvoiceSettings";
import DocumentTemplatesPage from "./DocumentTemplatesPage";
import LocationsPage from "./LocationsPage";
import { TeamPage, RoleEditor, Avatar } from "./TeamSettings";
import { ChecklistList, ChecklistEditor } from "./ChecklistSettings";
import CardReadersCard from "./CardReadersCard";
import { escapeHtml, sanitizeHtml } from "../../lib/safeHtml";
import TelephonySettings from "./TelephonySettings";
import ApiKeysCard from "./ApiKeysCard";


// Full section set = the registry sections + a few that have no nav entry of their own.
type Section = SectionId | "overview" | "automation" | "payroll-rules";

const lbl = "block text-[11px] font-bold text-ink-tertiary  mb-1";
const field = "glass-input w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg";

// General-settings option lists (legacy parity).
// `country` is stored as an ISO 3166-1 alpha-2 code (String(2) column). Each country
// carries its default timezone + currency; picking a country auto-fills both — timezone
// and currency stay editable afterwards. [iso2, name, timezone, currency_code, symbol]
const COUNTRY_META: [string, string, string, string, string][] = [
  ["IN", "India", "Asia/Kolkata", "INR", "₹"], ["US", "United States", "America/New_York", "USD", "$"],
  ["GB", "United Kingdom", "Europe/London", "GBP", "£"], ["CA", "Canada", "America/Toronto", "CAD", "$"],
  ["AU", "Australia", "Australia/Sydney", "AUD", "$"], ["NZ", "New Zealand", "Pacific/Auckland", "NZD", "$"],
  ["IE", "Ireland", "Europe/Dublin", "EUR", "€"], ["AE", "United Arab Emirates", "Asia/Dubai", "AED", "د.إ"],
  ["SA", "Saudi Arabia", "Asia/Riyadh", "SAR", "﷼"], ["QA", "Qatar", "Asia/Qatar", "QAR", "﷼"],
  ["KW", "Kuwait", "Asia/Kuwait", "KWD", "د.ك"], ["BH", "Bahrain", "Asia/Bahrain", "BHD", ".د.ب"],
  ["OM", "Oman", "Asia/Muscat", "OMR", "﷼"], ["SG", "Singapore", "Asia/Singapore", "SGD", "$"],
  ["MY", "Malaysia", "Asia/Kuala_Lumpur", "MYR", "RM"], ["ID", "Indonesia", "Asia/Jakarta", "IDR", "Rp"],
  ["PH", "Philippines", "Asia/Manila", "PHP", "₱"], ["TH", "Thailand", "Asia/Bangkok", "THB", "฿"],
  ["VN", "Vietnam", "Asia/Ho_Chi_Minh", "VND", "₫"], ["JP", "Japan", "Asia/Tokyo", "JPY", "¥"],
  ["KR", "South Korea", "Asia/Seoul", "KRW", "₩"], ["CN", "China", "Asia/Shanghai", "CNY", "¥"],
  ["HK", "Hong Kong", "Asia/Hong_Kong", "HKD", "$"], ["TW", "Taiwan", "Asia/Taipei", "TWD", "NT$"],
  ["PK", "Pakistan", "Asia/Karachi", "PKR", "₨"], ["BD", "Bangladesh", "Asia/Dhaka", "BDT", "৳"],
  ["LK", "Sri Lanka", "Asia/Colombo", "LKR", "₨"], ["NP", "Nepal", "Asia/Kathmandu", "NPR", "₨"],
  ["DE", "Germany", "Europe/Berlin", "EUR", "€"], ["FR", "France", "Europe/Paris", "EUR", "€"],
  ["ES", "Spain", "Europe/Madrid", "EUR", "€"], ["IT", "Italy", "Europe/Rome", "EUR", "€"],
  ["PT", "Portugal", "Europe/Lisbon", "EUR", "€"], ["NL", "Netherlands", "Europe/Amsterdam", "EUR", "€"],
  ["BE", "Belgium", "Europe/Brussels", "EUR", "€"], ["CH", "Switzerland", "Europe/Zurich", "CHF", "CHF"],
  ["AT", "Austria", "Europe/Vienna", "EUR", "€"], ["SE", "Sweden", "Europe/Stockholm", "SEK", "kr"],
  ["NO", "Norway", "Europe/Oslo", "NOK", "kr"], ["DK", "Denmark", "Europe/Copenhagen", "DKK", "kr"],
  ["FI", "Finland", "Europe/Helsinki", "EUR", "€"], ["PL", "Poland", "Europe/Warsaw", "PLN", "zł"],
  ["CZ", "Czechia", "Europe/Prague", "CZK", "Kč"], ["RO", "Romania", "Europe/Bucharest", "RON", "lei"],
  ["GR", "Greece", "Europe/Athens", "EUR", "€"], ["TR", "Turkey", "Europe/Istanbul", "TRY", "₺"],
  ["RU", "Russia", "Europe/Moscow", "RUB", "₽"], ["UA", "Ukraine", "Europe/Kyiv", "UAH", "₴"],
  ["ZA", "South Africa", "Africa/Johannesburg", "ZAR", "R"], ["NG", "Nigeria", "Africa/Lagos", "NGN", "₦"],
  ["KE", "Kenya", "Africa/Nairobi", "KES", "KSh"], ["EG", "Egypt", "Africa/Cairo", "EGP", "£"],
  ["MA", "Morocco", "Africa/Casablanca", "MAD", "د.م."], ["GH", "Ghana", "Africa/Accra", "GHS", "₵"],
  ["BR", "Brazil", "America/Sao_Paulo", "BRL", "R$"], ["MX", "Mexico", "America/Mexico_City", "MXN", "$"],
  ["AR", "Argentina", "America/Argentina/Buenos_Aires", "ARS", "$"], ["CL", "Chile", "America/Santiago", "CLP", "$"],
  ["CO", "Colombia", "America/Bogota", "COP", "$"], ["PE", "Peru", "America/Lima", "PEN", "S/"],
];
const TIMEZONES = Array.from(new Set([...COUNTRY_META.map((c) => c[2]),
  "America/Los_Angeles", "America/Chicago", "America/Denver", "Asia/Kolkata", "UTC"])).sort();
// De-duped currency picker, sorted by code. [code, symbol]
const CURRENCIES: [string, string][] = (() => {
  const m = new Map<string, string>();
  COUNTRY_META.forEach((c) => { if (!m.has(c[3])) m.set(c[3], c[4]); });
  return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]));
})();
const DOC_LANGS: [string, string][] = [["en", "English"], ["hi", "हिन्दी (Hindi)"], ["es", "Español"],
  ["fr", "Français"], ["de", "Deutsch"], ["pt", "Português"], ["ar", "العربية (Arabic)"], ["ru", "Русский"],
  ["zh", "中文 (Chinese)"], ["ja", "日本語 (Japanese)"]];
// Payroll commission events — friendly labels for the rule editor (backend enum in payroll.py).
const PAYROLL_EVENT_LABELS: Record<string, string> = {
  order_created: "Order created", order_closed: "Order closed (any outcome)",
  order_closed_success: "Order closed — success", order_closed_fail: "Order closed — declined / fail",
  labor_performed: "Labour performed", product_sold: "Product sold",
  assignee_labor: "Assignee's labour", assignee_product: "Assignee's product sale",
  task_closed: "Task closed",
};
const PAYROLL_EVENTS = Object.keys(PAYROLL_EVENT_LABELS);
const PAYROLL_TIMINGS: [string, string][] = [["on_close", "When the order closes"], ["immediately", "Immediately on the event"], ["on_status", "When it reaches a status"]];
const PAYROLL_TERMS: [string, string][] = [["any", "Any"], ["on_time", "Closed on time"], ["overdue", "Closed overdue"], ["urgent", "Urgent orders"]];
const PAYROLL_LEVELS: [string, string][] = [["generic", "All items (generic)"], ["product", "Specific product"], ["category", "A category"], ["service", "Specific service"], ["profile", "Employee profile default"]];

// Landing pages a role can start on (route → label). "" = default Home.
const INDUSTRIES: [string, string][] = [["general", "Generic — Assets"], ["auto", "Auto repair — Vehicles (VIN)"],
  ["powersports", "Powersports — Vehicles (VIN)"], ["marine", "Marine — Vessels (HIN)"],
  ["mobile_phone", "Phone repair — Devices (IMEI/Serial)"], ["computer", "Computer repair — Devices (Serial)"],
  ["small_engine", "Small engine — Equipment (Serial)"], ["tools", "Tool repair — Tools (Serial)"],
  ["bike", "Bike / cycle — Bikes (Serial)"]];

export default function SettingsHub() {
  const navigate = useNavigate();
  // ?section=… deep-links a section (e.g. "Manage options" in a directory dropdown).
  const [sec, setSec] = useState<Section>(() => (new URLSearchParams(window.location.search).get("section") as Section) || "overview");
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [co, setCo] = useState<any>(null);
  const [statuses, setStatuses] = useState<any[]>([]);
  const [statusRT, setStatusRT] = useState<string>("work_order");  // 144ij — which pipeline is being edited
  async function moveStatus(from: number, to: number) {
    if (from === to || to < 0 || to >= statuses.length) return;
    const prev = statuses;
    const next = [...statuses]; const [it] = next.splice(from, 1); next.splice(to, 0, it);
    setStatuses(next);
    try { await api.post("/statuses/reorder", { ordered_ids: next.map((x: any) => x.id) }); flash("Order saved"); }
    catch (e: any) { setStatuses(prev); flash(`Couldn't save the order: ${e.message}`); }
  }
  const [lists, setLists] = useState<Record<string, any[] | null>>({});
  const [roles, setRoles] = useState<any[] | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2200); };

  const coSaved = useRef<any>(null);   // last saved company settings (General shows a save bar when they differ)
  useEffect(() => { api.get<any>("/staff/company").then((c) => { coSaved.current = c; setCo(c); }).catch(() => setCo({})); }, []);
  useEffect(() => {
    if (sec === "statuses" || sec === "team") {
      const rt = sec === "statuses" ? statusRT : "work_order";
      api.get<any>(`/statuses?record_type=${rt}`).then((r) => setStatuses(r.items || r || [])).catch(() => {});
    }
  }, [sec, statusRT]);

  // Live data for the other configuration sections (read views).
  const SEC_URL: Record<string, string> = {
    team: "/staff/employees", warehouses: "/settings/warehouses",
    automation: "/automations", notifications: "/notifications/rules",
    templates: "/documents/templates", finance: "/settings/numbering",
    forms: "/forms/types", pricing: "/pricing/types", industry: "/settings/asset-groups",
    checklists: "/checklist-templates", directories: "/directories", "payroll-rules": "/payroll/rules",
    public: "/staff/public-pages",
  };
  const [integrations, setIntegrations] = useState<any>(null);
  const [notifTemplates, setNotifTemplates] = useState<any[] | null>(null);
  const [invites, setInvites] = useState<any[] | null>(null);
  const [rbacRoles, setRbacRoles] = useState<any[] | null>(null);
  const [rbacCatalog, setRbacCatalog] = useState<Record<string, { code: string; label: string }[]> | null>(null);
  const [roleEdit, setRoleEdit] = useState<any | "new" | null>(null);
  const [empEdit, setEmpEdit] = useState<any | "new" | null>(null);
  const [payrollEdit, setPayrollEdit] = useState<any | "new" | null>(null);
  const [statusEdit, setStatusEdit] = useState<any | "new" | null>(null);
  const reloadTeam = () => api.get<any>("/staff/employees").then((r) => { const d = r?.items ?? r; setLists((s) => ({ ...s, team: Array.isArray(d) ? d : [] })); }).catch(() => {});
  const reloadPayrollRules = () => api.get<any>("/payroll/rules").then((r) => { const d = r?.items ?? r; setLists((s) => ({ ...s, "payroll-rules": Array.isArray(d) ? d : [] })); }).catch(() => {});
  const [formFieldsFor, setFormFieldsFor] = useState<any | null>(null);
  const [checklistEdit, setChecklistEdit] = useState<any | null | undefined>(undefined);  // undefined=closed, null=new, obj=edit
  const reloadRbac = () => api.get<any>("/settings/rbac/roles").then((r) => setRbacRoles(r.items || r || [])).catch(() => setRbacRoles([]));
  const reloadNotifTemplates = () => api.get<any>("/notifications/templates").then((r) => setNotifTemplates(r.items || r || [])).catch(() => setNotifTemplates([]));
  const reloadInvites = () => api.get<any>("/staff/invites").then((r) => setInvites(r.items || r || [])).catch(() => setInvites([]));
  useEffect(() => {
    const url = SEC_URL[sec];
    if (url && lists[sec] === undefined) {
      api.get<any>(url).then((r) => { const d = r?.items ?? r; const arr = Array.isArray(d) ? d : (d ? [d] : []); setLists((s) => ({ ...s, [sec]: arr })); }).catch(() => setLists((s) => ({ ...s, [sec]: [] })));
    }
    if (sec === "team" && roles === null) {
      api.get<any>("/staff/roles").then((r) => setRoles(r.items || r || [])).catch(() => setRoles([]));
    }
    if (sec === "team" && invites === null) reloadInvites();
    if (sec === "team" && rbacRoles === null) reloadRbac();
    if (sec === "team" && rbacCatalog === null) api.get<any>("/settings/rbac/catalog").then((r) => setRbacCatalog(r.catalog || {})).catch(() => setRbacCatalog({}));
    if (sec === "team" && lists["payroll-rules"] === undefined) api.get<any>("/payroll/rules").then((r) => { const d = r?.items ?? r; setLists((s) => ({ ...s, "payroll-rules": Array.isArray(d) ? d : (d ? [d] : []) })); }).catch(() => setLists((s) => ({ ...s, "payroll-rules": [] })));
    if (sec === "notifications" && notifTemplates === null) reloadNotifTemplates();
    // 144jb: Automations now live inside the Notifications hub, so load them here too.
    if (sec === "notifications" && lists.automation === undefined) {
      api.get<any>("/automations").then((r) => { const d = r?.items ?? r; setLists((s) => ({ ...s, automation: Array.isArray(d) ? d : [] })); }).catch(() => setLists((s) => ({ ...s, automation: [] })));
    }
    if (sec === "integrations" && integrations === null) {
      Promise.allSettled([
        api.get<any>("/api-keys"), api.get<any>("/integrations/email/status"), api.get<any>("/integrations/qbo/status"),
        api.get<any>("/integrations/xero/status"), api.get<any>("/payments/config"), api.get<any>("/telephony/config"),
        api.get<any>("/ecommerce/integrations"), api.get<any>("/integrations/credentials"),
      ]).then(([k, e, q, x, p, t, ec, cr]) => setIntegrations({
        keys: k.status === "fulfilled" ? ((k.value as any).items || k.value || []) : [],
        email: e.status === "fulfilled" ? e.value : null,
        qbo: q.status === "fulfilled" ? q.value : null,
        xero: x.status === "fulfilled" ? x.value : null,
        pay: p.status === "fulfilled" ? p.value : null,
        tel: t.status === "fulfilled" ? t.value : null,
        ecom: ec.status === "fulfilled" ? ((ec.value as any).items || []) : [],
        creds: cr.status === "fulfilled" ? cr.value : {},
      }));
    }
  }, [sec]);

  // ── Inline editors for config entities (create / edit / delete) ──
  type Field = { k: string; label: string; type: "text" | "number" | "select" | "color" | "checkbox" | "textarea" | "date"; opts?: string[]; def?: any; req?: boolean; vars?: boolean; counter?: boolean };
  const EDITORS: Record<string, { create: string; upd?: (id: string) => string; updMethod?: "put" | "patch"; del?: (id: string) => string; fields: Field[]; reload: () => void }> = {
    automation: { create: "/automations", upd: (id) => `/automations/${id}`, del: (id) => `/automations/${id}`, reload: () => reloadSec("automation"), fields: [
      { k: "name", label: "Rule name", type: "text", req: true }, { k: "event", label: "Event", type: "select", opts: ["status_change", "order_created", "order_closed", "payment_received", "appointment_created"], def: "status_change" },
      { k: "trigger_status", label: "When status is (key)", type: "text" }, { k: "action_type", label: "Action", type: "select", opts: ["notify", "create_task", "assign", "webhook", "sms"], def: "notify", req: true }, { k: "active", label: "Active", type: "checkbox", def: true } ] },
    notifications: { create: "/notifications/rules", upd: (id) => `/notifications/rules/${id}`, updMethod: "patch", del: (id) => `/notifications/rules/${id}`, reload: () => reloadSec("notifications"), fields: [
      { k: "module", label: "Module", type: "select", opts: ["order", "client", "lead", "appointment", "invoice"], def: "order" }, { k: "event", label: "Event", type: "select", opts: ["status_change", "created", "closed", "payment_received", "reminder"], def: "status_change" },
      { k: "channel", label: "Channel", type: "select", opts: ["email", "sms", "push", "chat"], def: "email" }, { k: "delay_min", label: "Delay (minutes)", type: "number", def: 0 }, { k: "working_hours_only", label: "Working hours only", type: "checkbox" }, { k: "active", label: "Active", type: "checkbox", def: true } ] },
    templates: { create: "/documents/templates", upd: (id) => `/documents/templates/${id}`, del: (id) => `/documents/templates/${id}`, reload: () => reloadSec("templates"), fields: [
      { k: "name", label: "Template name", type: "text", req: true }, { k: "doc_type", label: "Document type", type: "select", opts: ["estimate", "invoice", "receipt", "work_order", "label", "other"], def: "estimate" },
      { k: "page_size", label: "Page size", type: "select", opts: ["a4", "letter", "label"], def: "a4" }, { k: "font_family", label: "Font", type: "text", def: "Inter" },
      { k: "is_default", label: "Default for type", type: "checkbox" }, { k: "body_html", label: "Body (HTML — use {Variables})", type: "textarea", req: true, vars: true } ] },
    notif_templates: { create: "/notifications/templates", upd: (id) => `/notifications/templates/${id}`, del: (id) => `/notifications/templates/${id}`, reload: () => reloadNotifTemplates(), fields: [
      { k: "name", label: "Template name", type: "text" }, { k: "channel", label: "Channel", type: "select", opts: ["sms", "email", "push"], def: "sms" },
      { k: "trigger_event", label: "Trigger event", type: "select", opts: ["status_change", "created", "closed", "reminder"], def: "status_change" }, { k: "trigger_status", label: "When status (key)", type: "text" },
      { k: "subject", label: "Subject (email)", type: "text" }, { k: "body_template", label: "Message body", type: "textarea", req: true, vars: true, counter: true }, { k: "active", label: "Active", type: "checkbox", def: true } ] },
    invites: { create: "/staff/invites", del: (id) => `/staff/invites/${id}`, reload: () => reloadInvites(), fields: [
      { k: "email", label: "Email", type: "text", req: true }, { k: "name", label: "Name", type: "text" } ] },
    forms: { create: "/forms/types", upd: (id) => `/forms/types/${id}`, del: (id) => `/forms/types/${id}`, reload: () => reloadSec("forms"), fields: [
      { k: "name", label: "Form name", type: "text", req: true }, { k: "color", label: "Colour", type: "color", def: "#4f8ef7" }, { k: "initial_status", label: "Initial status (key)", type: "text", def: "new" }, { k: "is_default", label: "Default", type: "checkbox" } ] },
    warehouses: { create: "/settings/warehouses", upd: (id) => `/settings/warehouses/${id}`, del: (id) => `/settings/warehouses/${id}`, reload: () => reloadSec("warehouses"), fields: [
      { k: "name", label: "Name", type: "text", req: true }, { k: "kind", label: "Kind", type: "select", opts: ["product", "asset"], def: "product" },
      { k: "scope", label: "Scope", type: "select", opts: ["location", "company"], def: "location" }, { k: "active", label: "Active", type: "checkbox", def: true } ] },
    statuses: { create: "/statuses", upd: (id) => `/statuses/${id}`, del: (id) => `/statuses/${id}`, reload: () => reloadSec("statuses"), fields: [
      { k: "name", label: "Name", type: "text", req: true }, { k: "status_group", label: "Group", type: "select", opts: ["new", "in_progress", "pending", "ready", "closed", "dropped"], def: "in_progress" },
      { k: "color", label: "Colour", type: "color", def: "#888888" }, { k: "is_default", label: "Default status", type: "checkbox" },
      { k: "triggers_invoice", label: "Triggers invoice", type: "checkbox" }, { k: "triggers_payment", label: "Prompts payment", type: "checkbox" }, { k: "locks_edits", label: "Locks edits (freeze after this status)", type: "checkbox" },
      { k: "time_limit_min", label: "Time limit (minutes, blank = none)", type: "number" }, { k: "moves_asset_to", label: "Auto-move asset to (warehouse/status key)", type: "text" },
      { k: "require_comment", label: "Require a comment before entering", type: "checkbox" }, { k: "require_checklist", label: "Require all checklist items done", type: "checkbox" }, { k: "require_payment", label: "Require full payment before entering", type: "checkbox" } ] },
    pricing: { create: "/pricing/types", upd: (id) => `/pricing/types/${id}`, del: (id) => `/pricing/types/${id}`, reload: () => reloadSec("pricing"), fields: [
      { k: "name", label: "Name", type: "text", req: true }, { k: "markup_pct", label: "Markup %", type: "number", def: 0 },
      { k: "is_default", label: "Default", type: "checkbox" }, { k: "location_scope", label: "Scope", type: "select", opts: ["all", "selected"], def: "all" },
      { k: "starts_at", label: "Promo starts (optional)", type: "date" }, { k: "ends_at", label: "Promo ends (optional)", type: "date" } ] },
    directories: { create: "/directories", del: (id) => `/directories/${id}`, reload: () => reloadSec("directories"), fields: [
      { k: "key", label: "Key", type: "text", req: true }, { k: "name", label: "Name", type: "text", req: true } ] },
  };
  const [editing, setEditing] = useState<{ sec: string; row: any | null; module?: string } | null>(null);
  async function reloadSec(s: string) {
    if (s === "statuses") { const r = await api.get<any>(`/statuses?record_type=${statusRT}`).catch(() => null); if (r) setStatuses(r.items || r || []); return; }
    const url = SEC_URL[s]; if (!url) return;
    const r = await api.get<any>(url).catch(() => null); const d = r?.items ?? r; setLists((x) => ({ ...x, [s]: Array.isArray(d) ? d : (d ? [d] : []) }));
  }
  async function delRow(s: string, row: any) {
    const cfg = EDITORS[s]; if (!cfg?.del || !row?.id) return;
    if (!(await confirmDialog(`Delete "${row.name || row.title || row.key || "this item"}"?`, { danger: true, confirmLabel: "Delete" }))) return;
    try { await api.del(cfg.del(row.id)); flash("Deleted"); cfg.reload(); } catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  async function saveRow(v: any) {
    if (!editing) return; const cfg = EDITORS[editing.sec]; const id = editing.row?.id;
    if (id && cfg.upd) { if (cfg.updMethod === "patch") await api.patch(cfg.upd(id), v); else await api.put(cfg.upd(id), v); } else await api.post(cfg.create, v);
    flash("Saved"); setEditing(null); cfg.reload();
  }

  async function saveCompany() {
    try {
      await api.put("/staff/company", {
        name: co.name, company_email: co.company_email, company_phone: co.company_phone, company_address: co.company_address,
        company_details: co.company_details, company_note: co.company_note, logo_data: co.logo_data,
        website: co.website, country: co.country, timezone: co.timezone,
        currency: co.currency, currency_code: co.currency_code, document_language: co.document_language,
        industry_template: co.industry_template,
        prohibit_dup_phone: co.prohibit_dup_phone, prohibit_dup_email: co.prohibit_dup_email,
        require_mfa: co.require_mfa, clock_wo_enabled: co.clock_wo_enabled, clock_attendance_enabled: co.clock_attendance_enabled,
        clock_labour_billing: co.clock_labour_billing, rounding_enabled: co.rounding_enabled,
      });
      // Switching industry also seeds its starter brands/models and refreshes the app's wording.
      const prevInd = coSaved.current?.industry_template;
      if (co.industry_template && co.industry_template !== prevInd) {
        await api.put("/settings/industry-template", { template_key: co.industry_template });
        resetTerminology();
      }
      flash("Settings saved");
      return true;
    } catch (e: any) { flash(`Failed: ${e.message}`); return false; }
  }
  const [rebuilding, setRebuilding] = useState(false);
  async function rebuildBalances() {
    setRebuilding(true);
    try { const r = await api.post<any>("/staff/ledger/rebuild", {}); flash(`Balances rebuilt${r?.clients != null ? ` · ${r.clients} clients` : ""}`); }
    catch (e: any) { flash(`Failed: ${e.message}`); } finally { setRebuilding(false); }
  }



  return (
    <Shell title="Settings">
      <div className="flex-1 overflow-hidden flex">
        {/* section nav */}
        <aside className="hidden md:block w-60 flex-shrink-0 border-r border-slate-200/80 bg-white/50 p-3 overflow-y-auto">
          <div className="relative mb-3">
            <M n="search" c="absolute left-2.5 top-2 text-base text-ink-tertiary pointer-events-none" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search settings…"
              className="w-full bg-slate-50 border border-slate-200 rounded-lg pl-8 pr-2 py-2 text-xs font-medium text-ink-primary outline-none focus:border-primary" />
          </div>
          <button onClick={() => { setSec("overview"); setQ(""); }} className={`w-full text-left px-2.5 py-2 rounded-lg text-xs font-semibold flex items-center gap-2.5 mb-2 ${sec === "overview" && !q ? "bg-blue-50/80 text-primary" : "text-ink-secondary hover:bg-slate-100"}`}><M n="grid_view" c="text-base" />Overview</button>
          {q.trim() ? (() => {
            const hits = searchSections(q);
            if (!hits.length) return <div className="text-xs text-ink-tertiary px-2.5 py-3">No settings match “{q}”.</div>;
            return hits.map((n) => (
              <button key={n.id} onClick={() => { setSec(n.id); setQ(""); }} className="w-full text-left px-2.5 py-2 rounded-lg text-xs font-semibold flex items-center gap-2.5 mb-0.5 text-ink-secondary hover:bg-slate-100"><M n={n.icon} c="text-base" />{n.label}<span className="ml-auto text-[11px] text-ink-tertiary font-medium">{CATS.find((c) => c.id === SEC_CAT[n.id])?.label}</span></button>
            ));
          })() : CATS.map((cat) => {
            const items = NAV.filter((n) => SEC_CAT[n.id] === cat.id);
            if (!items.length) return null;
            const col = !!collapsed[cat.id];
            return (
              <div key={cat.id} className="mb-1">
                <button onClick={() => setCollapsed((s) => ({ ...s, [cat.id]: !s[cat.id] }))} className="w-full flex items-center gap-1.5 px-2 py-1.5 text-[11px] font-bold text-ink-tertiary hover:text-ink-secondary">
                  {cat.label}<M n="expand_more" c={`text-sm ml-auto transition-transform ${col ? "-rotate-90" : ""}`} />
                </button>
                {!col && items.map((n) => (
                  <button key={n.id} onClick={() => setSec(n.id)} className={`w-full text-left px-2.5 py-2 rounded-lg text-xs font-semibold flex items-center gap-2.5 mb-0.5 ${sec === n.id ? "bg-blue-50/80 text-primary" : "text-ink-secondary hover:bg-slate-100"}`}><M n={n.icon} c="text-base" />{n.label}</button>
                ))}
              </div>
            );
          })}
        </aside>

        {/* pb-80: scroll room below the last card so dropdowns near the bottom
            (e.g. the Public-pages status pickers) have space to open downward. */}
        <div className="flex-1 min-w-0 overflow-y-auto p-6 pb-80">
          {/* Narrow screens hide the section list; the Overview's category cards are the menu. */}
          {sec !== "overview" && (
            <button onClick={() => setSec("overview")} className="md:hidden mb-3 -ml-1 flex items-center gap-1.5 px-1 py-2 text-xs font-semibold text-primary"><M n="arrow_back" c="text-sm" />All settings</button>
          )}
          {sec === "overview" && (
            <div className="max-w-3xl space-y-5">
              <div>
                <h2 className="text-xl font-semibold text-ink-primary">Settings</h2>
                <p className="text-xs text-ink-tertiary mt-0.5">Set up your shop once, change anything later.</p>
              </div>
              <SetupChecklist goSection={setSec} navigate={(to) => navigate(to)} />
              <div className="relative">
                <M n="search" c="absolute left-4 top-3.5 text-lg text-ink-tertiary pointer-events-none" />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a setting: tax, invoice number, reminder, technician…" aria-label="Find a setting"
                  className="w-full bg-white border border-slate-200 rounded-2xl pl-11 pr-4 py-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 shadow-clay-card" />
              </div>
              <div className="grid sm:grid-cols-2 gap-3">
                {CATS.map((cat) => {
                  const items = NAV.filter((n) => SEC_CAT[n.id] === cat.id);
                  if (!items.length) return null;
                  return (
                    <div key={cat.id} className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4">
                      <div className="flex items-center gap-2.5 mb-2">
                        <span className="w-8 h-8 rounded-lg bg-primary/10 text-primary grid place-items-center"><M n={cat.icon} c="text-base" /></span>
                        <span className="text-sm font-semibold text-ink-primary">{cat.label}</span>
                      </div>
                      <div className="flex flex-col">
                        {items.map((n) => (
                          <button key={n.id} onClick={() => setSec(n.id)} className="group flex items-center gap-2.5 px-2 py-1.5 -mx-1 rounded-lg text-left hover:bg-slate-50">
                            <M n={n.icon} c="text-[15px] text-ink-tertiary group-hover:text-primary" />
                            <span className="text-xs text-ink-secondary group-hover:text-ink-primary">{n.label}</span>
                            <M n="chevron_right" c="ml-auto text-sm text-ink-tertiary opacity-0 group-hover:opacity-100" />
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {sec === "general" && co && (
            <GeneralSettings co={co} setCo={setCo} saved={coSaved} onSave={saveCompany} rebuilding={rebuilding} onRebuild={rebuildBalances}
              opts={{ countries: COUNTRY_META, timezones: TIMEZONES, currencies: CURRENCIES, docLangs: DOC_LANGS, industries: INDUSTRIES }} />
          )}

          {sec === "statuses" && (
            <StatusList statuses={statuses} recordType={statusRT} onRecordType={(rt) => { setStatusRT(rt); setStatuses([]); }}
              onOpen={(s) => setStatusEdit(s)} onAdd={() => setStatusEdit("new")} onMove={moveStatus}
              onDeleted={() => reloadSec("statuses")} onFlash={flash} />
          )}

          {sec === "team" && (
            <TeamPage team={lists.team} roles={rbacRoles} invites={invites} rules={lists["payroll-rules"]} catalog={rbacCatalog}
              payLabel={(r) => ({ event: PAYROLL_EVENT_LABELS[r?.event] || r?.event || "", amount: r?.calc_type === "flat" ? `${r?.amount}` : `${r?.amount}%${r?.percent_basis === "margin" ? " of margin" : ""}` })}
              onEditPerson={(e) => setEmpEdit(e)} onNewPerson={() => setEmpEdit("new")} onInvite={() => setEditing({ sec: "invites", row: null })}
              onEditRole={(r) => setRoleEdit(r)} onNewRole={() => setRoleEdit("new")} onEditRule={(r) => setPayrollEdit(r)} onNewRule={() => setPayrollEdit("new")}
              onChanged={reloadInvites} onFlash={flash} />
          )}

          {sec === "locations" && <LocationsPage companyTz={co?.timezone || undefined} />}

          {sec === "feedback" && <FeedbackSettingsEditor />}

          {sec === "warehouses" && (
            <InventoryPage warehouses={lists.warehouses} currency={co?.currency || "$"} onAdd={() => setEditing({ sec: "warehouses", row: null })}
              onEdit={(r) => setEditing({ sec: "warehouses", row: r })} onDelete={(r) => delRow("warehouses", r)} />
          )}


          {sec === "notifications" && (
            <CustomerMessages rules={lists.notifications || []} templates={notifTemplates || []} shopName={co?.name || ""}
              statusesFor={(rt) => api.get<any>(`/statuses?record_type=${rt}`).then((r) => r.items || [])}
              reload={() => { reloadSec("notifications"); reloadNotifTemplates(); }} flash={flash}
              renderOther={(v) => (
                <NotificationsSettings fixedView={v}
                  rules={lists.notifications || []}
                  templates={notifTemplates || []}
                  automations={lists.automation || []}
                  reloadRules={() => reloadSec("notifications")}
                  reloadTemplates={() => reloadNotifTemplates()}
                  onEditAutomation={(row) => setEditing({ sec: "automation", row })}
                  onDeleteAutomation={(r) => delRow("automation", r)}
                  flash={flash}
                />
              )} />
          )}

          {sec === "templates" && (
            <DocumentTemplatesPage templates={lists.templates} onOpen={(row) => setEditing({ sec: "templates", row })}
              onChanged={() => reloadSec("templates")} onFlash={flash} />
          )}

          {sec === "finance" && <InvoiceSettings />}
          {sec === "finance" && <FinanceNumbering currency={co?.currency || "$"} shop={{ name: co?.name, address: co?.company_address, phone: co?.company_phone, email: co?.company_email, logo: co?.logo_data }} />}
          {sec === "ecommerce" && <EcommerceSettings />}
          {sec === "sso" && <SsoSettings />}
          {sec === "approvals" && <ApprovalsSettings />}

          {sec === "forms" && (
            <FormsDesigner types={lists.forms || []} onAddType={() => setEditing({ sec: "forms", row: null })}
              onDeleteType={(r) => delRow("forms", r)} onAdvanced={(t) => setFormFieldsFor(t)} />
          )}

          {sec === "pricing" && (
            <PricingPage types={lists.pricing} currency={co?.currency || "$"} onAdd={() => setEditing({ sec: "pricing", row: null })}
              onEdit={(r) => setEditing({ sec: "pricing", row: r })} onDelete={(r) => delRow("pricing", r)} tiers={<TierManager types={lists.pricing || []} />} />
          )}

          {sec === "industry" && (
            <div className="max-w-4xl space-y-5">
              <IndustryNote onChange={() => setSec("general")} />
              <MultiAssetSwitch />
              <AssetCatalog />
            </div>
          )}


          {sec === "checklists" && (
            <ChecklistList templates={lists.checklists} onOpen={(t) => setChecklistEdit(t)} onNew={(starter) => setChecklistEdit(starter || null)}
              onChanged={() => reloadSec("checklists")} onFlash={flash} />
          )}

          {sec === "directories" && <DirectoriesManager />}

          {sec === "public" && <PublicPagesSettings shopName={co?.name || ""} logo={co?.logo_data} StatusPicker={(pp) => <StatusMultiSelect value={pp.value} list={pp.list} onChange={pp.onChange} />} />}

          {sec === "integrations" && (
            <div className="max-w-4xl space-y-5">
              <IntegrationsHub data={integrations} goSection={setSec} reload={() => setIntegrations(null)} />
              <CardReadersCard />
              <TelephonySettings />
              <ApiKeysSection />
              <WebhooksCard />
            </div>
          )}
        </div>
      </div>
      {editing && editing.sec === "templates" ? (
        <TemplateEditorDrawer
          initial={editing.row}
          onClose={() => setEditing(null)}
          reload={() => reloadSec("templates")}
        />
      ) : editing && EDITORS[editing.sec] && (
        <EditorDrawer
          title={`${editing.row?.id ? "Edit" : "New"} ${NAV.find((n) => n.id === (editing.sec as Section))?.label || editing.sec}`}
          fields={EDITORS[editing.sec].fields}
          initial={editing.row}
          varModule={editing.module}
          onClose={() => setEditing(null)}
          onSave={saveRow}
          onDelete={EDITORS[editing.sec].del ? async () => { await delRow(editing.sec, editing.row); setEditing(null); } : undefined}
        />
      )}
      {roleEdit && rbacCatalog && (
        <RoleEditor role={roleEdit === "new" ? null : roleEdit} catalog={rbacCatalog} statuses={statuses}
          members={roleEdit === "new" ? 0 : (lists.team || []).filter((e: any) => e.role_id === roleEdit.id).length + (invites || []).filter((i: any) => i.role_id === roleEdit.id).length}
          onClose={() => setRoleEdit(null)} onSaved={() => { flash("Role saved"); setRoleEdit(null); reloadRbac(); }} />
      )}
      {empEdit && (
        <EmployeeEditorDrawer emp={empEdit === "new" ? null : empEdit} roles={rbacRoles || []}
          onClose={() => setEmpEdit(null)} onSaved={(m) => { flash(m || "Saved"); setEmpEdit(null); reloadTeam(); }} />
      )}
      {payrollEdit && (
        <PayrollRuleDrawer rule={payrollEdit === "new" ? null : payrollEdit} employees={lists.team || []} statuses={statuses}
          onClose={() => setPayrollEdit(null)} onSaved={(m) => { flash(m || "Saved"); setPayrollEdit(null); reloadPayrollRules(); }} />
      )}
      {statusEdit && (
        <StatusEditor status={statusEdit === "new" ? null : statusEdit} recordType={statusRT} statuses={statuses}
          onClose={() => setStatusEdit(null)} onSaved={(m) => { flash(m || "Saved"); setStatusEdit(null); reloadSec("statuses"); }}
          onOpenNotifications={() => { setStatusEdit(null); setSec("notifications"); }} />
      )}
      {formFieldsFor && (
        <FormFieldsDrawer type={formFieldsFor} onClose={() => setFormFieldsFor(null)} onChanged={() => reloadSec("forms")} />
      )}
      {checklistEdit !== undefined && (
        <ChecklistEditor tpl={checklistEdit} onClose={() => setChecklistEdit(undefined)} onSaved={() => { setChecklistEdit(undefined); reloadSec("checklists"); }} />
      )}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}



/* ── Inventory settings card (bins/reservations/serial templates) → /staff/inventory-settings ── */
/* ── Delivery status: which notification channels are live vs simulated (N-GAP-4) ── */
function DeliveryStatusBanner() {
  const [ch, setCh] = useState<Record<string, string> | null>(null);
  useEffect(() => { api.get<any>("/notifications/providers").then((r) => setCh(r.channels || {})).catch(() => setCh(null)); }, []);
  if (!ch) return null;
  const order = ["email", "sms", "whatsapp", "push"];
  const anySim = order.some((k) => ch[k] === "simulated");
  return (
    <div className={"rounded-xl border p-4 " + (anySim ? "border-amber-300 bg-amber-50" : "border-emerald-300 bg-emerald-50")}>
      <div className="text-[13px] font-bold text-ink-primary">Message delivery</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {order.map((k) => (
          <span key={k} className={"inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-semibold " + (ch[k] === "live" ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-ink-secondary")}>
            <span className={"h-1.5 w-1.5 rounded-full " + (ch[k] === "live" ? "bg-emerald-500" : "bg-slate-400")} />
            {k.toUpperCase()} · {ch[k]}
          </span>
        ))}
      </div>
      {anySim && <div className="mt-2 text-[12px] text-ink-tertiary">Channels marked <b>simulated</b> are logged, not delivered. Add provider credentials (SMTP / Twilio / VAPID) or connect an email account to go live.</div>}
    </div>
  );
}

/* ── Notifications settings (144jb): External · Internal · Automations ─────────
   Mirrors Orderry's Workflows layout. External = client messages; Internal =
   staff (assignee + manager) alerts; Automations = the broader action engine
   (tasks, tags, webhooks, overdue/low-stock triggers) folded in here too. */
const EXTERNAL_MODULES: { key: string; label: string }[] = [
  { key: "order", label: "Workflows" },
  { key: "lead", label: "Inquiries" },
  { key: "appointment", label: "Appointments" },
  { key: "contact", label: "Contacts" },
];
// Internal = staff-facing. Orderry's Internal has Tasks/Inquiries/Workflows.
const INTERNAL_MODULES: { key: string; label: string }[] = [
  { key: "order", label: "Workflows" },
  { key: "lead", label: "Inquiries" },
  { key: "task", label: "Tasks" },
];
// Events per (scope, module). Reminder is client-only; birthday is contacts-only.
function eventsFor(internal: boolean, module: string): [string, string][] {
  if (module === "contact") return [["birthday", "Birthday"]];
  if (module === "task") return [["assigned", "Assigned to the task"], ["changed", "Task changed"], ["completed", "Task completed"]];
  if (module === "lead") return internal
    ? [["created", "Created"], ["status_changed", "Status changed"], ["closed", "Closed"]]
    : [["created", "Inquiry created"], ["status_changed", "Status changed"], ["closed", "Inquiry closed"]];
  if (module === "appointment") return [["created", "Appointment created"], ["status_changed", "Status changed"], ["changed", "Rescheduled"], ["reminder", "Appointment reminder"]];
  // order module (Workflows) — 144jc adds comment/payment events
  if (internal) return [["created", "Created"], ["status_changed", "Status changed"], ["comment_added", "Comment added"], ["payment_received", "Payment received"], ["closed", "Closed"]];
  return [["created", "Order created"], ["status_changed", "Status changed"], ["comment_added", "Comment added"], ["payment_received", "Payment received"], ["closed", "Order closed"], ["reminder", "Appointment reminder"]];
}
const EVENT_LABEL = (internal: boolean, m: string, e: string) => (eventsFor(internal, m).find(([k]) => k === e)?.[1]) || e.replace(/_/g, " ");

function NotificationsSettings({ fixedView, rules, templates, automations, reloadRules, reloadTemplates, onEditAutomation, onDeleteAutomation, flash }: {
  fixedView?: "internal" | "automations";
  rules: any[]; templates: any[]; automations: any[];
  reloadRules: () => void; reloadTemplates: () => void;
  onEditAutomation: (row: any | null) => void; onDeleteAutomation: (r: any) => void;
  flash: (m: string) => void;
}) {
  const [viewState, setView] = useState<"external" | "internal" | "automations">("external");
  const view = fixedView || viewState;
  const isInternal = view === "internal";
  const mods = isInternal ? INTERNAL_MODULES : EXTERNAL_MODULES;
  const [module, setModule] = useState<string>("order");
  const [recType, setRecType] = useState<"work_order" | "estimate">("work_order");
  const [ruleEdit, setRuleEdit] = useState<any | null | "new">(null);
  const [tplEdit, setTplEdit] = useState<any | null | "new">(null);
  useEffect(() => { if (view !== "automations" && !mods.some((m) => m.key === module)) setModule(mods[0].key); }, [view]);  // eslint-disable-line

  const wantScope = isInternal ? "staff" : "client";
  const tplName = (id: string) => templates.find((t) => t.id === id)?.name || "—";

  // Rules for the active module + scope; the order module further splits by record type.
  const moduleRules = (rules || []).filter((r) =>
    (r.module || "order") === module &&
    (r.scope || "client") === wantScope &&
    (module !== "order" || (recType === "estimate" ? r.record_type === "estimate" : r.record_type !== "estimate")));
  const eventRules = moduleRules.filter((r) => r.event !== "reminder");
  const reminderRules = moduleRules.filter((r) => r.event === "reminder");
  const remindersAllowed = !isInternal && (module === "order" || module === "appointment");

  const ruleCols: [string, (r: any) => string][] = [
    ["Event", (r) => EVENT_LABEL(isInternal, r.module || "order", r.event) + (r.trigger_status_key ? ` → ${r.trigger_status_key}` : "")],
    ["Send via", (r) => (r.channel === "push" ? "In-app / push" : String(r.channel || "—").toUpperCase())],
    ["Template", (r) => (isInternal ? "—" : tplName(r.template_id))],
    ["Location", (r) => (r.location_id ? "Selected" : "All")],
    ["Status", (r) => (r?.active ? "Active" : "Off")],
  ];
  const delRule = async (r: any) => { if (await confirmDialog("Delete this notification?", { danger: true, confirmLabel: "Delete" })) { try { await api.del(`/notifications/rules/${r.id}`); flash("Deleted"); reloadRules(); } catch (e: any) { flash(`Failed: ${e.message}`); } } };

  const modLabel = mods.find((m) => m.key === module)?.label || module;

  return (
    <div className="max-w-3xl space-y-5">
      {!fixedView && <DeliveryStatusBanner />}

      {/* External / Internal / Automations */}
      {!fixedView && <div className="inline-flex rounded-xl border border-slate-200 bg-slate-50 p-1">
        {([["external", "External (clients)"], ["internal", "Internal (staff)"], ["automations", "Automations"]] as const).map(([v, label]) => (
          <button key={v} onClick={() => setView(v)}
            className={"px-4 py-1.5 rounded-lg text-xs font-semibold transition-colors " + (view === v ? "bg-white text-ink-primary shadow-clay-sm" : "text-ink-tertiary hover:text-ink-secondary")}>
            {label}
          </button>
        ))}
      </div>}

      {view === "automations" ? (
        <>
          <SectionCard title="Automation rules" count={automations?.length} onAdd={() => onEditAutomation(null)}>
            <p className="px-3.5 pt-3 text-[11px] text-ink-tertiary">Trigger an action when something happens — create a task, assign a tech, add a tag, call a webhook, or fire a notification. Also runs on overdue orders and low stock.</p>
            <SecTable rows={automations} onRowClick={(r) => onEditAutomation(r)} onDelete={onDeleteAutomation}
              cols={[["Rule", (r) => r.name || "—"], ["Event", (r) => String(r.event || "—").replace(/_/g, " ")], ["Action", (r) => String(r.action_type || "—").replace(/_/g, " ")], ["Status", (r) => (r?.active ? "Active" : "Off")]]} />
          </SectionCard>
        </>
      ) : (
        <>
          {/* Per-module tabs */}
          <div className="flex flex-wrap gap-1.5">
            {mods.map((m) => (
              <button key={m.key} onClick={() => setModule(m.key)}
                className={"px-3 py-1.5 rounded-full text-xs font-semibold transition-colors " + (module === m.key ? "bg-primary text-white" : "bg-slate-100 text-ink-secondary hover:bg-slate-200/70")}>
                {m.label}
              </button>
            ))}
          </div>

          {/* Workflows splits into Jobs vs Estimates (Orderry parity) */}
          {module === "order" && (
            <div className="flex gap-4 text-xs font-semibold border-b border-slate-200">
              {([["work_order", "Jobs"], ["estimate", "Estimates"]] as const).map(([k, l]) => (
                <button key={k} onClick={() => setRecType(k)}
                  className={"py-2 border-b-2 transition-colors " + (recType === k ? "text-primary border-primary" : "text-ink-tertiary border-transparent hover:text-ink-secondary")}>
                  {l}
                </button>
              ))}
            </div>
          )}

          <SectionCard
            title={`${module === "order" ? (recType === "estimate" ? "Estimates" : "Jobs") : modLabel} notifications`}
            count={eventRules.length}
            onAdd={() => setRuleEdit("new")}>
            <p className="px-3.5 pt-3 text-[11px] text-ink-tertiary">
              {isInternal
                ? "Keep your team informed — the assigned technician and the order's manager get these."
                : "Clients receive notifications only if they've given consent (set per contact, or defaulted in Settings › General)."}
            </p>
            <SecTable rows={eventRules} onRowClick={(r) => setRuleEdit(r)} onDelete={delRule} cols={ruleCols} />
          </SectionCard>

          {remindersAllowed && (
            <SectionCard title="Reminders" count={reminderRules.length} onAdd={() => setRuleEdit({ __reminder: true })}>
              <p className="px-3.5 pt-3 text-[11px] text-ink-tertiary">Remind clients ahead of a scheduled appointment.</p>
              <SecTable rows={reminderRules} onRowClick={(r) => setRuleEdit(r)} onDelete={delRule}
                cols={[["Before", (r) => `${r.reminder_offset_min || 0} min`], ["Send via", (r) => String(r.channel || "—").toUpperCase()], ["Template", (r) => tplName(r.template_id)], ["Location", (r) => (r.location_id ? "Selected" : "All")], ["Status", (r) => (r?.active ? "Active" : "Off")]]} />
            </SectionCard>
          )}

          <SectionCard title="Message templates" count={templates?.length ?? undefined} onAdd={() => setTplEdit("new")}>
            <SecTable rows={templates} onRowClick={(r) => setTplEdit(r)}
              onDelete={async (r) => { if (await confirmDialog("Delete this template?", { danger: true, confirmLabel: "Delete" })) { try { await api.del(`/notifications/templates/${r.id}`); flash("Deleted"); reloadTemplates(); } catch (e: any) { flash(`Failed: ${e.message}`); } } }}
              cols={[["Title", (t) => t.name || t.trigger_event || "—"], ["Type", (t) => (t.channel === "email" ? "Email" : t.channel === "sms" ? "SMS" : String(t.channel || "—").toUpperCase())], ["Status", (t) => (t?.active ? "Active" : "Off")]]} />
          </SectionCard>
        </>
      )}

      {ruleEdit !== null && (
        <NotifRuleDrawer
          module={module} internal={isInternal}
          recordType={module === "order" ? recType : null}
          forceReminder={!!(ruleEdit && ruleEdit.__reminder)}
          rule={ruleEdit === "new" || ruleEdit?.__reminder ? null : ruleEdit}
          templates={templates}
          onClose={() => setRuleEdit(null)}
          onSaved={() => { setRuleEdit(null); reloadRules(); flash("Saved"); }} />
      )}
      {tplEdit !== null && (
        <NotifTemplateDrawer
          tpl={tplEdit === "new" ? null : tplEdit}
          module={module}
          onClose={() => setTplEdit(null)}
          onSaved={() => { setTplEdit(null); reloadTemplates(); flash("Saved"); }} />
      )}
    </div>
  );
}

function NotifRuleDrawer({ module, internal, recordType, forceReminder, rule, templates, onClose, onSaved }: {
  module: string; internal: boolean; recordType: string | null; forceReminder: boolean;
  rule: any | null; templates: any[]; onClose: () => void; onSaved: () => void;
}) {
  const events = eventsFor(internal, module);
  const [locations, setLocations] = useState<any[]>([]);
  useEffect(() => { api.get<any>("/staff/locations").then((r) => setLocations((r.items || r || []).filter((l: any) => l.active !== false))).catch(() => setLocations([])); }, []);
  // 144jd — status picker for "Status changed to": load the org's statuses for this
  // module's record type (Orderry shows the actual statuses, grouped & coloured).
  const statusRT = module === "order" ? (recordType || "work_order") : module === "lead" ? "inquiry" : null;
  const [statuses, setStatuses] = useState<any[]>([]);
  useEffect(() => {
    if (!statusRT) { setStatuses([]); return; }
    api.get<any>(`/statuses?record_type=${statusRT}`).then((r) => setStatuses(r.items || r || [])).catch(() => setStatuses([]));
  }, [statusRT]);
  const [f, setF] = useState<any>({
    event: rule?.event || (forceReminder ? "reminder" : events[0][0]),
    channel: rule?.channel || (internal ? "push" : "email"),
    template_id: rule?.template_id || "",
    trigger_status_key: rule?.trigger_status_key || "",
    location_id: rule?.location_id || "",
    delay_min: rule?.delay_min ?? 0,
    working_hours_only: !!rule?.working_hours_only,
    chat_first: !!rule?.chat_first,
    reminder_offset_min: rule?.reminder_offset_min ?? 60,
    active: rule?.active ?? true,
  });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  const channels = internal ? ["push", "email", "sms"] : ["email", "sms", "whatsapp", "chat"];
  const isReminder = forceReminder || f.event === "reminder";
  const tplOpts = templates.filter((t) => !t.channel || t.channel === f.channel);

  async function save() {
    setBusy(true); setErr(null);
    const body: any = {
      module, scope: internal ? "staff" : "client",
      record_type: module === "order" ? recordType : null,
      event: isReminder ? "reminder" : f.event, channel: f.channel,
      template_id: internal ? null : (f.template_id || null),
      trigger_status_key: f.trigger_status_key || null,
      location_id: f.location_id || null,
      delay_min: Number(f.delay_min) || 0,
      working_hours_only: !!f.working_hours_only,
      chat_first: internal ? false : !!f.chat_first,
      reminder_offset_min: isReminder ? (Number(f.reminder_offset_min) || 0) : 0,
      active: !!f.active,
    };
    try {
      if (rule?.id) await api.patch(`/notifications/rules/${rule.id}`, body);
      else await api.post("/notifications/rules", body);
      onSaved();
    } catch (e: any) { setErr(e?.message || "Could not save"); setBusy(false); }
  }

  const scopeLabel = internal ? "Internal" : "External";
  const modLabel = module === "order" ? (recordType === "estimate" ? "Estimates" : "Jobs")
    : (EXTERNAL_MODULES.concat(INTERNAL_MODULES).find((m) => m.key === module)?.label || module);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[440px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <div>
            <span className="text-[11px] font-bold text-primary block">{scopeLabel} · {modLabel}</span>
            <h2 className="font-bold text-ink-primary">{rule?.id ? "Edit notification" : isReminder ? "New reminder" : "New notification"}</h2>
          </div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          {!isReminder && (
            <div><label className={lbl}>Event</label>
              <select value={f.event} onChange={(e) => set("event", e.target.value)} className={field}>
                {events.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select></div>
          )}
          {!isReminder && f.event === "status_changed" && statusRT && (
            <div><label className={lbl}>Status changed to</label>
              <StatusMultiSelect single placeholder="Any status change"
                value={f.trigger_status_key}
                list={statuses.map((s: any) => ({ key: s.key || s.id, name: s.name, status_group: s.status_group, color: s.color }))}
                onChange={(v) => set("trigger_status_key", v)} />
              <div className="mt-1 text-[11px] text-ink-tertiary">Leave "Any" to fire on every status change, or pick a specific status.</div></div>
          )}
          {!isReminder && f.event === "status_changed" && !statusRT && (
            <div><label className={lbl}>Only when status is (key, optional)</label>
              <input value={f.trigger_status_key} onChange={(e) => set("trigger_status_key", e.target.value)} placeholder="blank = any" className={field} /></div>
          )}
          <div><label className={lbl}>Send via</label>
            <select value={f.channel} onChange={(e) => set("channel", e.target.value)} className={field}>
              {channels.map((c) => <option key={c} value={c}>{c === "push" ? "In-app / push" : c.toUpperCase()}</option>)}
            </select></div>
          {!internal && (
            <div><label className={lbl}>Template</label>
              <select value={f.template_id} onChange={(e) => set("template_id", e.target.value)} className={field}>
                <option value="">Default message</option>
                {tplOpts.map((t) => <option key={t.id} value={t.id}>{t.name || t.trigger_event}</option>)}
              </select>
              <div className="mt-1 text-[11px] text-ink-tertiary">No template → a sensible default message is sent.</div>
            </div>
          )}
          {isReminder && (
            <div><label className={lbl}>Send how long before?</label>
              <div className="flex items-center gap-2">
                <input type="number" min={1} value={f.reminder_offset_min} onChange={(e) => set("reminder_offset_min", e.target.value)} className={field + " w-28"} />
                <span className="text-xs text-ink-tertiary">minutes before the appointment</span>
              </div></div>
          )}
          {!isReminder && (
            <div><label className={lbl}>Delay sending (minutes)</label>
              <input type="number" min={0} value={f.delay_min} onChange={(e) => set("delay_min", e.target.value)} className={field} />
              <div className="mt-1 text-[11px] text-ink-tertiary">0 = send immediately.</div></div>
          )}
          {locations.length > 1 && (
            <div><label className={lbl}>Location</label>
              <select value={f.location_id} onChange={(e) => set("location_id", e.target.value)} className={field}>
                <option value="">All locations</option>
                {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select></div>
          )}
          <ToggleRow label="Only send during working hours" on={f.working_hours_only} onToggle={() => set("working_hours_only", !f.working_hours_only)} />
          {!internal && <ToggleRow label="Try the client's latest chat first" on={f.chat_first} onToggle={() => set("chat_first", !f.chat_first)} />}
          <ToggleRow label="Active" on={f.active} onToggle={() => set("active", !f.active)} />
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex items-center gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs ml-auto">Cancel</button>
          <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}

// 144je — inline variable pills for the email editor (mirrors the document editor):
// {Tokens} render as non-editable chips; serialize converts them back to {Tokens}.
function notifPill(tok: string) {
  const label = tok.replace(/^\{|\}$/g, "");
  return `<span class="tok-pill" contenteditable="false" data-tok="${escapeHtml(tok)}">${escapeHtml(label)}</span>`;
}
function notifHydrate(html: string) { return sanitizeHtml(html || "").replace(/\{[A-Z][^{}]*\}/g, (m) => notifPill(m)); }
function notifSerialize(root: HTMLElement) {
  const clone = root.cloneNode(true) as HTMLElement;
  clone.querySelectorAll(".tok-pill").forEach((el) => el.replaceWith(document.createTextNode((el as HTMLElement).dataset.tok || "")));
  clone.querySelectorAll("[contenteditable]").forEach((el) => el.removeAttribute("contenteditable"));
  return clone.innerHTML;
}
function notifTableHTML() {
  const cell = (s: string) => `<td style="border:1px solid #ddd;padding:6px">${s}</td>`;
  return `<table style="width:100%;border-collapse:collapse;margin:8px 0"><tbody><tr>${cell("Label")}${cell("Value")}</tr><tr>${cell("&nbsp;")}${cell("&nbsp;")}</tr></tbody></table>`;
}

// 144jd/je — rich email template editor, styled like the document template editor:
// font/size/colour/highlight, B/I/U/S, headings, lists, alignment, divider, link,
// image, one-click company logo, table, inline variable pills, and an HTML source
// toggle. SMS stays a plain textarea. Stores HTML with {Tokens}.
function RichTextEditor({ value, onChange, registerInsert }: {
  value: string; onChange: (html: string) => void; registerInsert?: (fn: (tok: string) => void) => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const savedRange = useRef<Range | null>(null);
  const [logo, setLogo] = useState<string>("");
  const [code, setCode] = useState<string | null>(null);  // HTML source view (null = off)
  useEffect(() => { api.get<any>("/staff/company").then((c) => setLogo(c.logo_data || "")).catch(() => {}); }, []);
  useEffect(() => { if (ref.current) ref.current.innerHTML = notifHydrate(value || ""); }, []);  // init once
  const emit = () => onChange(ref.current ? notifSerialize(ref.current) : "");
  const focus = () => ref.current?.focus();
  const saveSel = () => { const s = window.getSelection(); if (s && s.rangeCount && ref.current?.contains(s.anchorNode)) savedRange.current = s.getRangeAt(0).cloneRange(); };
  const restoreSel = () => { const s = window.getSelection(); if (savedRange.current && s) { s.removeAllRanges(); s.addRange(savedRange.current); } };
  const exec = (cmd: string, val?: string) => { restoreSel(); focus(); document.execCommand(cmd, false, val); emit(); };
  const applyFont = (fam: string) => { restoreSel(); focus(); document.execCommand("styleWithCSS", false, "true"); document.execCommand("fontName", false, fam); emit(); };
  const applyColor = (c: string) => { restoreSel(); focus(); document.execCommand("styleWithCSS", false, "true"); document.execCommand("foreColor", false, c); emit(); };
  const applyHilite = (c: string) => { restoreSel(); focus(); document.execCommand("styleWithCSS", false, "true"); (document.execCommand("hiliteColor", false, c) || document.execCommand("backColor", false, c)); emit(); };
  const applySize = (px: number) => {
    restoreSel(); focus(); document.execCommand("styleWithCSS", false, "false"); document.execCommand("fontSize", false, "7");
    ref.current?.querySelectorAll('font[size="7"]').forEach((ft) => { const sp = document.createElement("span"); sp.style.fontSize = px + "px"; while (ft.firstChild) sp.appendChild(ft.firstChild); ft.replaceWith(sp); });
    emit();
  };
  const insertHtml = (html: string) => { restoreSel(); focus(); document.execCommand("insertHTML", false, html); emit(); };
  useEffect(() => { registerInsert?.((tok: string) => insertHtml(notifPill(tok))); }, [registerInsert]);  // eslint-disable-line
  function toggleCode() {
    if (code === null) { if (ref.current) setCode(notifSerialize(ref.current)); }
    else { if (ref.current) ref.current.innerHTML = notifHydrate(code); setCode(null); onChange(code); }
  }
  const fonts = ["Inter", "Arial", "Helvetica", "Georgia", "Times New Roman", "Courier New", "Verdana", "Tahoma"];
  const sizes = [10, 11, 12, 14, 16, 18, 20, 24, 28, 32];
  const btn = "w-8 h-8 rounded hover:bg-slate-200/70 text-ink-secondary flex items-center justify-center";
  const TB = ({ cmd, val, icon, title }: { cmd: string; val?: string; icon: string; title: string }) => (
    <button type="button" aria-label={title} title={title} onMouseDown={(e) => { e.preventDefault(); exec(cmd, val); }} className={btn}><M n={icon} c="text-[18px]" /></button>
  );
  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden bg-white">
      <div className="flex flex-wrap items-center gap-0.5 border-b border-slate-200 bg-slate-50 px-2 py-1">
        <select title="Font" onMouseDown={saveSel} onChange={(e) => applyFont(e.target.value)} defaultValue="" className="h-8 border border-slate-200 rounded-md text-xs px-1.5 bg-white text-ink-secondary"><option value="" disabled>Font</option>{fonts.map((ff) => <option key={ff} value={ff} style={{ fontFamily: ff }}>{ff}</option>)}</select>
        <select title="Size" onMouseDown={saveSel} onChange={(e) => applySize(Number(e.target.value))} defaultValue="" className="h-8 border border-slate-200 rounded-md text-xs px-1.5 bg-white text-ink-secondary w-16"><option value="" disabled>Size</option>{sizes.map((s) => <option key={s} value={s}>{s}px</option>)}</select>
        <label className={btn + " relative cursor-pointer"} title="Text colour"><M n="format_color_text" c="text-[18px]" /><input type="color" onMouseDown={saveSel} onChange={(e) => applyColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" /></label>
        <label className={btn + " relative cursor-pointer"} title="Highlight"><M n="format_ink_highlighter" c="text-[18px]" /><input type="color" onMouseDown={saveSel} onChange={(e) => applyHilite(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" /></label>
        <span className="w-px h-5 bg-slate-200 mx-1" />
        <TB cmd="bold" icon="format_bold" title="Bold" />
        <TB cmd="italic" icon="format_italic" title="Italic" />
        <TB cmd="underline" icon="format_underlined" title="Underline" />
        <TB cmd="strikeThrough" icon="strikethrough_s" title="Strikethrough" />
        <TB cmd="formatBlock" val="h1" icon="title" title="Heading" />
        <TB cmd="insertUnorderedList" icon="format_list_bulleted" title="Bullet list" />
        <TB cmd="justifyLeft" icon="format_align_left" title="Align left" />
        <TB cmd="justifyCenter" icon="format_align_center" title="Align centre" />
        <TB cmd="justifyRight" icon="format_align_right" title="Align right" />
        <TB cmd="insertHorizontalRule" icon="horizontal_rule" title="Divider" />
        <span className="w-px h-5 bg-slate-200 mx-1" />
        <button aria-label="Insert link" type="button" className={btn} title="Insert link" onMouseDown={async (e) => { e.preventDefault(); saveSel(); const u = await promptDialog("Link URL"); if (u) exec("createLink", u); }}><M n="link" c="text-[18px]" /></button>
        <button aria-label="Insert image" type="button" className={btn} title="Insert image" onMouseDown={async (e) => { e.preventDefault(); saveSel(); const u = await promptDialog("Image URL"); if (u) insertHtml(`<img src="${u}" style="max-width:100%" />`); }}><M n="image" c="text-[18px]" /></button>
        <button aria-label="Insert table" type="button" className={btn} title="Insert table" onMouseDown={(e) => { e.preventDefault(); saveSel(); insertHtml(notifTableHTML()); }}><M n="table" c="text-[18px]" /></button>
        {logo && <button type="button" className="h-8 px-2 rounded hover:bg-slate-200/70 text-xs font-semibold text-primary flex items-center gap-1" title="Insert company logo" onMouseDown={(e) => { e.preventDefault(); saveSel(); insertHtml(`<img src="${logo}" alt="logo" style="max-height:64px" />`); }}><M n="image" c="text-[16px]" /> Logo</button>}
        <button aria-label="Edit HTML source" type="button" title="Edit HTML source" onClick={toggleCode} className={"w-8 h-8 rounded flex items-center justify-center ml-auto " + (code !== null ? "bg-primary/10 text-primary" : "hover:bg-slate-200/70 text-ink-secondary")}><M n="code" c="text-[18px]" /></button>
      </div>
      {code !== null ? (
        <textarea value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false}
          className="w-full min-h-[240px] max-h-[420px] bg-slate-900 text-slate-100 tabular-nums text-xs p-3 outline-none resize-y" />
      ) : (
        <div ref={ref} contentEditable suppressContentEditableWarning onInput={emit} onKeyUp={saveSel} onMouseUp={saveSel}
          className="min-h-[240px] max-h-[420px] overflow-y-auto px-3 py-2 text-sm text-ink-primary focus:outline-none" />
      )}
    </div>
  );
}

// 144jb — template editor differentiated by Type (Orderry: Email has a subject +
// rich body; SMS is plain text with a segment counter).
function NotifTemplateDrawer({ tpl, module, onClose, onSaved }: {
  tpl: any | null; module: string; onClose: () => void; onSaved: () => void;
}) {
  const [f, setF] = useState<any>({
    name: tpl?.name || "",
    channel: tpl?.channel || "sms",
    subject: tpl?.subject || "",
    body_template: tpl?.body_template || "",
    trigger_event: tpl?.trigger_event || "status_changed",
    active: tpl?.active ?? true,
  });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const areaRef = useRef<HTMLTextAreaElement | null>(null);
  const richInsert = useRef<((tok: string) => void) | null>(null);
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  const isEmail = f.channel === "email";
  async function sendTest() {
    if (!f.body_template.trim()) { setErr("Add some content to test"); return; }
    setTestMsg("Sending…"); setErr(null);
    try {
      const r = await api.post<any>("/notifications/test", { channel: f.channel, subject: isEmail ? f.subject : null, body: f.body_template });
      setTestMsg(r.status === "sent" ? `Test sent to ${r.recipient}` : `Test ${r.status || "queued"} (${r.recipient})`);
    } catch (e: any) { setTestMsg(null); setErr(e?.message || "Test failed — check your profile has an email/phone"); }
  }
  function insertVar(tok: string) {
    const ta = areaRef.current; const cur = String(f.body_template ?? "");
    if (!ta) { set("body_template", cur + tok); return; }
    const s = ta.selectionStart ?? cur.length, e = ta.selectionEnd ?? cur.length;
    set("body_template", cur.slice(0, s) + tok + cur.slice(e));
    requestAnimationFrame(() => { ta.focus(); const p = s + tok.length; ta.setSelectionRange(p, p); });
  }
  async function save() {
    if (!f.name.trim()) { setErr("Give the template a title"); return; }
    if (!f.body_template.trim()) { setErr("Message body is required"); return; }
    setBusy(true); setErr(null);
    const body = {
      name: f.name, channel: f.channel,
      subject: isEmail ? (f.subject || null) : null,
      body_template: f.body_template, trigger_event: f.trigger_event, active: !!f.active,
    };
    try {
      if (tpl?.id) await api.put(`/notifications/templates/${tpl.id}`, body);
      else await api.post("/notifications/templates", body);
      onSaved();
    } catch (e: any) { setErr(e?.message || "Could not save"); setBusy(false); }
  }
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[720px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-bold text-ink-primary">{tpl?.id ? "Edit template" : "New template"}</h2>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          {/* Type: SMS vs Email — the editor changes with it */}
          <div><label className={lbl}>Type</label>
            <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1">
              {(["sms", "email"] as const).map((c) => (
                <button key={c} type="button" onClick={() => set("channel", c)}
                  className={"px-4 py-1.5 rounded-md text-xs font-semibold transition-colors " + (f.channel === c ? "bg-white text-ink-primary shadow-clay-sm" : "text-ink-tertiary")}>
                  {c === "sms" ? "SMS" : "Email"}
                </button>
              ))}
            </div>
          </div>
          <div><label className={lbl}>Title *</label>
            <input value={f.name} onChange={(e) => set("name", e.target.value)} className={field} placeholder={isEmail ? "E-mail: order ready" : "SMS: order ready"} /></div>
          {isEmail && (
            <div><label className={lbl}>Subject</label>
              <input value={f.subject} onChange={(e) => set("subject", e.target.value)} className={field} placeholder="Your order is ready for pickup" /></div>
          )}
          <div>
            <label className={lbl}>Content *</label>
            <div className="grid grid-cols-1 sm:grid-cols-[1fr_260px] gap-3">
              {isEmail ? (
                <RichTextEditor value={f.body_template} onChange={(html) => set("body_template", html)} registerInsert={(fn) => { richInsert.current = fn; }} />
              ) : (
                <textarea ref={areaRef} value={f.body_template} onChange={(e) => set("body_template", e.target.value)} rows={6}
                  className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary tabular-nums" />
              )}
              <VarPicker onInsert={(tok) => (isEmail ? richInsert.current?.(tok) : insertVar(tok))} module={module} />
            </div>
            {!isEmail && <CharCounter text={String(f.body_template ?? "")} sms />}
          </div>
          <ToggleRow label="Active" on={f.active} onToggle={() => set("active", !f.active)} />
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex items-center gap-2">
          <button onClick={sendTest} className="px-3 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Send test</button>
          {testMsg && <span className="text-[11px] text-emerald-600 font-medium">{testMsg}</span>}
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs ml-auto">Cancel</button>
          <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}

function ToggleRow({ label, on, onToggle }: { label: string; on: boolean; onToggle: () => void }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs font-medium text-ink-secondary">{label}</span>
      <button type="button" onClick={onToggle} className={`w-10 h-6 rounded-full transition-colors ${on ? "bg-primary" : "bg-slate-300"} relative`}>
        <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
      </button>
    </div>
  );
}

/* ── Customer feedback settings → /feedback/settings ({settings} wrapper, SettingsIn) ── */
function FeedbackSettingsEditor() {
  const [c, setC] = useState<any>(null); const [saved, setSaved] = useState(false);
  useEffect(() => { api.get<any>("/feedback/settings").then((r) => setC(r.settings || { system: "five_point", heading: "How did we do?", question: "How would you rate your experience?", clarify_question: "Tell us more", active: false })).catch(() => setC({})); }, []);
  const set = (k: string, v: any) => setC((s: any) => ({ ...s, [k]: v }));
  async function save() {
    try {
      await api.put("/feedback/settings", { system: c.system || "five_point", trigger_status_id: c.trigger_status_id || null, heading: c.heading || "How did we do?", subheading: c.subheading || null, question: c.question || "How would you rate your experience?", clarify_question: c.clarify_question || "Tell us more", bands: c.bands || {}, active: !!c.active, google_review_url: c.google_review_url || null });
      setSaved(true); setTimeout(() => setSaved(false), 1500);
    } catch (e: any) { alertDialog(e.message); }
  }
  return (
    <div className="max-w-3xl"><SectionCard title="Customer feedback">
      {!c ? <div className="p-5 text-xs text-ink-tertiary">Loading…</div> : (
        <div className="p-5 space-y-4">
          <label className="flex items-center gap-2 text-xs font-medium text-ink-secondary"><input type="checkbox" checked={!!c.active} onChange={(e) => set("active", e.target.checked)} className="accent-primary" />Ask customers for feedback after a work order closes</label>
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl}>Rating system</label><select value={c.system || "five_point"} onChange={(e) => set("system", e.target.value)} className={field}>{[["five_point", "5-point stars"], ["like", "Like / dislike"], ["nps", "NPS (0–10)"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
            <div><label className={lbl}>Heading</label><input value={c.heading || ""} onChange={(e) => set("heading", e.target.value)} className={field} /></div>
          </div>
          <div><label className={lbl}>Subheading</label><input value={c.subheading || ""} onChange={(e) => set("subheading", e.target.value)} className={field} /></div>
          <div><label className={lbl}>Question</label><input value={c.question || ""} onChange={(e) => set("question", e.target.value)} className={field} /></div>
          <div><label className={lbl}>Clarify prompt (after rating)</label><input value={c.clarify_question || ""} onChange={(e) => set("clarify_question", e.target.value)} className={field} /></div>
          <div><label className={lbl}>Public review link (shown to happy customers)</label><input value={c.google_review_url || ""} onChange={(e) => set("google_review_url", e.target.value)} placeholder="https://g.page/r/…/review" className={field} /><div className="mt-1 text-[11px] text-ink-tertiary">Everyone is asked to rate; high raters also get a one-tap button to post this public review.</div></div>
          <div className="flex justify-end items-center gap-2">{saved && <span className="text-[11px] font-semibold text-emerald-600">Saved</span>}<button onClick={save} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-semibold">Save settings</button></div>
        </div>
      )}
    </SectionCard></div>
  );
}

/* ── Integrations hub — surfaces every connector already wired in the backend
   (credential-gated seams). Card grid like Orderry; badges read live status
   where an endpoint exists, else show the gated/available state honestly. ── */
function IntegrationsHub({ data, goSection, reload }: { data: any; goSection: (s: Section) => void; reload?: () => void }) {
  const [keyProv, setKeyProv] = useState<string | null>(null);
  if (!data) return <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-6 text-xs text-ink-tertiary">Loading integrations…</div>;
  const creds = data.creds || {};
  const srcOf = (p: string) => creds[p]?.source as ("org" | "env" | "none" | undefined);
  const keyBadge = (p: string): { text: string; tone: "on" | "off" | "neutral" } => {
    const s = srcOf(p);
    if (s === "org") return { text: "Your keys", tone: "on" };
    if (s === "env") return { text: "Shared default", tone: "neutral" };
    return { text: "Not set", tone: "off" };
  };
  const keyAction = (p: string) => ({ label: srcOf(p) === "org" ? "Edit keys" : "Add keys", onClick: () => setKeyProv(p) });
  const emailOn = (data.email?.accounts?.length || 0) > 0;
  const payGw = data.pay?.gateway; const payLive = !!data.pay?.live;
  const payAvail = Object.entries(data.pay?.available || {}).filter(([, v]) => v).map(([k]) => k);
  const telOn = !!data.tel?.enabled; const telProv = data.tel?.provider;
  const ecomN = data.ecom?.length || 0;
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const copyWidget = async () => {
    const snip = `<div id="tekly-status"></div>\n<script src="${origin}/api/public/widget.js" data-api="${origin}"></script>`;
    try { navigator.clipboard.writeText(snip); alertDialog("Website status-widget embed snippet copied to clipboard."); }
    catch { await promptDialog("Copy the embed snippet:", { defaultValue: snip }); }
  };
  type Badge = { text: string; tone: "on" | "off" | "neutral" };
  const B = { on: "bg-emerald-100 text-emerald-700", off: "bg-slate-100 text-ink-secondary", neutral: "bg-indigo-50 text-indigo-700" } as const;
  const groups: { group: string; cards: { name: string; desc: string; badge: Badge; hint?: string; action?: { label: string; onClick: () => void } }[] }[] = [
    { group: "Payments", cards: [
      { name: "Online payments (Stripe)", desc: "Hosted checkout & payment links; reconciles into your cashboxes.",
        badge: payLive ? { text: `Live: ${payGw}`, tone: "on" } : keyBadge("stripe"),
        hint: payAvail.length ? `Configured: ${payAvail.join(", ")}` : "Add your own Stripe secret key to go live",
        action: keyAction("stripe") },
      { name: "Square", desc: "In-person & online card payments with Square.",
        badge: keyBadge("square"), action: keyAction("square") },
      { name: "SumUp", desc: "Card terminal & hosted checkout with SumUp.",
        badge: keyBadge("sumup"), action: keyAction("sumup") } ] },
    { group: "Accounting", cards: [
      { name: "QuickBooks Online", desc: "Two-way sync of invoices, payments and contacts.",
        badge: data.qbo?.connected ? { text: "Connected", tone: "on" } : { text: data.qbo?.configured ? "Not connected" : "Not configured", tone: "off" },
        hint: data.qbo?.configured ? undefined : "Set QBO_CLIENT_ID / SECRET on the server" },
      { name: "Xero", desc: "Sync invoices, payments and clients both ways.",
        badge: data.xero?.connected ? { text: "Connected", tone: "on" } : { text: data.xero?.configured ? "Not connected" : "Not configured", tone: "off" },
        hint: data.xero?.configured ? undefined : "Set XERO_CLIENT_ID / SECRET on the server" } ] },
    { group: "Communication", cards: [
      { name: "Email (Gmail / Outlook)", desc: "Send from your own mailbox (OAuth). SMTP fallback available.",
        badge: emailOn ? { text: "Connected", tone: "on" } : keyBadge("smtp"),
        hint: emailOn ? `${data.email.accounts.length} account(s) connected` : "Connect Gmail/Outlook, or add SMTP keys",
        action: keyAction("smtp") },
      { name: "SMS & WhatsApp (Twilio)", desc: "Two-way messages; inbound webhook threads them to jobs.",
        badge: keyBadge("twilio"), action: keyAction("twilio") },
      { name: "Calls / VoIP", desc: "Click-to-call and an inbound-call feed through your Twilio account.",
        badge: telOn ? { text: telProv === "simulated" ? "Simulated" : telProv === "twilio" && data.tel?.twilio?.test_mode ? "Twilio: test mode" : `Live: ${telProv}`, tone: "on" } : { text: "Off", tone: "off" }, hint: telOn ? undefined : "Set up below in Calls & telephony" },
      { name: "Chat inbox (Messenger / Instagram / WhatsApp)", desc: "Meta channels land in one inbox, linked to clients & jobs.",
        badge: keyBadge("meta"), action: keyAction("meta") } ] },
    { group: "E-commerce", cards: [
      { name: "Shopify / WooCommerce", desc: "Sync products, orders and customers from your store.",
        badge: ecomN ? { text: `${ecomN} store(s)`, tone: "on" } : { text: "Not connected", tone: "off" },
        action: { label: "Manage stores", onClick: () => goSection("ecommerce") } } ] },
    { group: "Automation & developer", cards: [
      { name: "Zapier / Make / n8n", desc: "Trigger thousands of apps from Tekly events — no code.",
        badge: { text: "Available", tone: "on" }, hint: "Point them at your Webhooks (below)" },
      { name: "Public REST API", desc: "Build custom integrations against orders, contacts, inventory & more.",
        badge: { text: "Available", tone: "on" }, hint: "Create an API key (below)" },
      { name: "Workflow AI (Anthropic)", desc: "Order summaries, estimate drafting, reply suggestions.",
        badge: { text: "Server-keyed", tone: "neutral" }, hint: "Set ANTHROPIC_API_KEY on the server" } ] },
    { group: "On your website", cards: [
      { name: "Repair-status widget", desc: "Embed a status checker on your site — customers look up a job by order # + phone.",
        badge: { text: "Available", tone: "on" }, action: { label: "Copy embed code", onClick: copyWidget } } ] },
  ];
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      <div className="px-5 pt-4 pb-1">
        <h3 className="text-sm font-bold text-ink-primary">Integrations</h3>
        <p className="text-[12px] text-ink-tertiary mt-0.5">Connect Tekly to the tools you already use. Credential-gated connectors go live the moment their keys are set on the server.</p>
      </div>
      <div className="p-4 space-y-5">
        {groups.map((g) => (
          <div key={g.group}>
            <p className="text-[11px] font-bold text-ink-tertiary px-1 pb-2">{g.group}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {g.cards.map((c) => (
                <div key={c.name} className="border border-slate-200/90 rounded-xl p-3.5 flex flex-col gap-1.5 bg-white hover:border-slate-300 transition-colors">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-[13px] font-bold text-ink-primary leading-tight">{c.name}</span>
                    <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold whitespace-nowrap ${B[c.badge.tone]}`}>{c.badge.text}</span>
                  </div>
                  <p className="text-[12px] text-ink-secondary leading-snug">{c.desc}</p>
                  {c.hint && <p className="text-[11px] text-ink-tertiary mt-0.5">{c.hint}</p>}
                  {c.action && <button type="button" onClick={c.action.onClick} className="mt-1 self-start px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-[11px] font-semibold text-ink-primary">{c.action.label}</button>}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      {keyProv && <CredDrawer provider={keyProv} status={creds[keyProv]}
        onClose={() => setKeyProv(null)}
        onSaved={() => { setKeyProv(null); reload && reload(); }} />}
    </div>
  );
}

/* ── Per-shop integration credentials setup drawer (144jn) ── */
function CredDrawer({ provider, status, onClose, onSaved }: {
  provider: string; status: any; onClose: () => void; onSaved: () => void;
}) {
  const fields: any[] = status?.fields || [];
  const [vals, setVals] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const label: Record<string, string> = {
    stripe: "Stripe", square: "Square", sumup: "SumUp", twilio: "Twilio (SMS / WhatsApp)",
    smtp: "SMTP email", meta: "Meta (Messenger / Instagram / WhatsApp Cloud)",
  };
  async function save() {
    setBusy(true); setErr(null);
    try {
      // only send fields the user actually typed; blanks keep the stored value
      const data: Record<string, string> = {};
      for (const [k, v] of Object.entries(vals)) if (v.trim() !== "") data[k] = v.trim();
      await api.put("/integrations/credentials", { provider, data });
      onSaved();
    } catch (e: any) { setErr(e.message || "Save failed"); } finally { setBusy(false); }
  }
  async function clearKeys() {
    if (!(await confirmDialog("Remove this shop's own keys? It will fall back to the default.", { danger: true, confirmLabel: "Remove" }))) return;
    setBusy(true);
    try { await api.del(`/integrations/credentials/${provider}`); onSaved(); }
    catch (e: any) { setErr(e.message || "Failed"); } finally { setBusy(false); }
  }
  return createPortal(
    <div className="fixed inset-0 z-[90] flex justify-end" onMouseDown={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white h-full shadow-drawer flex flex-col animate-[slideIn_.15s_ease]" onMouseDown={(e) => e.stopPropagation()}>
        <style>{`@keyframes slideIn{from{transform:translateX(24px);opacity:.6}to{transform:none;opacity:1}}`}</style>
        <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-bold text-ink-primary">{label[provider] || provider} keys</h3>
            <p className="text-[11px] text-ink-tertiary mt-0.5">These keys are used only by your shop — never shared with other companies.</p>
          </div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 grid place-items-center rounded-lg hover:bg-slate-100"><M n="close" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-3">
          {fields.map((f) => (
            <div key={f.key}>
              <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">{f.label}</label>
              <input
                type={f.secret ? "password" : "text"}
                defaultValue={f.secret ? "" : (f.value || "")}
                placeholder={f.set ? (f.secret ? "•••• (leave blank to keep)" : f.value) : ""}
                onChange={(e) => setVals((s) => ({ ...s, [f.key]: e.target.value }))}
                className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary" />
            </div>
          ))}
          {err && <div className="text-xs text-rose-600">{err}</div>}
        </div>
        <div className="px-5 py-3 border-t border-slate-100 flex items-center justify-between">
          {status?.source === "org"
            ? <button onClick={clearKeys} disabled={busy} className="text-[12px] font-semibold text-rose-600 hover:underline">Remove keys</button>
            : <span />}
          <div className="flex gap-2">
            <button onClick={onClose} className="px-3 py-2 rounded-lg text-xs font-semibold text-ink-secondary hover:bg-slate-100">Cancel</button>
            <button onClick={save} disabled={busy} className="px-3.5 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">{busy ? "Saving…" : "Save keys"}</button>
          </div>
        </div>
      </div>
    </div>, document.body);
}

/* ── API keys → ./ApiKeysCard (WS-C: scoped keys, per-key limits) ── */
function ApiKeysSection() { return <ApiKeysCard Card={SectionCard} />; }

/* ── Webhooks (create / delete) → /webhooks ── */
function WebhooksCard() {
  const [hooks, setHooks] = useState<any[] | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [nf, setNf] = useState({ url: "", events: "*" }); const [busy, setBusy] = useState(false);
  const load = () => api.get<any>("/webhooks").then((r) => { setHooks(r.items || []); setEvents(r.available_events || []); }).catch(() => setHooks([]));
  useEffect(() => { load(); }, []);
  async function create() { if (!nf.url.trim()) return; setBusy(true); try { await api.post("/webhooks", { url: nf.url.trim(), events: nf.events }); setNf({ url: "", events: nf.events }); load(); } catch (e: any) { alertDialog(e.message); } finally { setBusy(false); } }
  async function remove(id: string) { if (!(await confirmDialog("Delete this webhook?", { danger: true, confirmLabel: "Delete" }))) return; try { await api.del(`/webhooks/${id}`); load(); } catch (e: any) { alertDialog(e.message); } }
  return (
    <SectionCard title="Webhooks" count={hooks?.length ?? undefined}>
      <div className="p-4 flex items-end gap-2">
        <div className="flex-1"><label className={lbl}>Endpoint URL</label><input value={nf.url} onChange={(e) => setNf({ ...nf, url: e.target.value })} placeholder="https://example.com/hook" className={field} /></div>
        <div><label className={lbl}>Events</label><select value={nf.events} onChange={(e) => setNf({ ...nf, events: e.target.value })} className={field}><option value="*">All events</option>{events.map((e) => <option key={e} value={e}>{e}</option>)}</select></div>
        <button onClick={create} disabled={busy} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">Create</button>
      </div>
      <SecTable rows={hooks} onDelete={(w) => remove(w.id)} cols={[["URL", (w) => w?.url || "—"], ["Events", (w) => w?.events || "*"], ["Active", (w) => (w?.active === false ? "Off" : "On")]]} />
    </SectionCard>
  );
}

function SectionCard({ title, count, onAdd, children }: { title: string; count?: number; onAdd?: () => void; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      <div className="p-3.5 border-b border-slate-100 flex items-center justify-between">
        <span className="text-xs font-bold text-ink-primary ">{title}{count != null && <span className="ml-2 text-[11px] font-bold bg-slate-100 text-ink-secondary px-2 py-0.5 rounded-full">{count}</span>}</span>
        {onAdd && <button onClick={onAdd} className="text-primary text-xs font-semibold hover:underline flex items-center gap-1"><M n="add" c="text-sm" />Add</button>}
      </div>
      {children}
    </div>
  );
}

// ── RBAC role editor (permission matrix) ───────────────────────────────
// ── Employee editor (Orderry-style) ────────────────────────────────────
function EmployeeEditorDrawer({ emp, roles, onClose, onSaved }: { emp: any | null; roles: any[]; onClose: () => void; onSaved: (m?: string) => void }) {
  const isNew = !emp?.id;
  const [f, setF] = useState<any>(emp || { name: "", email: "", active: true, wage_type: "fixed", track_job_hours: true, track_attendance: true });
  const [tab, setTab] = useState<"general" | "access" | "payroll" | "security">("general");
  const [locations, setLocations] = useState<any[]>([]);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [pw, setPw] = useState("");
  const { me } = useMe();
  const isSelf = !!emp?.id && emp.id === me?.id;
  // Your own email or password needs your current password (the API refuses without it).
  const askCurrent = () => promptDialog("Enter your current password to confirm.", { title: "Confirm it's you", type: "password", confirmLabel: "Confirm" });
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  useEffect(() => { api.get<any>("/staff/locations").then((r) => setLocations(r.items || r || [])).catch(() => setLocations([])); }, []);

  async function save() {
    if (!f.name?.trim()) { setErr("Name is required"); return; }
    if (!f.email?.trim()) { setErr("Email is required"); return; }
    setBusy(true); setErr(null);
    const body: any = {
      name: f.name.trim(), email: f.email.trim(), phone: f.phone || null,
      position: f.position || null, public_name: f.public_name || null, tax_id: f.tax_id || null,
      hired_at: f.hired_at || null, note: f.note || null,
      role_id: f.role_id || null, location_id: f.location_id || null,
      wage_type: f.wage_type || "fixed", wage_value: Number(f.wage_value) || 0,
      hourly_basis: f.hourly_basis === "attendance" ? "attendance" : "labour",
      track_job_hours: f.track_job_hours !== false, track_attendance: f.track_attendance !== false,
    };
    try {
      let current_password: string | undefined;
      if (isSelf && (f.email || "").trim().toLowerCase() !== (emp.email || "").trim().toLowerCase()) {
        const p = await askCurrent(); if (!p) { setBusy(false); return; }
        current_password = p;
      }
      if (isNew) await api.post("/staff/employees", body);
      else await api.patch(`/staff/employees/${emp.id}`, { ...body, active: f.active !== false, ...(current_password ? { current_password } : {}) });
      onSaved(isNew ? "Employee added" : "Employee saved");
    } catch (e: any) { setErr(e?.message || "Could not save"); setBusy(false); }
  }
  async function resetPassword() {
    if (!pw.trim() || !emp?.id) return;
    let current_password: string | undefined;
    if (isSelf) { const p = await askCurrent(); if (!p) return; current_password = p; }
    try { await api.post(`/staff/employees/${emp.id}/password`, { password: pw.trim(), ...(current_password ? { current_password } : {}) }); setPw(""); onSaved("Password updated"); }
    catch (e: any) { setErr(e?.message || "Failed"); }
  }
  async function sendReset() { try { await api.post(`/staff/employees/${emp.id}/send-reset`, {}); onSaved("Reset link emailed"); } catch (e: any) { setErr(e?.message || "Failed"); } }
  async function resetMfa() { try { await api.post(`/staff/employees/${emp.id}/reset-mfa`, {}); onSaved("2FA reset"); } catch (e: any) { setErr(e?.message || "Failed"); } }

  const lblc = "block text-xs font-medium text-ink-secondary mb-1.5";
  const fld = "w-full bg-white border border-slate-200 rounded-xl px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/10";
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[540px] max-w-full bg-white h-full shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <div className="px-5 pt-5 pb-3">
          <div className="flex items-center gap-3">
            {isNew ? <span className="w-11 h-11 rounded-full bg-slate-100 text-ink-tertiary grid place-items-center flex-shrink-0"><M n="person_add" c="text-lg" /></span> : <Avatar name={f.name || "?"} size={44} />}
            <div className="flex-1 min-w-0">
              <div className="text-lg font-semibold text-ink-primary truncate">{isNew ? "Add a teammate" : (f.name || "Teammate")}</div>
              <div className="text-[11px] text-ink-tertiary truncate">{isNew ? "They get an account to sign in with" : [f.position, f.email].filter(Boolean).join(" · ")}</div>
            </div>
            {!isNew && f.active === false && <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md bg-rose-50 text-rose-600">Blocked</span>}
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary flex-shrink-0"><M n="close" c="text-base" /></button>
          </div>
          <div className="mt-4 inline-flex rounded-xl bg-slate-100 p-1">
            {([["general", "Profile"], ["access", "Access"], ["payroll", "Pay"], ...(isNew ? [] : [["security", "Sign-in"]] as [string, string][])] as [string, string][]).map(([id, label]) => (
              <button key={id} onClick={() => setTab(id as any)} aria-pressed={tab === id}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${tab === id ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{label}</button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

          {tab === "general" && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div><label className={lblc}>Full name</label><input value={f.name || ""} onChange={(e) => set("name", e.target.value)} className={fld} /></div>
              <div><label className={lblc}>Position</label><input value={f.position || ""} onChange={(e) => set("position", e.target.value)} className={fld} placeholder="Technician" /></div>
              <div><label className={lblc}>Email</label><input value={f.email || ""} onChange={(e) => set("email", e.target.value)} className={fld} /></div>
              <div><label className={lblc}>Phone</label><input value={f.phone || ""} onChange={(e) => set("phone", e.target.value)} className={`${fld} tabular-nums`} /></div>
              <div><label className={lblc}>Name on documents</label><input value={f.public_name || ""} onChange={(e) => set("public_name", e.target.value)} className={fld} /></div>
              <div><label className={lblc}>Tax ID</label><input value={f.tax_id || ""} onChange={(e) => set("tax_id", e.target.value)} className={`${fld} font-mono`} /></div>
              <div><label className={lblc}>Hired date</label><DateField mode="date" value={(f.hired_at || "").slice(0, 10)} onChange={(v) => set("hired_at", v)} /></div>
            </div>
            <div><label className={lblc}>Note</label><textarea value={f.note || ""} onChange={(e) => set("note", e.target.value)} rows={2} className={fld} /></div>
          </div>
          )}

          {tab === "access" && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div><label className={lblc}>Role</label>
                <select value={f.role_id || ""} onChange={(e) => set("role_id", e.target.value)} className={`${fld} appearance-none`}><option value="">— No role —</option>{roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></div>
              <div><label className={lblc}>Home location</label>
                <select value={f.location_id || ""} onChange={(e) => set("location_id", e.target.value)} className={`${fld} appearance-none`}><option value="">All locations</option>{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
            </div>
            {!isNew && (
              <label className="flex items-center justify-between py-1 cursor-pointer">
                <div><div className="text-xs font-semibold text-ink-primary">Active</div><div className="text-[11px] text-ink-tertiary">Blocked employees can't sign in.</div></div>
                <button type="button" onClick={() => set("active", !(f.active !== false))} className={`w-10 h-6 rounded-full transition-colors flex-shrink-0 ${f.active !== false ? "bg-primary" : "bg-slate-300"} relative`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${f.active !== false ? "left-[18px]" : "left-0.5"}`} /></button>
              </label>
            )}
          </div>
          )}

          {tab === "payroll" && (
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <div><label className={lblc}>Wage type</label>
                <select value={f.wage_type || "fixed"} onChange={(e) => set("wage_type", e.target.value)} className={`${fld} appearance-none`}><option value="fixed">Fixed salary</option><option value="hourly">Hourly</option></select></div>
              <div><label className={lblc}>Wage value</label><input type="number" value={f.wage_value ?? ""} onChange={(e) => set("wage_value", e.target.value)} className={`${fld} font-mono`} /></div>
              <div><label className={lblc}>Pay hours from</label>
                <select value={f.hourly_basis === "attendance" ? "attendance" : "labour"} onChange={(e) => set("hourly_basis", e.target.value)} className={`${fld} appearance-none`}><option value="labour">Approved job timesheets</option><option value="attendance">Shop clock-in / out</option></select></div>
            </div>
            <div className="divide-y divide-slate-100">
              <label className="flex items-center justify-between py-2 cursor-pointer"><span className="text-xs font-semibold text-ink-primary">Work-order / job timer</span>
                <button type="button" onClick={() => set("track_job_hours", !(f.track_job_hours !== false))} className={`w-10 h-6 rounded-full flex-shrink-0 ${f.track_job_hours !== false ? "bg-primary" : "bg-slate-300"} relative`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${f.track_job_hours !== false ? "left-[18px]" : "left-0.5"}`} /></button></label>
              <label className="flex items-center justify-between py-2 cursor-pointer"><span className="text-xs font-semibold text-ink-primary">Shop attendance clock</span>
                <button type="button" onClick={() => set("track_attendance", !(f.track_attendance !== false))} className={`w-10 h-6 rounded-full flex-shrink-0 ${f.track_attendance !== false ? "bg-primary" : "bg-slate-300"} relative`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${f.track_attendance !== false ? "left-[18px]" : "left-0.5"}`} /></button></label>
            </div>
          </div>

          )}

          {tab === "security" && !isNew && (
            <div className="space-y-4">
                <div className="flex items-end gap-2">
                <div className="flex-1"><label className={lblc}>Set new password</label><input value={pw} onChange={(e) => setPw(e.target.value)} type="text" placeholder="Min 8 chars" className={fld} /></div>
                <button onClick={resetPassword} disabled={!pw.trim()} className="px-3 py-2 rounded-lg border border-slate-200 text-xs font-bold text-ink-secondary hover:bg-slate-50 disabled:opacity-50">Set</button>
              </div>
              <div className="flex gap-2">
                <button onClick={sendReset} className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50">Email reset link</button>
                <button onClick={resetMfa} className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50">Reset 2FA</button>
              </div>
              {!isSelf && (
                <div className="border-t border-slate-100 pt-4">
                  <label className={lblc}>Signed-in devices</label>
                  <DeviceList base={`/staff/employees/${emp.id}/sessions`} person={(f.name || emp.name || "This person").split(" ")[0]} />
                </div>
              )}
            </div>
          )}
        </div>
        <div className="px-5 py-4 border-t border-slate-100 flex items-center gap-2">
          <button onClick={onClose} className="ml-auto px-4 py-2 rounded-xl border border-slate-200 text-ink-secondary hover:bg-slate-50 font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-5 py-2 rounded-xl bg-ink-primary text-white font-semibold text-xs hover:opacity-90 disabled:opacity-60">{busy ? "Saving…" : isNew ? "Add teammate" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}

// ── Payroll commission-event rule editor (Orderry-style) ───────────────
function PayrollRuleDrawer({ rule, employees, statuses, onClose, onSaved }: { rule: any | null; employees: any[]; statuses: any[]; onClose: () => void; onSaved: (m?: string) => void }) {
  const isNew = !rule?.id;
  const [f, setF] = useState<any>(rule || { name: "Commission", event: "order_closed_success", calc_type: "percent", percent_basis: "total", amount: 10, coefficient: 1, calc_timing: "on_close", term_condition: "any", rule_level: "generic", cancel_if_deleted: true });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));

  async function save() {
    if (!f.name?.trim()) { setErr("Rule name is required"); return; }
    if (f.calc_timing === "on_status" && !f.trigger_status_key) { setErr("Pick the status that triggers this rule"); return; }
    setBusy(true); setErr(null);
    const body = {
      employee_id: f.employee_id || null, name: f.name.trim(), event: f.event,
      calc_type: f.calc_type, percent_basis: f.percent_basis,
      amount: Number(f.amount) || 0, coefficient: Number(f.coefficient) || 1,
      category: f.category || null, wo_type_id: f.wo_type_id || null,
      calc_timing: f.calc_timing, trigger_status_key: f.calc_timing === "on_status" ? f.trigger_status_key : null,
      term_condition: f.term_condition, cancel_if_deleted: f.cancel_if_deleted !== false,
      rule_level: f.rule_level, product_id: f.product_id || null, service_id: f.service_id || null,
    };
    try {
      if (isNew) await api.post("/payroll/rules", body);
      else await api.put(`/payroll/rules/${rule.id}`, body);
      onSaved(isNew ? "Rule added" : "Rule saved");
    } catch (e: any) { setErr(e?.message || "Could not save"); setBusy(false); }
  }
  async function del() {
    if (!rule?.id || !(await confirmDialog("Delete this payroll rule?", { danger: true, confirmLabel: "Delete" }))) return;
    try { await api.del(`/payroll/rules/${rule.id}`); onSaved("Rule deleted"); } catch (e: any) { setErr(e?.message || "Failed"); }
  }

  const lblc = "block text-[11px] font-bold text-ink-tertiary  mb-1";
  const fld = "glass-input w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg";
  const isPct = f.calc_type === "percent";
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[520px] max-w-full bg-white h-full shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
          <div><span className="text-[11px] font-bold text-primary block">Team › Payroll rules</span><h2 className="font-bold text-ink-primary">{isNew ? "New commission rule" : f.name}</h2></div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

          <div className="bg-white rounded-xl border border-slate-200/90 shadow-clay-card p-4 space-y-3">
            <h3 className="text-xs font-bold text-ink-primary">Trigger</h3>
            <div><label className={lblc}>Rule name</label><input value={f.name || ""} onChange={(e) => set("name", e.target.value)} className={fld} /></div>
            <div><label className={lblc}>Pays when this happens (event)</label>
              <select value={f.event} onChange={(e) => set("event", e.target.value)} className={fld}>
                {PAYROLL_EVENTS.map((ev) => <option key={ev} value={ev}>{PAYROLL_EVENT_LABELS[ev]}</option>)}
              </select></div>
            <div><label className={lblc}>Applies to</label>
              <select value={f.employee_id || ""} onChange={(e) => set("employee_id", e.target.value)} className={fld}>
                <option value="">All employees (or by role default)</option>
                {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
              </select></div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200/90 shadow-clay-card p-4 space-y-3">
            <h3 className="text-xs font-bold text-ink-primary">Amount</h3>
            <div className="grid grid-cols-2 gap-3">
              <div><label className={lblc}>Type</label>
                <select value={f.calc_type} onChange={(e) => set("calc_type", e.target.value)} className={fld}><option value="percent">Percentage</option><option value="flat">Flat amount</option></select></div>
              <div><label className={lblc}>{isPct ? "Percent (%)" : "Amount"}</label><input type="number" value={f.amount ?? ""} onChange={(e) => set("amount", e.target.value)} className={`${fld} font-mono`} /></div>
              {isPct && (
                <div><label className={lblc}>Percentage of</label>
                  <select value={f.percent_basis} onChange={(e) => set("percent_basis", e.target.value)} className={fld}><option value="total">Line / order total</option><option value="margin">Gross profit (margin)</option></select></div>
              )}
              <div><label className={lblc}>Coefficient (×)</label><input type="number" step="0.1" value={f.coefficient ?? ""} onChange={(e) => set("coefficient", e.target.value)} className={`${fld} font-mono`} /></div>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-slate-200/90 shadow-clay-card p-4 space-y-3">
            <h3 className="text-xs font-bold text-ink-primary">Conditions</h3>
            <div className="grid grid-cols-2 gap-3">
              <div><label className={lblc}>Accrue</label>
                <select value={f.calc_timing} onChange={(e) => set("calc_timing", e.target.value)} className={fld}>{PAYROLL_TIMINGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
              {f.calc_timing === "on_status" && (
                <div><label className={lblc}>At status</label>
                  <select value={f.trigger_status_key || ""} onChange={(e) => set("trigger_status_key", e.target.value)} className={fld}><option value="">— pick —</option>{statuses.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}</select></div>
              )}
              <div><label className={lblc}>Only for orders</label>
                <select value={f.term_condition} onChange={(e) => set("term_condition", e.target.value)} className={fld}>{PAYROLL_TERMS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
              <div><label className={lblc}>Level</label>
                <select value={f.rule_level} onChange={(e) => set("rule_level", e.target.value)} className={fld}>{PAYROLL_LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
              {f.rule_level === "category" && (
                <div className="col-span-2"><label className={lblc}>Category name</label><input value={f.category || ""} onChange={(e) => set("category", e.target.value)} className={fld} placeholder="e.g. Screens" /></div>
              )}
              {(f.rule_level === "product" || f.rule_level === "service") && (
                <div className="col-span-2"><label className={lblc}>{f.rule_level === "product" ? "Product" : "Service"} ID</label><input value={(f.rule_level === "product" ? f.product_id : f.service_id) || ""} onChange={(e) => set(f.rule_level === "product" ? "product_id" : "service_id", e.target.value)} className={`${fld} font-mono`} placeholder="Paste the catalog item ID" /></div>
              )}
            </div>
            <label className="flex items-center justify-between py-1 cursor-pointer">
              <div><div className="text-xs font-semibold text-ink-primary">Reverse if the order is deleted</div><div className="text-[11px] text-ink-tertiary">Claws back the commission when the source record is removed.</div></div>
              <button type="button" onClick={() => set("cancel_if_deleted", !(f.cancel_if_deleted !== false))} className={`w-10 h-6 rounded-full flex-shrink-0 relative transition-colors ${f.cancel_if_deleted !== false ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${f.cancel_if_deleted !== false ? "left-[18px]" : "left-0.5"}`} /></button>
            </label>
          </div>
        </div>
        <div className="px-5 py-4 border-t border-slate-100 flex items-center justify-between gap-2 bg-slate-50/60">
          {!isNew ? <button onClick={del} className="px-3 py-2 rounded-lg text-xs font-bold text-rose-600 hover:bg-rose-50">Delete</button> : <span />}
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs">Cancel</button>
            <button onClick={save} disabled={busy} className="px-5 py-2 rounded-lg bg-primary text-white font-bold text-xs shadow-clay-sm hover:brightness-110 disabled:opacity-60">{busy ? "Saving…" : isNew ? "Add rule" : "Save"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Form custom-fields builder ─────────────────────────────────────────
function FormFieldsDrawer({ type, onClose, onChanged }: { type: any; onClose: () => void; onChanged: () => void }) {
  const [fields, setFields] = useState<any[] | null>(null);
  const [nf, setNf] = useState<any>({ field_label: "", field_type: "text", required: false, show_when_field_key: "", show_when_op: "is", show_when_value: "", upsell_on: false, upsell_comparator: "lt", upsell_value: "", upsell_threshold: "", upsell_price: "", gate_on: false, gate_status_key: "", gate_comparator: "is", gate_value: "", gate_threshold: "" });
  const [busy, setBusy] = useState(false);
  const [statuses, setStatuses] = useState<any[]>([]);
  const load = () => api.get<{ items: any[] }>(`/forms/types/${type.id}/fields`).then((r) => setFields(r.items || [])).catch(() => setFields([]));
  useEffect(() => { load(); api.get<any>("/statuses").then((r) => setStatuses(r.items || [])).catch(() => {}); }, []);
  async function add() {
    if (!nf.field_label.trim()) return; setBusy(true);
    try {
      const cond = nf.show_when_field_key ? { show_when_field_key: nf.show_when_field_key, show_when_op: nf.show_when_op, show_when_value: nf.show_when_op === "filled" ? null : nf.show_when_value } : {};
      const numeric = nf.upsell_comparator === "lt" || nf.upsell_comparator === "gt";
      const rule = nf.upsell_on ? { upsell_kind: "custom", upsell_price: parseFloat(nf.upsell_price) || 0, upsell_comparator: nf.upsell_comparator, upsell_value: numeric ? null : nf.upsell_value, upsell_threshold: numeric ? (parseFloat(nf.upsell_threshold) || 0) : null } : {};
      const gnum = nf.gate_comparator === "lt" || nf.gate_comparator === "gt";
      const gate = (nf.gate_on && nf.gate_status_key) ? { gate_status_key: nf.gate_status_key, gate_comparator: nf.gate_comparator, gate_value: gnum ? null : nf.gate_value, gate_threshold: gnum ? (parseFloat(nf.gate_threshold) || 0) : null } : {};
      await api.post(`/forms/types/${type.id}/fields`, { field_label: nf.field_label.trim(), field_type: nf.field_type, required: nf.required, lock_after_create: false, ...cond, ...rule, ...gate });
      setNf({ field_label: "", field_type: "text", required: false, show_when_field_key: "", show_when_op: "is", show_when_value: "", upsell_on: false, upsell_comparator: "lt", upsell_value: "", upsell_threshold: "", upsell_price: "", gate_on: false, gate_status_key: "", gate_comparator: "is", gate_value: "", gate_threshold: "" }); load(); onChanged();
    }
    finally { setBusy(false); }
  }
  async function del(id: string) { try { await api.del(`/forms/fields/${id}`); load(); onChanged(); } catch { /* ignore */ } }
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[480px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <div><h2 className="font-bold text-ink-primary">Custom fields</h2><p className="text-[11px] text-ink-tertiary">{type.name || type.label}</p></div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <div className="rounded-xl border border-slate-200 divide-y divide-slate-100">
            {!fields && <div className="p-4 text-xs text-ink-tertiary">Loading…</div>}
            {fields?.length === 0 && <div className="p-4 text-xs text-ink-tertiary">No custom fields yet.</div>}
            {fields?.map((f) => (
              <div key={f.id} className="flex items-center justify-between px-4 py-2.5 text-xs">
                <div><span className="font-bold text-ink-primary">{f.field_label}</span><span className="ml-2 text-[11px] bg-slate-100 text-ink-tertiary px-1.5 py-0.5 rounded">{f.field_type}</span>{f.required && <span className="ml-1 text-[11px] text-rose-600 font-semibold">required</span>}{f.show_when_field_key && <span className="ml-1 text-[11px] text-primary font-semibold">conditional</span>}{f.upsell_comparator && <span className="ml-1 text-[11px] text-amber-700 font-semibold">upsell</span>}{f.gate_status_key && <span className="ml-1 text-[11px] text-rose-600 font-semibold">gate</span>}</div>
                <button onClick={() => del(f.id)} className="text-rose-600 font-semibold hover:underline">Delete</button>
              </div>
            ))}
          </div>
          <div className="rounded-xl border border-slate-200 p-4 space-y-3">
            <div className="text-[11px] font-bold text-ink-tertiary">Add a field</div>
            <input value={nf.field_label} onChange={(e) => setNf({ ...nf, field_label: e.target.value })} placeholder="Field label" className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary" />
            <div className="flex items-center gap-3">
              <select value={nf.field_type} onChange={(e) => setNf({ ...nf, field_type: e.target.value })} className="bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 capitalize">
                {/* "bool" is the server's yes/no type — the old "checkbox" option was rejected with a 400 */}
                {[["text", "Short text"], ["textarea", "Long text"], ["number", "Number"], ["select", "Pick one"], ["multiselect", "Pick several"], ["bool", "Yes / no"], ["date", "Date"], ["link", "Link"]].map(([t, l]) => <option key={t} value={t}>{l}</option>)}
              </select>
              <label className="flex items-center gap-1.5 text-xs text-ink-secondary"><input type="checkbox" checked={nf.required} onChange={(e) => setNf({ ...nf, required: e.target.checked })} className="w-4 h-4 rounded border-slate-300 text-primary" />Required</label>
              <button onClick={add} disabled={busy} className="ml-auto px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-60">Add</button>
            </div>
            {/* When to show */}
            <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary pt-1 border-t border-slate-100">
              <span className="font-semibold"><M n="visibility" c="text-[13px] align-middle" /> Show only when</span>
              <select value={nf.show_when_field_key} onChange={(e) => setNf({ ...nf, show_when_field_key: e.target.value })} className="bg-white border border-slate-200 rounded-lg px-2 py-1.5">
                <option value="">always</option>
                {(fields || []).map((f) => <option key={f.id} value={f.field_key}>{f.field_label}</option>)}
              </select>
              {nf.show_when_field_key && <>
                <select value={nf.show_when_op} onChange={(e) => setNf({ ...nf, show_when_op: e.target.value })} className="bg-white border border-slate-200 rounded-lg px-2 py-1.5">
                  <option value="is">is</option><option value="is_not">is not</option><option value="gt">&gt;</option><option value="lt">&lt;</option><option value="filled">is filled</option>
                </select>
                {nf.show_when_op !== "filled" && <input value={nf.show_when_value} onChange={(e) => setNf({ ...nf, show_when_value: e.target.value })} placeholder="value" className="bg-white border border-slate-200 rounded-lg px-2 py-1.5 w-24" />}
              </>}
            </div>
            {/* What it does — upsell rule (shared engine with checklist) */}
            <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary pt-1 border-t border-slate-100">
              <label className="font-semibold flex items-center gap-1"><input type="checkbox" checked={nf.upsell_on} onChange={(e) => setNf({ ...nf, upsell_on: e.target.checked })} className="w-4 h-4 rounded border-slate-300 text-primary" /><M n="sell" c="text-[13px] align-middle" /> Draft upsell when value</label>
              {nf.upsell_on && <>
                <select value={nf.upsell_comparator} onChange={(e) => setNf({ ...nf, upsell_comparator: e.target.value })} className="bg-white border border-slate-200 rounded-lg px-2 py-1.5">
                  <option value="lt">&lt;</option><option value="gt">&gt;</option><option value="is">is</option><option value="is_not">is not</option>
                </select>
                {(nf.upsell_comparator === "lt" || nf.upsell_comparator === "gt")
                  ? <input value={nf.upsell_threshold} onChange={(e) => setNf({ ...nf, upsell_threshold: e.target.value })} placeholder="80" className="bg-white border border-slate-200 rounded-lg px-2 py-1.5 w-16" />
                  : <input value={nf.upsell_value} onChange={(e) => setNf({ ...nf, upsell_value: e.target.value })} placeholder="value" className="bg-white border border-slate-200 rounded-lg px-2 py-1.5 w-24" />}
                <span>· price</span>
                <input value={nf.upsell_price} onChange={(e) => setNf({ ...nf, upsell_price: e.target.value })} placeholder="59" className="bg-white border border-slate-200 rounded-lg px-2 py-1.5 w-16" />
              </>}
            </div>
            {/* Gate a status — a value can block entering a status */}
            <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary pt-1 border-t border-slate-100">
              <label className="font-semibold flex items-center gap-1"><input type="checkbox" checked={nf.gate_on} onChange={(e) => setNf({ ...nf, gate_on: e.target.checked })} className="w-4 h-4 rounded border-slate-300 text-primary" /><M n="block" c="text-[13px] align-middle" /> Block status</label>
              {nf.gate_on && <>
                <select value={nf.gate_status_key} onChange={(e) => setNf({ ...nf, gate_status_key: e.target.value })} className="bg-white border border-slate-200 rounded-lg px-2 py-1.5">
                  <option value="">choose…</option>{statuses.map((s) => <option key={s.id} value={s.key}>{s.name}</option>)}
                </select>
                <span>when value</span>
                <select value={nf.gate_comparator} onChange={(e) => setNf({ ...nf, gate_comparator: e.target.value })} className="bg-white border border-slate-200 rounded-lg px-2 py-1.5">
                  <option value="is">is</option><option value="is_not">is not</option><option value="lt">&lt;</option><option value="gt">&gt;</option>
                </select>
                {(nf.gate_comparator === "lt" || nf.gate_comparator === "gt")
                  ? <input value={nf.gate_threshold} onChange={(e) => setNf({ ...nf, gate_threshold: e.target.value })} placeholder="0" className="bg-white border border-slate-200 rounded-lg px-2 py-1.5 w-16" />
                  : <input value={nf.gate_value} onChange={(e) => setNf({ ...nf, gate_value: e.target.value })} placeholder="value" className="bg-white border border-slate-200 rounded-lg px-2 py-1.5 w-24" />}
              </>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SecTable({ rows, cols, onRowClick, onDelete }: { rows: any[] | null | undefined; cols: [string, (o: any) => string][]; onRowClick?: (r: any) => void; onDelete?: (r: any) => void }) {
  if (rows == null) return <div className="p-5 text-xs text-ink-tertiary">Loading…</div>;
  if (rows.length === 0) return <div className="p-5 text-xs text-ink-tertiary">Nothing here yet.</div>;
  return (
    <table className="w-full text-left border-collapse text-xs">
      <thead><tr className="bg-slate-50/60 border-b border-slate-100 text-ink-tertiary text-[11px] font-bold ">
        {cols.map(([h]) => <th key={h} className="py-2.5 px-4">{h}</th>)}
        {(onRowClick || onDelete) && <th className="py-2.5 px-4 text-right">Actions</th>}
      </tr></thead>
      <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
        {rows.map((r, i) => (
          <tr key={r?.id || i} className={`hover:bg-blue-50/30 ${onRowClick ? "cursor-pointer" : ""}`} onClick={onRowClick ? () => onRowClick(r) : undefined}>
            {cols.map(([h, fn], j) => <td key={h} className={`py-2.5 px-4 ${j === 0 ? "font-bold text-ink-primary" : ""}`}>{fn(r)}</td>)}
            {(onRowClick || onDelete) && (
              <td className="py-2.5 px-4 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                {onRowClick && <button onClick={() => onRowClick(r)} className="text-primary font-semibold hover:underline mr-3">Edit</button>}
                {onDelete && <button onClick={() => onDelete(r)} className="text-rose-600 font-semibold hover:underline">Delete</button>}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ── Variable picker (grouped, searchable, click-to-insert) ─────────────────
   Reads the live catalog from /documents/templates so it always matches what the
   backend can actually resolve — including the org's own custom fields. */
function VarPicker({ onInsert, module }: { onInsert: (tok: string) => void; module?: string }) {
  const [groups, setGroups] = useState<{ group: string; items: string[] }[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    // N-P3: module-scoped variables via /notifications/variables?module=…; the
    // endpoint returns { groups: { name: [items] } }, so reshape to the array form.
    if (module) {
      api.get<any>(`/notifications/variables?module=${encodeURIComponent(module)}`)
        .then((d) => setGroups(Object.entries(d?.groups || {}).map(([group, items]) => ({ group, items: items as string[] }))))
        .catch(() => setGroups([]));
    } else {
      api.get<any>("/documents/templates").then((d) => setGroups(d?.variable_groups || [])).catch(() => setGroups([]));
    }
  }, [module]);
  const ql = q.trim().toLowerCase();
  const shown = groups
    .map((g) => ({ ...g, items: ql ? g.items.filter((i) => i.toLowerCase().includes(ql)) : g.items }))
    .filter((g) => g.items.length);
  return (
    <div className="border border-slate-200 rounded-lg bg-slate-50/60 flex flex-col max-h-[460px]">
      <div className="p-2 border-b border-slate-200">
        <div className="text-[11px] font-semibold text-ink-tertiary mb-1 px-1">Insert variable</div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search variables…" className="w-full bg-white border border-slate-200 rounded-md text-xs px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary" />
      </div>
      <div className="flex-1 overflow-y-auto p-1.5 space-y-1">
        {shown.length === 0 && <div className="text-xs text-ink-tertiary px-2 py-3">No matches.</div>}
        {shown.map((g) => {
          const isOpen = ql ? true : (open[g.group] ?? false);
          return (
            <div key={g.group}>
              <button type="button" onClick={() => setOpen((o) => ({ ...o, [g.group]: !isOpen }))}
                className="w-full flex items-center justify-between px-2 py-1.5 rounded-md hover:bg-slate-200/60 text-left">
                <span className="text-[11px] font-bold text-ink-secondary ">{g.group}</span>
                <M n={isOpen ? "expand_less" : "expand_more"} c="text-sm text-ink-tertiary" />
              </button>
              {isOpen && (
                <div className="flex flex-wrap gap-1 px-1.5 pb-1.5">
                  {g.items.map((tok) => (
                    <button key={tok} type="button" onClick={() => onInsert(tok)} title={`Insert ${tok}`}
                      className="text-[11px] tabular-nums px-1.5 py-0.5 rounded border border-slate-200 bg-white hover:bg-primary hover:text-white hover:border-primary transition-colors">
                      {tok.replace(/^\{|\}$/g, "")}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// SMS is 160 GSM-7 chars per segment (153 when concatenated across >1 segment).
function smsSegments(len: number): number {
  if (len <= 160) return len === 0 ? 0 : 1;
  return Math.ceil(len / 153);
}

// N-P1 — live character + SMS segment counter, matching Orderry's template editor.
function CharCounter({ text, sms }: { text: string; sms: boolean }) {
  const len = text.length;
  const seg = smsSegments(len);
  return (
    <div className="mt-1 flex items-center gap-2 text-[11px] text-ink-tertiary">
      <span>{len} character{len === 1 ? "" : "s"}</span>
      {sms && (
        <>
          <span className="text-slate-300">·</span>
          <span className={seg > 1 ? "text-amber-600 font-medium" : ""}>{seg} SMS segment{seg === 1 ? "" : "s"}</span>
          {seg > 1 && <span className="text-ink-tertiary">— variable values can push this higher</span>}
        </>
      )}
    </div>
  );
}

function EditorDrawer({ title, fields, initial, varModule, onClose, onSave, onDelete }: { title: string; fields: any[]; initial: any; varModule?: string; onClose: () => void; onSave: (v: any) => Promise<void>; onDelete?: () => Promise<void> }) {
  const seed: any = {}; fields.forEach((f) => { const raw = initial?.[f.k] ?? f.def ?? (f.type === "checkbox" ? false : f.type === "number" ? 0 : ""); seed[f.k] = f.type === "date" && typeof raw === "string" && raw ? raw.slice(0, 10) : raw; });
  const [v, setV] = useState<any>(seed);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const set = (k: string) => (e: any) => setV({ ...v, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const hasVars = fields.some((f) => f.vars);
  const areaRefs = useRef<Record<string, HTMLTextAreaElement | null>>({});
  // insert a {Token} at the caret of the field's textarea (append if not focused)
  function insertVar(k: string, tok: string) {
    const ta = areaRefs.current[k];
    const cur = String(v[k] ?? "");
    if (!ta) { setV({ ...v, [k]: cur + tok }); return; }
    const s = ta.selectionStart ?? cur.length, e = ta.selectionEnd ?? cur.length;
    const next = cur.slice(0, s) + tok + cur.slice(e);
    setV({ ...v, [k]: next });
    requestAnimationFrame(() => { ta.focus(); const p = s + tok.length; ta.setSelectionRange(p, p); });
  }
  async function save() {
    const missing = fields.find((f) => f.req && !String(v[f.k] ?? "").trim());
    if (missing) { setErr(`${missing.label} is required`); return; }
    setBusy(true); setErr(null);
    try { await onSave(v); } catch (e: any) { setErr(e?.message || "Could not save"); setBusy(false); }
  }
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className={`relative ${hasVars ? "w-[720px]" : "w-[440px]"} max-w-full bg-white h-full shadow-drawer flex flex-col`}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-bold text-ink-primary">{title}</h2>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          {fields.map((f) => (
            <div key={f.k}>
              <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">{f.label}{f.req ? " *" : ""}</label>
              {f.type === "checkbox" ? (
                <button type="button" onClick={() => setV({ ...v, [f.k]: !v[f.k] })} className={`w-10 h-6 rounded-full transition-colors ${v[f.k] ? "bg-primary" : "bg-slate-300"} relative`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${v[f.k] ? "left-[18px]" : "left-0.5"}`} /></button>
              ) : f.type === "select" ? (
                <select value={v[f.k]} onChange={set(f.k)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary capitalize">{f.opts.map((o: string) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}</select>
              ) : f.type === "color" ? (
                <input type="color" value={v[f.k]} onChange={set(f.k)} className="h-9 w-16 rounded border border-slate-200" />
              ) : f.type === "textarea" ? (
                f.vars ? (
                  <div>
                    <div className="grid grid-cols-1 sm:grid-cols-[1fr_260px] gap-3">
                      <textarea ref={(el) => { areaRefs.current[f.k] = el; }} value={v[f.k]} onChange={set(f.k)} rows={12} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary font-mono" />
                      <VarPicker onInsert={(tok) => insertVar(f.k, tok)} module={varModule} />
                    </div>
                    {f.counter && <CharCounter text={String(v[f.k] ?? "")} sms={v.channel === "sms"} />}
                  </div>
                ) : (
                  <div>
                    <textarea ref={(el) => { areaRefs.current[f.k] = el; }} value={v[f.k]} onChange={set(f.k)} rows={5} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary font-mono" />
                    {f.counter && <CharCounter text={String(v[f.k] ?? "")} sms={v.channel === "sms"} />}
                  </div>
                )
              ) : (
                <input type={f.type} value={v[f.k]} onChange={set(f.k)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary" />
              )}
            </div>
          ))}
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex items-center gap-2">
          {onDelete && initial?.id && <button onClick={async () => { if (await confirmDialog("Delete this item?", { danger: true, confirmLabel: "Delete" })) { await onDelete(); } }} className="text-rose-600 font-semibold text-xs hover:underline mr-auto">Delete</button>}
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs ml-auto">Cancel</button>
          <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}

/* ── 144jf — compact status multi-select (popover) — replaces the chip wall in the
   public-page approval settings. Shows selected statuses as small chips + an "Add
   status" popover grouped by status group. Clean, like Orderry's multiselects. */
const SMS_GROUP_ORDER = ["new", "in_progress", "pending", "ready", "closed", "dropped"];
const SMS_GROUP_LABEL: Record<string, string> = {
  new: "New", in_progress: "In progress", pending: "Pending",
  ready: "Ready", closed: "Closed", dropped: "Dropped", other: "Other",
};
const SMS_DEFAULT_COLOR = "#64748b";
const smsTintBg = (hex?: string) => `${hex || SMS_DEFAULT_COLOR}1f`;
const smsTintBorder = (hex?: string) => `${hex || SMS_DEFAULT_COLOR}59`;

function StatusMultiSelect({ value, list, onChange, single, placeholder }: {
  value: string; list: { key: string; name: string; status_group?: string; color?: string }[]; onChange: (csv: string) => void;
  single?: boolean; placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [rect, setRect] = useState<{ left: number; width: number; top?: number; bottom?: number; maxH: number } | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const cleanup = useRef<(() => void) | null>(null);
  // Always open *downward*, anchored under the trigger (Orderry-style). The
  // list height is capped to the room below and scrolls internally; when the
  // field sits too low we scroll it up first (in the open effect) so there's
  // always room — the popover never flips up over the card above.
  const place = () => {
    const r = boxRef.current?.getBoundingClientRect(); if (!r) return;
    const below = window.innerHeight - r.bottom - 12;
    setRect({ left: r.left, width: r.width, top: r.bottom + 6, maxH: Math.max(160, Math.min(320, below)) });
  };
  useEffect(() => {
    if (!open) { setQ(""); return; }
    // If there isn't room for a comfortable downward popover, bring the trigger
    // up into view first so it can open down without running off-screen.
    const r0 = boxRef.current?.getBoundingClientRect();
    if (r0 && window.innerHeight - r0.bottom - 12 < 240) boxRef.current?.scrollIntoView({ block: "center" });
    place();
    // Attach listeners on the next frame so our own scrollIntoView above
    // doesn't immediately trigger the close-on-scroll handler.
    const raf = requestAnimationFrame(() => {
      const onDown = (e: MouseEvent) => { const t = e.target as Node; if (!boxRef.current?.contains(t) && !popRef.current?.contains(t)) setOpen(false); };
      // Close on page scroll — but NOT when the scroll happens *inside* the
      // popover's own list (capture:true fires for descendants too, which was
      // slamming the dropdown shut the instant you scrolled the status list).
      const onScroll = (e: Event) => { const t = e.target as Node; if (popRef.current?.contains(t)) return; setOpen(false); };
      document.addEventListener("mousedown", onDown);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", onScroll);
      cleanup.current = () => { document.removeEventListener("mousedown", onDown); window.removeEventListener("scroll", onScroll, true); window.removeEventListener("resize", onScroll); };
    });
    return () => { cancelAnimationFrame(raf); cleanup.current?.(); cleanup.current = null; };
  }, [open]);
  const cur = (value || "").split(",").map((x) => x.trim()).filter(Boolean);
  const setCur = (next: string[]) => onChange(next.join(","));
  const pick = (key: string) => {
    if (single) { onChange(cur[0] === key ? "" : key); setOpen(false); return; }
    setCur(cur.includes(key) ? cur.filter((x) => x !== key) : [...cur, key]);
  };
  const byStatus = (key: string) => list.find((s) => s.key === key);
  const ql = q.trim().toLowerCase();
  const shown = ql ? list.filter((s) => s.name.toLowerCase().includes(ql)) : list;
  const groupsPresent = Array.from(new Set(shown.map((s) => s.status_group || "other")));
  const groups = [
    ...SMS_GROUP_ORDER.filter((g) => groupsPresent.includes(g)),
    ...groupsPresent.filter((g) => !SMS_GROUP_ORDER.includes(g)),
  ];
  const grpLabel = (g: string) => SMS_GROUP_LABEL[g] || g.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const empty = placeholder || "All statuses (any)";
  return (
    <div ref={boxRef}>
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="w-full min-h-[38px] bg-white border border-slate-200 rounded-lg px-2 py-1.5 flex items-center gap-1.5 flex-wrap text-left hover:border-slate-300 focus:outline-none focus:ring-1 focus:ring-primary">
        {cur.length === 0 && <span className="text-sm text-ink-tertiary px-1">{empty}</span>}
        {cur.map((key) => {
          const s = byStatus(key);
          return (
            <span key={key} className="inline-flex items-center gap-1.5 pl-2 pr-1 py-0.5 rounded-full text-xs font-semibold shadow-2xs"
              style={{ background: smsTintBg(s?.color), border: `0.5px solid ${smsTintBorder(s?.color)}`, color: s?.color || SMS_DEFAULT_COLOR }}>
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: s?.color || SMS_DEFAULT_COLOR }} />
              {s?.name || key}
              <span onClick={(e) => { e.stopPropagation(); pick(key); }} className="w-4 h-4 grid place-items-center rounded-full hover:bg-black/5 cursor-pointer"><M n="close" c="text-[12px]" /></span>
            </span>
          );
        })}
        <M n="expand_more" c="text-base text-ink-tertiary ml-auto" />
      </button>
      {open && rect && createPortal(
        <div ref={popRef} style={{ position: "fixed", left: rect.left, width: rect.width, zIndex: 80, maxHeight: rect.maxH, ...(rect.top !== undefined ? { top: rect.top } : { bottom: rect.bottom }) }}
          className="flex flex-col bg-white border border-slate-200 rounded-xl shadow-clay-drawer overflow-hidden animate-[smsFade_.12s_ease]">
          <style>{`@keyframes smsFade{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}`}</style>
          {list.length > 6 && (
            <div className="p-1.5 border-b border-slate-100">
              <div className="relative">
                <M n="search" c="absolute left-2 top-1/2 -translate-y-1/2 text-sm text-ink-tertiary" />
                <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter statuses" className="w-full bg-white border border-slate-200 rounded-lg text-xs pl-7 pr-2 py-1.5 focus:outline-none focus:border-primary/50" />
              </div>
            </div>
          )}
          <div className="flex-1 min-h-0 overflow-y-auto p-1.5 space-y-0.5">
            {!single && cur.length > 0 && (
              <div className="flex items-center justify-end px-1 pb-0.5">
                <button type="button" onClick={() => setCur([])} className="text-[11px] font-semibold text-primary hover:underline">Clear all</button>
              </div>
            )}
            {shown.length === 0 && <div className="px-2 py-3 text-xs text-ink-tertiary text-center">{list.length === 0 ? "No statuses yet." : "No match"}</div>}
            {groups.map((g) => (
              <div key={g}>
                <p className="px-2.5 pt-2 pb-0.5 text-[11px] font-bold text-ink-tertiary ">{grpLabel(g)}</p>
                {shown.filter((s) => (s.status_group || "other") === g).map((s) => {
                  const sel = cur.includes(s.key);
                  return (
                    <button type="button" key={s.key} onClick={() => pick(s.key)}
                      className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left transition-colors ${sel ? "" : "hover:bg-slate-50"}`}
                      style={sel ? { background: smsTintBg(s.color), border: `0.5px solid ${smsTintBorder(s.color)}` } : undefined}>
                      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: s.color || SMS_DEFAULT_COLOR }} />
                      <span className="text-xs font-semibold text-ink-primary truncate flex-1">{s.name}</span>
                      {sel && <M n="check" c="text-sm flex-shrink-0" />}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        </div>, document.body)}
    </div>
  );
}

/* ── Public appointment page editor (link + branding + appointment rules) ─────────── */
/* ── System fields per entity: toggle built-in fields' Show/Required, persisted to /forms/system-layout ── */
// ── E-commerce integrations (Shopify/WooCommerce) — legacy gap, backend /api/ecommerce/integrations ──
function EcommerceSettings() {
  const [items, setItems] = useState<any[] | null>(null);
  const [f, setF] = useState<any>({ platform: "shopify", store_url: "", sync_products: true, sync_orders: true });
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 2000); };
  function reload() { api.get<any>("/ecommerce/integrations").then((r) => setItems(r.items || [])).catch(() => setItems([])); }
  useEffect(reload, []);
  async function add() {
    if (!f.store_url.trim()) return; setBusy(true);
    try { await api.post("/ecommerce/integrations", f); setF({ platform: "shopify", store_url: "", sync_products: true, sync_orders: true }); reload(); flash("Store connected"); }
    catch (e: any) { alertDialog(e.message || "Failed"); } finally { setBusy(false); }
  }
  return (
    <div className="max-w-3xl"><SectionCard title="E-commerce stores" count={items?.length}>
      <div className="divide-y divide-slate-100">
        {(items || []).map((i) => (
          <div key={i.id} className="px-5 py-3 flex items-center gap-2 text-xs">
            <span className="px-2 py-0.5 rounded bg-slate-100 text-[11px] font-bold capitalize">{i.platform}</span>
            <span className="font-semibold text-ink-primary">{i.store_url || "—"}</span>
            <span className="text-ink-tertiary">{[i.sync_products && "products", i.sync_orders && "orders"].filter(Boolean).join(" + ")}</span>
            <div className="flex-1" />
            <span className="text-[11px] text-ink-tertiary">{i.last_synced_at ? `synced ${new Date(i.last_synced_at).toLocaleDateString()}` : "never synced"}</span>
            <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${i.active ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-ink-secondary"}`}>{i.active ? "Active" : "Off"}</span>
          </div>
        ))}
        {items && items.length === 0 && <div className="px-5 py-3 text-xs text-ink-tertiary">No stores connected.</div>}
        <div className="px-5 py-3 flex items-end gap-2 flex-wrap">
          <div><label className={lbl}>Platform</label><select value={f.platform} onChange={(e) => setF({ ...f, platform: e.target.value })} className={field}><option value="shopify">Shopify</option><option value="woocommerce">WooCommerce</option></select></div>
          <div className="flex-1 min-w-[180px]"><label className={lbl}>Store URL</label><input value={f.store_url} onChange={(e) => setF({ ...f, store_url: e.target.value })} placeholder="my-shop.myshopify.com" className={field} /></div>
          <label className="flex items-center gap-1.5 text-[11px] font-semibold text-ink-secondary"><input type="checkbox" checked={f.sync_products} onChange={(e) => setF({ ...f, sync_products: e.target.checked })} className="accent-primary" />Products</label>
          <label className="flex items-center gap-1.5 text-[11px] font-semibold text-ink-secondary"><input type="checkbox" checked={f.sync_orders} onChange={(e) => setF({ ...f, sync_orders: e.target.checked })} className="accent-primary" />Orders</label>
          <button onClick={add} disabled={busy} className="px-3 py-1.5 rounded-lg bg-primary text-white text-[11px] font-bold shadow-clay-sm">Connect store</button>
        </div>
      </div>
    </SectionCard>{msg && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-drawer z-[70]">{msg}</div>}</div>
  );
}

// ── Single sign-on (OIDC/SAML) — legacy gap, backend /api/sso/config ──
function SsoSettings() {
  const [items, setItems] = useState<any[] | null>(null);
  const [f, setF] = useState<any>({ protocol: "oidc", display_name: "", issuer: "", client_id: "", client_secret: "", metadata_url: "", email_domain: "", enabled: true });
  const [busy, setBusy] = useState(false);
  function reload() { api.get<any>("/sso/config").then((r) => setItems(r.items || [])).catch(() => setItems([])); }
  useEffect(reload, []);
  async function add() {
    if (!f.display_name.trim()) return; setBusy(true);
    try { await api.post("/sso/config", f); setF({ protocol: "oidc", display_name: "", issuer: "", client_id: "", client_secret: "", metadata_url: "", email_domain: "", enabled: true }); reload(); }
    catch (e: any) { alertDialog(e.message || "Failed"); } finally { setBusy(false); }
  }
  async function del(id: string) { try { await api.del(`/sso/config/${id}`); reload(); } catch (e: any) { alertDialog(e.message); } }
  return (
    <div className="max-w-3xl"><SectionCard title="SSO connections" count={items?.length}>
      <div className="divide-y divide-slate-100">
        {(items || []).map((s) => (
          <div key={s.id} className="px-5 py-3 flex items-center gap-2 text-xs">
            <span className="px-2 py-0.5 rounded bg-slate-100 text-[11px] font-bold ">{s.protocol}</span>
            <span className="font-semibold text-ink-primary">{s.display_name}</span>
            {s.email_domain && <span className="text-ink-tertiary">@{s.email_domain}</span>}
            <div className="flex-1" />
            <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${s.enabled ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-ink-secondary"}`}>{s.enabled ? "Enabled" : "Disabled"}</span>
            <button aria-label="Delete" onClick={() => del(s.id)} className="text-rose-600"><M n="delete" c="text-sm" /></button>
          </div>
        ))}
        {items && items.length === 0 && <div className="px-5 py-3 text-xs text-ink-tertiary">No SSO connections. Staff sign in with email + password until one is enabled.</div>}
        <div className="px-5 py-3 grid grid-cols-2 gap-2">
          <div><label className={lbl}>Protocol</label><select value={f.protocol} onChange={(e) => setF({ ...f, protocol: e.target.value })} className={field}><option value="oidc">OIDC</option><option value="saml">SAML</option></select></div>
          <div><label className={lbl}>Display name</label><input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} placeholder="Google Workspace" className={field} /></div>
          <div><label className={lbl}>Issuer / metadata URL</label><input value={f.metadata_url} onChange={(e) => setF({ ...f, metadata_url: e.target.value })} placeholder="https://…/.well-known/openid-configuration" className={field} /></div>
          <div><label className={lbl}>Email domain</label><input value={f.email_domain} onChange={(e) => setF({ ...f, email_domain: e.target.value })} placeholder="company.com" className={field} /></div>
          <div><label className={lbl}>Client ID</label><input value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value })} className={field} /></div>
          <div><label className={lbl}>Client secret</label><input type="password" value={f.client_secret} onChange={(e) => setF({ ...f, client_secret: e.target.value })} className={field} /></div>
          <label className="flex items-center gap-1.5 text-[11px] font-semibold text-ink-secondary"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} className="accent-primary" />Enabled</label>
          <div className="text-right"><button onClick={add} disabled={busy} className="px-3 py-1.5 rounded-lg bg-primary text-white text-[11px] font-bold shadow-clay-sm">+ Add connection</button></div>
        </div>
      </div>
    </SectionCard></div>
  );
}

// ── Approvals inbox (refund/discount/retro-edit requests) — legacy gap, backend /api/approvals ──
function ApprovalsSettings() {
  const [items, setItems] = useState<any[] | null>(null);
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [busy, setBusy] = useState(false);
  function reload() { api.get<any>(`/approvals?status=${filter}`).then((r) => setItems(r.items || [])).catch(() => setItems([])); }
  useEffect(reload, [filter]);
  async function decide(id: string, decision: string) {
    setBusy(true);
    try { await api.post(`/approvals/${id}/decide`, { decision }); reload(); }
    catch (e: any) { alertDialog(e.message || "Failed"); } finally { setBusy(false); }
  }
  const pill = (s: string) => s === "approved" ? "bg-emerald-100 text-emerald-700" : s === "rejected" ? "bg-rose-100 text-rose-700" : "bg-amber-100 text-amber-700";
  return (
    <div className="max-w-3xl"><SectionCard title="Approval requests" count={items?.length}>
      <div className="px-5 py-2 flex gap-1.5 border-b border-slate-100">
        {(["pending", "all"] as const).map((s) => <button key={s} onClick={() => setFilter(s)} className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold capitalize ${filter === s ? "bg-primary text-white" : "bg-slate-50 text-ink-secondary hover:bg-slate-100"}`}>{s}</button>)}
      </div>
      <div className="divide-y divide-slate-100">
        {!items && <div className="px-5 py-4 text-xs text-ink-tertiary">Loading…</div>}
        {items && items.length === 0 && <div className="px-5 py-4 text-xs text-ink-tertiary">No {filter === "pending" ? "pending" : ""} approval requests.</div>}
        {(items || []).map((r) => (
          <div key={r.id} className="px-5 py-3 flex items-center gap-2 text-xs">
            <span className="px-2 py-0.5 rounded bg-slate-100 text-[11px] font-bold capitalize">{(r.kind || "").replace(/_/g, " ")}</span>
            {r.amount != null && <span className="tabular-nums font-bold text-ink-primary">{r.amount}</span>}
            {r.reason && <span className="text-ink-secondary truncate max-w-[220px]">{r.reason}</span>}
            <div className="flex-1" />
            {r.status === "pending"
              ? <><button onClick={() => decide(r.id, "approve")} disabled={busy} className="px-2.5 py-1 rounded-lg bg-emerald-600 text-white text-[11px] font-bold">Approve</button><button onClick={() => decide(r.id, "reject")} disabled={busy} className="px-2.5 py-1 rounded-lg border border-rose-200 text-rose-600 text-[11px] font-bold">Reject</button></>
              : <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold capitalize ${pill(r.status)}`}>{r.status}</span>}
          </div>
        ))}
      </div>
    </SectionCard></div>
  );
}

// Industry template picker (Settings ▸ Industry & assets). Switching re-labels the
// app chrome (Assets → Vessels / Units / …); existing assets keep the noun they
// were created under. Seeds the new template's groups/brands on save.
// Asset catalog admin — the cockpit for the multi-industry engine: manage per-template
// Groups, Brands and Models, view the identifier rules each group enforces, and bulk-import
// via CSV. All CRUD hits /settings/asset-{groups,brands,models}; rules are read-only (they
// live in the template registry). Everything is scoped to the org's current template.
const ID_LABEL: Record<string, string> = { imei: "IMEI", serial: "Serial", vin: "VIN", hin: "HIN", engine_serial: "Engine serial", barcode: "Barcode", asset_tag: "Asset tag" };
function AssetCatalog() {
  const [tk, setTk] = useState<string>("");
  const [groups, setGroups] = useState<any[]>([]);
  const [brands, setBrands] = useState<any[]>([]);
  const [models, setModels] = useState<any[]>([]);
  const [rules, setRules] = useState<any[]>([]);
  const [gsel, setGsel] = useState<string>("");      // selected group filter (id) or "" = all
  const [bsel, setBsel] = useState<string>("");      // selected brand for the models column
  const [toast, setToast] = useState<string | null>(null);
  const [newGroup, setNewGroup] = useState(""); const [newBrand, setNewBrand] = useState(""); const [newModel, setNewModel] = useState("");
  const [csvOpen, setCsvOpen] = useState(false); const [csv, setCsv] = useState(""); const [busy, setBusy] = useState(false);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2000); };

  const loadGroups = (t: string) => api.get<any>(`/settings/asset-groups?template_key=${t}`).then((r) => setGroups(r.items || [])).catch(() => setGroups([]));
  const loadBrands = (t: string) => api.get<any>(`/settings/asset-brands?template_key=${t}`).then((r) => setBrands(r.items || [])).catch(() => setBrands([]));
  const loadModels = (t: string) => api.get<any>(`/settings/asset-models?template_key=${t}`).then((r) => setModels(r.items || [])).catch(() => setModels([]));
  const loadRules = (t: string) => api.get<any>(`/settings/asset-identifier-rules?template_key=${t}`).then((r) => setRules(r.items || [])).catch(() => setRules([]));

  useEffect(() => {
    api.get<any>(`/settings/industry-template`).then((r) => {
      const t = r?.current || "general"; setTk(t);
      loadGroups(t); loadBrands(t); loadModels(t); loadRules(t);
    }).catch(() => {});
  }, []);

  const gName = (id?: string | null) => groups.find((g) => g.id === id)?.label || "—";
  const ruleFor = (gid?: string | null) => rules.find((r) => r.asset_group_id === gid) || rules.find((r) => !r.asset_group_id);
  const shownBrands = gsel ? brands.filter((b) => b.asset_group_id === gsel || !b.asset_group_id) : brands;
  const shownModels = bsel ? models.filter((m) => m.brand_id === bsel) : (gsel ? models.filter((m) => m.asset_group_id === gsel) : models);

  async function addGroup() { if (!newGroup.trim()) return; try { await api.post(`/settings/asset-groups`, { template_key: tk, key: newGroup.trim().toLowerCase().replace(/\s+/g, "_"), label: newGroup.trim() }); setNewGroup(""); flash("Group added"); loadGroups(tk); loadRules(tk); } catch (e: any) { flash(e.message || "Failed"); } }
  async function addBrand() { if (!newBrand.trim()) return; try { await api.post(`/settings/asset-brands`, { template_key: tk, name: newBrand.trim(), asset_group_id: gsel || null }); setNewBrand(""); flash("Brand added"); loadBrands(tk); } catch (e: any) { flash(e.message || "Failed"); } }
  async function addModel() { if (!newModel.trim() || !bsel) { flash("Pick a brand first"); return; } try { await api.post(`/settings/asset-models`, { template_key: tk, brand_id: bsel, name: newModel.trim(), asset_group_id: gsel || null }); setNewModel(""); flash("Model added"); loadModels(tk); } catch (e: any) { flash(e.message || "Failed"); } }
  async function delRow(kind: "groups" | "brands" | "models", id: string) { if (!(await confirmDialog("Delete this entry?", { danger: true, confirmLabel: "Delete" }))) return; try { await api.del(`/settings/asset-${kind}/${id}`); flash("Deleted"); if (kind === "groups") { loadGroups(tk); if (gsel === id) setGsel(""); } if (kind === "brands") { loadBrands(tk); if (bsel === id) setBsel(""); } if (kind === "models") loadModels(tk); } catch (e: any) { flash(e.message || "Delete failed"); } }
  async function runImport() { if (!csv.trim()) return; setBusy(true); try { const r = await api.post<any>(`/settings/asset-directory/import`, { template_key: tk, csv }); flash(`Imported ${r?.brands ?? 0} brands, ${r?.models ?? 0} models`); setCsv(""); setCsvOpen(false); loadBrands(tk); loadModels(tk); } catch (e: any) { flash(e.message || "Import failed"); } finally { setBusy(false); } }

  const box = "bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden";
  const addInp = "glass-input flex-1 px-3 py-2 rounded-lg text-xs font-semibold text-ink-primary";
  const addBtn = "px-3 py-2 rounded-lg bg-blue-50 text-primary border border-blue-100 text-xs font-bold hover:bg-blue-100 shrink-0";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div><div className="text-sm font-bold text-ink-primary">Asset catalog</div><div className="text-xs text-ink-tertiary">Groups, brands, models &amp; identifier rules for the <span className="font-bold">{tk}</span> template.</div></div>
        <button type="button" onClick={() => setCsvOpen((o) => !o)} className={addBtn}>{csvOpen ? "Close import" : "Import CSV"}</button>
      </div>

      {csvOpen && (
        <div className="rounded-2xl bg-slate-50/70 border border-slate-200/80 p-3.5 space-y-2">
          <div className="text-[11px] font-bold text-ink-tertiary">Bulk import — header row required: <span className="tabular-nums normal-case">brand,model,group</span></div>
          <textarea value={csv} onChange={(e) => setCsv(e.target.value)} rows={5} className="glass-input block w-full py-2 px-3 text-xs tabular-nums text-ink-primary rounded-lg resize-none" placeholder={"brand,model,group\nApple,iPhone 15,Phone\nSamsung,Galaxy S24,Phone"} />
          <div className="flex justify-end"><button type="button" onClick={runImport} disabled={busy} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 disabled:opacity-60">{busy ? "Importing…" : "Import"}</button></div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Groups */}
        <div className={box}>
          <div className="px-4 py-2.5 border-b border-slate-100 text-[11px] font-bold text-ink-tertiary">Groups ({groups.length})</div>
          <div className="divide-y divide-slate-100 max-h-72 overflow-y-auto">
            <button type="button" onClick={() => { setGsel(""); setBsel(""); }} className={`w-full text-left px-4 py-2 text-xs font-bold ${!gsel ? "bg-blue-50/60 text-primary" : "text-ink-secondary hover:bg-slate-50"}`}>All groups</button>
            {groups.map((g) => {
              const rule = ruleFor(g.id);
              return (
                <div key={g.id} className={`px-4 py-2 flex items-center gap-2 ${gsel === g.id ? "bg-blue-50/60" : "hover:bg-slate-50"}`}>
                  <button type="button" onClick={() => { setGsel(g.id); setBsel(""); }} className="flex-1 text-left min-w-0">
                    <div className="text-xs font-bold text-ink-primary truncate">{g.label}</div>
                    {rule && <div className="text-[11px] text-ink-tertiary">ID: {(rule.allowed_kinds || []).map((k: string) => ID_LABEL[k] || k).join(" / ") || "—"}</div>}
                  </button>
                  {!g.is_system && <button aria-label="Close" type="button" onClick={() => delRow("groups", g.id)} className="text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>}
                </div>
              );
            })}
          </div>
          <div className="p-2.5 flex gap-1.5 border-t border-slate-100"><input value={newGroup} onChange={(e) => setNewGroup(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addGroup()} className={addInp} placeholder="New group" /><button type="button" onClick={addGroup} className={addBtn}>Add</button></div>
        </div>

        {/* Brands */}
        <div className={box}>
          <div className="px-4 py-2.5 border-b border-slate-100 text-[11px] font-bold text-ink-tertiary">Brands ({shownBrands.length}){gsel ? ` · ${gName(gsel)}` : ""}</div>
          <div className="divide-y divide-slate-100 max-h-72 overflow-y-auto">
            {shownBrands.length === 0 && <p className="px-4 py-3 text-xs text-ink-tertiary">No brands.</p>}
            {shownBrands.map((b) => (
              <div key={b.id} className={`px-4 py-2 flex items-center gap-2 ${bsel === b.id ? "bg-blue-50/60" : "hover:bg-slate-50"}`}>
                <button type="button" onClick={() => setBsel(b.id)} className="flex-1 text-left text-xs font-bold text-ink-primary truncate">{b.name}</button>
                {!b.is_system && <button aria-label="Close" type="button" onClick={() => delRow("brands", b.id)} className="text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>}
              </div>
            ))}
          </div>
          <div className="p-2.5 flex gap-1.5 border-t border-slate-100"><input value={newBrand} onChange={(e) => setNewBrand(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addBrand()} className={addInp} placeholder={gsel ? `New brand in ${gName(gsel)}` : "New brand"} /><button type="button" onClick={addBrand} className={addBtn}>Add</button></div>
        </div>

        {/* Models */}
        <div className={box}>
          <div className="px-4 py-2.5 border-b border-slate-100 text-[11px] font-bold text-ink-tertiary">Models ({shownModels.length}){bsel ? ` · ${brands.find((b) => b.id === bsel)?.name || ""}` : ""}</div>
          <div className="divide-y divide-slate-100 max-h-72 overflow-y-auto">
            {!bsel && <p className="px-4 py-3 text-xs text-ink-tertiary">Pick a brand to see and add its models.</p>}
            {bsel && shownModels.length === 0 && <p className="px-4 py-3 text-xs text-ink-tertiary">No models for this brand.</p>}
            {bsel && shownModels.map((m) => (
              <div key={m.id} className="px-4 py-2 flex items-center gap-2 hover:bg-slate-50">
                <span className="flex-1 text-xs font-bold text-ink-primary truncate">{m.name}{(m.year_from || m.year_to) ? <span className="text-ink-tertiary font-normal"> · {m.year_from || "…"}–{m.year_to || "…"}</span> : null}</span>
                {!m.is_system && <button aria-label="Close" type="button" onClick={() => delRow("models", m.id)} className="text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>}
              </div>
            ))}
          </div>
          {bsel && <div className="p-2.5 flex gap-1.5 border-t border-slate-100"><input value={newModel} onChange={(e) => setNewModel(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addModel()} className={addInp} placeholder="New model" /><button type="button" onClick={addModel} className={addBtn}>Add</button></div>}
        </div>
      </div>
      {toast && <div className="text-xs font-semibold text-primary">{toast}</div>}
    </div>
  );
}

/* Industry is chosen in General › What you repair; this section only says which one is active. */
function IndustryNote({ onChange }: { onChange: () => void }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => { api.get<any>("/settings/industry-template").then(setD).catch(() => setD(null)); }, []);
  const cur = (d?.templates || []).find((t: any) => t.key === d?.current);
  return (
    <div className="flex items-center gap-3 bg-white rounded-2xl border border-slate-200/90 shadow-clay-card px-5 py-4">
      <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="handyman" c="text-lg" /></span>
      <div className="min-w-0">
        <div className="text-sm text-ink-primary">{cur ? <>Your shop repairs: <span className="font-semibold">{cur.label}</span></> : "What your shop repairs"}</div>
        <div className="text-[11px] text-ink-tertiary">The brands, models and groups below follow this. Change it in General.</div>
      </div>
      <button onClick={onChange} className="ml-auto px-3.5 py-2 rounded-xl border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50 flex-shrink-0">Change</button>
    </div>
  );
}

// Several items per job — only offered to tools, small engine / equipment, phone and computer
// shops (the server enforces the same list: services/multi_asset.ELIGIBLE). Other industries never see it.
function MultiAssetSwitch() {
  const [st, setSt] = useState<{ available: boolean; on: boolean } | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<any>("/staff/company").then((c) => setSt({ available: !!c.multi_asset_available, on: !!c.multi_asset_jobs })).catch(() => setSt(null)); }, []);
  if (!st?.available) return null;
  async function flip() {
    if (!st) return;
    setBusy(true); setErr(null);
    try { await api.put("/staff/company", { multi_asset_jobs: !st.on }); setSt({ ...st, on: !st.on }); }
    catch (e: any) { setErr(e?.message || "Couldn't change this"); } finally { setBusy(false); }
  }
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card px-5 py-4">
      <div className="flex items-start gap-3">
        <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="inventory" c="text-lg" /></span>
        <div className="min-w-0">
          <div className="text-sm font-semibold text-ink-primary">Several items per job</div>
          <div className="text-xs text-ink-tertiary leading-relaxed">For customers who drop off a batch, like six tools at once. One job holds every item, each with its own problem and result, and each service or part can be marked for one item.</div>
          {err && <div className="mt-1 text-xs text-rose-600">{err}</div>}
        </div>
        <button type="button" role="switch" aria-checked={st.on} aria-label="Several items per job" disabled={busy} onClick={flip}
          className={`ml-auto w-10 h-6 rounded-full relative transition-colors flex-shrink-0 disabled:opacity-60 ${st.on ? "bg-primary" : "bg-slate-300"}`}>
          <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${st.on ? "left-[18px]" : "left-0.5"}`} />
        </button>
      </div>
    </div>
  );
}

// Quantity breaks per price level (Settings ▸ Pricing › Quantity breaks) → /pricing/types/{id}/tiers.
function TierManager({ types }: { types: any[] }) {
  const [ptId, setPtId] = useState("");
  const [tiers, setTiers] = useState<any[]>([]);
  const [minQty, setMinQty] = useState("");
  const [markup, setMarkup] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { if (!ptId) { setTiers([]); return; }
    api.get<any>(`/pricing/types/${ptId}/tiers`).then((r) => setTiers(r.items || [])).catch(() => setTiers([])); }, [ptId]);
  async function add() {
    if (!ptId || minQty === "" || markup === "") return;
    setBusy(true); setMsg("");
    try {
      await api.post(`/pricing/types/${ptId}/tiers`, { min_qty: Number(minQty), markup_pct: Number(markup) });
      const r = await api.get<any>(`/pricing/types/${ptId}/tiers`); setTiers(r.items || []);
      setMinQty(""); setMarkup("");
    } catch (e: any) { setMsg(e.message || "Could not add"); } finally { setBusy(false); }
  }
  async function del(id: string) {
    await api.del(`/pricing/types/${ptId}/tiers/${id}`);
    const r = await api.get<any>(`/pricing/types/${ptId}/tiers`); setTiers(r.items || []);
  }
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-5 space-y-3">
      <div><h4 className="text-sm font-bold text-ink-primary">Quantity break tiers</h4>
        <p className="text-xs text-ink-tertiary mt-0.5">Lower the markup automatically once a line reaches a quantity. The highest matching tier wins.</p></div>
      <select value={ptId} onChange={(e) => setPtId(e.target.value)} className="glass-input px-3 py-2 rounded-lg text-xs max-w-xs">
        <option value="">Choose a price type…</option>{types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
      {ptId && <div className="space-y-2">
        {tiers.length === 0 && <p className="text-[11px] text-ink-tertiary">No tiers — uses the type's base markup at every quantity.</p>}
        {tiers.map((t) => (
          <div key={t.id} className="flex items-center gap-3 text-xs">
            <span className="text-ink-secondary">At qty ≥ <b>{t.min_qty}</b> → markup <b>{t.markup_pct}%</b></span>
            <button onClick={() => del(t.id)} className="text-red-500 hover:underline text-[11px]">Remove</button></div>
        ))}
        <div className="flex items-end gap-2 pt-1">
          <div className="space-y-1"><label className="block text-[11px] font-semibold text-ink-secondary">Min qty</label>
            <input type="number" min={1} value={minQty} onChange={(e) => setMinQty(e.target.value)} className="glass-input w-24 px-3 py-2 rounded-lg text-xs" /></div>
          <div className="space-y-1"><label className="block text-[11px] font-semibold text-ink-secondary">Markup %</label>
            <input type="number" value={markup} onChange={(e) => setMarkup(e.target.value)} className="glass-input w-24 px-3 py-2 rounded-lg text-xs" /></div>
          <button onClick={add} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-50">Add tier</button>
          {msg && <span className="text-[11px] text-red-500 pb-2">{msg}</span>}
        </div>
      </div>}
    </div>
  );
}

// Directory item editor (144hi): add/delete items with an optional parent + per-location
// scope, merge duplicates, and CSV import/export. Appears when a directory row is clicked.


// ── Forms & fields: pick a form, customize it in place ─────────────────────
// One pill per work-order type plus the Client / Device / Product forms; the selected form opens
// in FormCustomizer (drag to reorder, click to edit, add a field where it belongs).
function FormsDesigner({ types, onAddType, onDeleteType, onAdvanced }: {
  types: any[]; onAddType: () => void; onDeleteType: (t: any) => void; onAdvanced: (t: any) => void;
}) {
  const want = new URLSearchParams(window.location.search).get("form");
  const [pick, setPick] = useState<string>(() => want || "");
  const choices: { id: string; label: string; form: FormKey; typeId?: string; type?: any }[] = [
    ...types.map((t) => ({ id: `type:${t.id}`, label: `Work order · ${t.name}`, form: "order" as FormKey, typeId: t.id, type: t })),
    { id: "client", label: "Client", form: "client" }, { id: "device", label: "Device / asset", form: "device" },
    { id: "product", label: "Product", form: "product" }, { id: "lead", label: "Inquiry", form: "lead" },
  ];
  const cur = choices.find((c) => c.id === pick) || choices[0];
  const pill = (on: boolean) => `px-3.5 py-1.5 rounded-full text-xs font-semibold border transition-colors ${on ? "bg-primary text-white border-primary" : "bg-white text-ink-secondary border-slate-200 hover:border-slate-300"}`;
  return (
    <div className="max-w-6xl space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {choices.map((c) => <button key={c.id} onClick={() => setPick(c.id)} className={pill(c.id === cur?.id)}>{c.label}</button>)}
        <button onClick={onAddType} className="px-3 py-1.5 rounded-full text-xs font-semibold text-primary hover:bg-blue-50 flex items-center gap-1"><M n="add" c="text-sm" />Work order type</button>
      </div>
      {cur?.type && (
        <div className="flex flex-wrap items-center gap-3 text-xs text-ink-tertiary">
          <span>New orders start as <span className="font-semibold text-ink-secondary">{cur.type.initial_status || "new"}</span>{cur.type.is_default ? " · default type" : ""}</span>
          <button onClick={() => onAdvanced(cur.type)} className="font-semibold text-primary hover:underline">Advanced rules (show only when, upsell, block status)</button>
          {!cur.type.is_default && <button onClick={() => onDeleteType(cur.type)} className="font-semibold text-rose-600 hover:underline">Delete this type</button>}
        </div>
      )}
      {cur && <FormCustomizer key={cur.id} form={cur.form} typeId={cur.typeId} title={cur.label} />}
    </div>
  );
}
