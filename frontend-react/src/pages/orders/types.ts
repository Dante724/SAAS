export type Order = {
  id: string;
  order_number?: string;
  status?: string;
  invoice_status?: string;
  priority?: number;
  total?: number;
  is_estimate?: boolean;
  client_msg_unread?: number;
  checklists_pending?: boolean;
  client?: { id?: string; name?: string; phone?: string } | null;
  device?: { id?: string; brand?: string; model?: string; serial?: string; asset_label?: string; asset_type?: string; color?: string } | null;
  assigned_to?: { name?: string } | null;
  created_at?: string;
  scheduled_for?: string | null;
  due_at?: string | null;
};

export function deviceName(d: Order["device"]) {
  if (!d) return "—";
  return [d.brand, d.model].filter(Boolean).join(" ") || d.serial || "—";
}
// Org currency symbol — the React equivalent of legacy `fmt._sym`. MeProvider sets it
// from /api/auth/me at boot; persisted so it's correct on the very first render too.
let _sym = "$";
try { const s = localStorage.getItem("ros_cur_sym"); if (s) _sym = s; } catch { /* ignore */ }
export const currencySymbol = () => _sym;
export function setCurrencySymbol(sym?: string) {
  _sym = sym || "$";
  try { localStorage.setItem("ros_cur_sym", _sym); } catch { /* ignore */ }
}
// `sym` overrides the org default — used for per-account multi-currency displays (e.g. CashHub).
// Number grouping follows the user's locale (US/EU/Arabic), never a hardcoded region.
// `cents` always shows two decimals (balances must read 50.50, not 50.5).
export function money(n?: number, sym?: string, cents = false) {
  let loc = "en-US";
  try { const l = localStorage.getItem("ros_lang"); loc = l === "es" ? "es-ES" : l === "ar" ? "ar" : "en-US"; } catch { /* ignore */ }
  const v = n ?? 0;
  // The sign goes before the symbol: "−₹135", never "₹-135".
  return `${v < 0 ? "−" : ""}${sym ?? _sym}${Math.abs(v).toLocaleString(loc, cents ? { minimumFractionDigits: 2, maximumFractionDigits: 2 } : undefined)}`;
}
