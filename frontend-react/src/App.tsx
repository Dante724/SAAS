import { Routes, Route } from "react-router-dom";
import CompanyHub from "./pages/company/CompanyHub";
import CatalogHub from "./pages/catalog/CatalogHub";
import WorkOrdersHub from "./pages/orders/WorkOrdersHub";
import ClientsHub from "./pages/clients/ClientsHub";
import AssetsHub from "./pages/assets/AssetsHub";
import InventoryHub from "./pages/inventory/InventoryHub";
import SalesPOS from "./pages/sales/SalesPOS";
import FinanceHub from "./pages/finance/FinanceHub";
import CashHub from "./pages/cash/CashHub";
import ReportsHub from "./pages/reports/ReportsHub";
import SettingsHub from "./pages/settings/SettingsHub";
import TasksHub from "./pages/tasks/TasksHub";
import TimesheetsHub from "./pages/timesheets/TimesheetsHub";
import MyWorkHub from "./pages/timesheets/MyWorkHub";
import InquiriesHub from "./pages/inbox/InquiriesHub";
import ChatsHub from "./pages/chats/ChatsHub";
import AppointmentsHub from "./pages/appointments/AppointmentsHub";
import MarketingHub from "./pages/marketing/MarketingHub";

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
      <Routes>
        <Route path="/" element={<CompanyHub />} />
        <Route path="/catalog" element={<CatalogHub />} />
        <Route path="/orders" element={<WorkOrdersHub />} />
        <Route path="/invoices" element={<FinanceHub initialTab="invoices" />} />
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
        <Route path="*" element={<div className="p-10 text-ink-tertiary">This route is still served by the legacy app.</div>} />
      </Routes>
    </div>
  );
}
