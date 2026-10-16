import { lazy, Suspense } from "react";
import { Routes, Route } from "react-router-dom";

// Each hub is its own chunk: the first page load no longer downloads every screen (the single
// bundle was ~1.3 MB of JS before any route rendered).
const CompanyHub = lazy(() => import("./pages/company/CompanyHub"));
const CatalogHub = lazy(() => import("./pages/catalog/CatalogHub"));
const WorkOrdersHub = lazy(() => import("./pages/orders/WorkOrdersHub"));
const ClientsHub = lazy(() => import("./pages/clients/ClientsHub"));
const AssetsHub = lazy(() => import("./pages/assets/AssetsHub"));
const InventoryHub = lazy(() => import("./pages/inventory/InventoryHub"));
const SalesPOS = lazy(() => import("./pages/sales/SalesPOS"));
const FinanceHub = lazy(() => import("./pages/finance/FinanceHub"));
const InvoicesHub = lazy(() => import("./pages/invoices/InvoicesHub"));
const CashHub = lazy(() => import("./pages/cash/CashHub"));
const ReportsHub = lazy(() => import("./pages/reports/ReportsHub"));
const SettingsHub = lazy(() => import("./pages/settings/SettingsHub"));
const TasksHub = lazy(() => import("./pages/tasks/TasksHub"));
const TimesheetsHub = lazy(() => import("./pages/timesheets/TimesheetsHub"));
const MyWorkHub = lazy(() => import("./pages/timesheets/MyWorkHub"));
const InquiriesHub = lazy(() => import("./pages/inbox/InquiriesHub"));
const ChatsHub = lazy(() => import("./pages/chats/ChatsHub"));
const AppointmentsHub = lazy(() => import("./pages/appointments/AppointmentsHub"));
const MarketingHub = lazy(() => import("./pages/marketing/MarketingHub"));

// ── Route-ownership table (Strangler Fig) ─────────────────────────────
// Flip a module from "legacy" to "react" once it passes the parity gate.
export const ROUTE_OWNERS: Record<string, "legacy" | "react"> = {
  orders: "react",   // Sprint 1 — migrated
  clients: "react",  // Sprint 2 — migrated
  assets: "react",   // Sprint 2 — migrated
  inventory: "react",  // Sprint 3 — migrated
  sales: "react",      // Sprint 4 — migrated
  finance: "react",    // Sprint 5 — migrated
  cash: "react",       // Sprint 6 — migrated
  reports: "react",    // Sprint 7 — migrated
  settings: "react",   // Sprint 8 — migrated
  tasks: "react",      // Sprint 9 — migrated
  inquiries: "react",  // Sprint 9 — migrated
  chats: "react",      // Sprint 9 — migrated
  appointments: "react",   // Sprint 9 — migrated
};

export default function App() {
  return (
    <div className="min-h-screen">
      <Suspense fallback={<div className="p-10 text-ink-tertiary text-sm">Loading…</div>}>
      <Routes>
        <Route path="/" element={<CompanyHub />} />
        <Route path="/catalog" element={<CatalogHub />} />
        <Route path="/orders" element={<WorkOrdersHub />} />
        <Route path="/invoices" element={<InvoicesHub />} />
        <Route path="/clients" element={<ClientsHub />} />
        <Route path="/assets" element={<AssetsHub />} />
        <Route path="/inventory" element={<InventoryHub />} />
        <Route path="/sales" element={<SalesPOS />} />
        <Route path="/finance" element={<FinanceHub />} />
        <Route path="/cash" element={<CashHub />} />
        <Route path="/reports" element={<ReportsHub />} />
        <Route path="/settings" element={<SettingsHub />} />
        <Route path="/tasks" element={<TasksHub />} />
        <Route path="/my-work" element={<MyWorkHub />} />
        <Route path="/timesheets" element={<TimesheetsHub />} />
        <Route path="/inquiries" element={<InquiriesHub />} />
        <Route path="/chats" element={<ChatsHub />} />
        <Route path="/appointments" element={<AppointmentsHub />} />
        <Route path="/marketing" element={<MarketingHub />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
      </Suspense>
    </div>
  );
}

// Any address the app doesn't know (an old bookmark, a mistyped link).
function NotFound() {
  return (
    <div className="min-h-[60vh] grid place-items-center p-10 text-center">
      <div>
        <div className="text-sm font-semibold text-ink-primary">This page doesn't exist</div>
        <div className="text-xs text-ink-tertiary mt-1">The link may be old or mistyped.</div>
        <a href="/" className="inline-block mt-4 px-3.5 py-2 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container">Go to Home</a>
      </div>
    </div>
  );
}
