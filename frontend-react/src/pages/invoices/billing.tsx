import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { M } from "../../components/Shell";
import { hueText, tint } from "../../lib/tint";
import { money } from "../orders/types";
import { api } from "../../api/client";
import { deliveryText, deliveryTone, type Delivery, type Tone } from "../../lib/delivery";
import { buttonClass, IconButton } from "../../components/Button";
import SharedDrawer from "../../components/Drawer";

/* Shared bits for the billing drawers (invoice view, record payment, credit notes). */

export type Payment = { id?: string; method: string; kind?: string; amount: number; reference?: string | null; paid_at?: string };
export type CreditNoteRef = { id: string; number?: string | null; status: string; total: number; issued_at?: string | null };
export type InvoiceLine = { description?: string; qty?: number; unit_price?: number; total?: number; item?: string | null };
export type InvoiceDetail = {
  id: string; invoice_number?: string; status?: string; display_mode?: string;
  subtotal?: number; discount?: number; tax?: number; total?: number;
  amount_paid?: number; credited?: number; balance_due?: number;
  issued_at?: string | null; due_at?: string | null; comment?: string;
  client_id?: string | null; location_id?: string | null; customer?: { id: string; name?: string; email?: string } | null;
  payer?: { id: string; name?: string; email?: string } | null;
  lines?: InvoiceLine[]; orders?: { id: string; order_number?: string }[];
  payments?: Payment[]; credit_notes?: CreditNoteRef[];
};

export const DISPLAY_MODES: { key: string; label: string; hint: string }[] = [
  { key: "detailed", label: "Detailed", hint: "Every service and product as its own line" },
  { key: "simplified", label: "Simplified", hint: "One line per work order" },
  { key: "one_line", label: "One line", hint: "A single line for the whole invoice" },
];

/* How the invoice prints its lines, as the app's segmented look (Tabs "segmented"). It stays a
   radio group: one of three, always one picked. */
export function DisplayModes({ value, onChange, disabled }: { value: string; onChange: (k: string) => void; disabled?: boolean }) {
  return (
    <div role="radiogroup" aria-label="Display on the invoice" className="inline-flex max-w-full gap-0.5 rounded-xl bg-slate-100 p-1">
      {DISPLAY_MODES.map((m) => (
        <button key={m.key} type="button" role="radio" aria-checked={value === m.key} title={m.hint} disabled={disabled} onClick={() => onChange(m.key)}
          className={`h-7 px-3 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors disabled:cursor-not-allowed ${value === m.key ? "bg-white text-ink-primary shadow-2xs" : "text-ink-secondary hover:text-ink-primary disabled:hover:text-ink-secondary"}`}>{m.label}</button>
      ))}
    </div>
  );
}

/** I6 — email or text an invoice to the customer. Returns the sentence to show: the server's
 *  delivery message, so the screen never says "sent" when it wasn't. Throws on an API error. */
export async function sendInvoice(id: string, channel?: "auto" | "email" | "sms"): Promise<{ text: string; tone?: Tone }> {
  const r = await api.post<{ ok: boolean; status?: string; error?: string | null; recipient?: string | null; delivery?: Delivery }>(
    `/finance/invoices/${id}/send`, channel && channel !== "auto" ? { channel } : {});
  const fallback = r?.ok === false ? (r.error || "The invoice wasn't sent.") : "Invoice sent to the customer.";
  return { text: deliveryText(r?.delivery, fallback), tone: deliveryTone(r?.delivery) ?? (r?.ok === false ? "warn" : undefined) };
}

export const isVoid = (s?: string) => ["cancelled", "canceled", "void", "voided"].includes(s || "");

// Money in the org currency, always with cents (the shared money() formatter, sign in front).
export function cash(n?: number | null) {
  const v = Number(n || 0);
  return `${v < 0 ? "−" : ""}${money(Math.abs(v), undefined, true)}`;
}

// Mirrors services/money.d2: round half-up to the cent.
export const round2 = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100;

export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export const fmtDate = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "—";

// The field kit's size (components/Field.tsx): 36px tall, 12px corners, sapphire focus ring.
// min-h rather than h so a textarea that shares it still grows.
export const inputCls = "w-full bg-white border border-slate-200 rounded-xl min-h-9 px-3 py-1.5 text-sm text-ink-primary outline-none hover:border-slate-300 focus:border-primary focus:ring-2 focus:ring-primary/10 disabled:opacity-60";
export { labelClass as labelCls } from "../../components/Field";

/* Days an invoice is late: only once it has a due date in the past and still owes money.
   This is the one thing on an invoice that turns red. */
export function daysLate(inv: { due_at?: string | null; balance_due?: number | null; status?: string }): number {
  if (!inv.due_at || isVoid(inv.status) || round2(inv.balance_due || 0) <= 0) return 0;
  const due = new Date(inv.due_at); due.setHours(0, 0, 0, 0);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.max(0, Math.round((today.getTime() - due.getTime()) / 86400000));
}
export const lateText = (n: number) => `${n} day${n === 1 ? "" : "s"} late`;

/* One pill for invoices and credit notes, list and drawer alike: a soft tint of the status
   hue (lib/tint keeps it readable in dark mode), sentence case, semibold. */
const STATUS_HUE: Record<string, string> = {
  draft: "#64748b", issued: "#2c72d9", partial: "#BA7517", paid: "#1D9E75",
  canceled: "#64748b", cancelled: "#64748b", void: "#64748b", voided: "#64748b",
};
export function StatusPill({ status, label }: { status?: string; label: string }) {
  const hue = STATUS_HUE[status || ""] || "#64748b";
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-2xs font-semibold whitespace-nowrap" style={{ background: tint(hue), color: hueText(hue) }}>{label}</span>;
}

/* "⋮" menu for the extra actions on a row or in a drawer footer. Items render only while it
   is open, so the closed menu adds nothing to the page. The list is placed with fixed
   coordinates so a scrolling table or drawer body never clips it; it opens upward when
   there's no room below. */
export type MenuItem = { label: string; icon?: string; onClick: () => void; danger?: boolean; disabled?: boolean; hidden?: boolean };
export function MoreMenu({ items, label = "More actions", className = "", align = "end" }: { items: MenuItem[]; label?: string; className?: string; align?: "start" | "end" }) {
  const [pos, setPos] = useState<{ left?: number; right?: number; top?: number; bottom?: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!pos) return;
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setPos(null); };
    // Escape closes the menu only, not the drawer it sits in.
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setPos(null); } };
    const close = () => setPos(null);
    document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    window.addEventListener("resize", close); window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc);
      window.removeEventListener("resize", close); window.removeEventListener("scroll", close, true);
    };
  }, [pos]);
  const shown = items.filter((i) => !i.hidden);
  if (!shown.length) return null;
  function toggle() {
    if (pos) { setPos(null); return; }
    const r = btn.current?.getBoundingClientRect();
    const vh = window.innerHeight || 800, vw = window.innerWidth || 1200;
    if (!r) { setPos({ right: 16, top: 16 }); return; }
    const x = align === "start" ? { left: Math.max(8, r.left) } : { right: Math.max(8, vw - r.right) };
    setPos(vh - r.bottom < shown.length * 40 + 24 ? { ...x, bottom: vh - r.top + 4 } : { ...x, top: r.bottom + 4 });
  }
  return (
    <div ref={ref} className={`relative ${className}`} onClick={(e) => e.stopPropagation()}>
      <IconButton ref={btn} label={label} aria-haspopup="menu" aria-expanded={!!pos} onClick={toggle} icon={<M n="more_vert" c="text-lg" />} />
      {pos && (
        <div role="menu" style={{ position: "fixed", left: pos.left, right: pos.right, top: pos.top, bottom: pos.bottom }}
          className="z-[80] min-w-[200px] bg-white border border-slate-200 rounded-xl shadow-menu p-1.5">
          {shown.map((i) => (
            <button key={i.label} role="menuitem" type="button" disabled={i.disabled} onClick={() => { setPos(null); i.onClick(); }}
              className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-left text-sm whitespace-nowrap disabled:opacity-50 ${i.danger ? "text-rose-700 hover:bg-rose-50" : "text-ink-primary hover:bg-slate-50"}`}>
              {i.icon && <M n={i.icon} c={`text-base ${i.danger ? "" : "text-ink-tertiary"}`} />}{i.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* Right-hand drawer used by every billing panel: the app's one Drawer (main footer button first,
   on the left). `width` is kept for the callers that still pass one and maps to the nearest size. */
export function Drawer({ title, sub, onClose, children, footer, z = 50, width = 560, label }: {
  title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; z?: number; width?: number; label: string;
}) {
  const size = width <= 480 ? "sm" : width <= 640 ? "md" : "lg";
  return <SharedDrawer title={title} sub={sub} label={label} onClose={onClose} footer={footer} size={size} z={z}>{children}</SharedDrawer>;
}

// Kept as names for the ~14 billing screens that import them; they are the shared Button looks.
export const btnGhost = buttonClass("secondary");
export const btnPrimary = buttonClass("primary");
export const btnDanger = buttonClass("danger");

export function ErrorNote({ msg }: { msg: string | null }) {
  return msg ? <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{msg}</div> : null;
}

/* The search box every list toolbar in money and stock uses: magnifier inside, 36px, and it keeps
   its place (disabled) on a tab where there's nothing to search. */
export function SearchBox({ value, onChange, label, placeholder, disabled, className = "", onKeyDown, invalid }: {
  value: string; onChange: (v: string) => void; label: string; placeholder?: string; disabled?: boolean; className?: string;
  onKeyDown?: (e: ReactKeyboardEvent<HTMLInputElement>) => void; invalid?: boolean;
}) {
  return (
    <div className={`relative w-full sm:w-64 ${className}`}>
      <M n="search" c="absolute left-3 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" />
      <input value={value} onChange={(e) => onChange(e.target.value)} onKeyDown={onKeyDown} aria-label={label} placeholder={placeholder} disabled={disabled}
        className={`${inputCls} pl-9 disabled:cursor-not-allowed ${invalid ? "!border-rose-400 ring-2 ring-rose-400/30" : ""}`} />
    </div>
  );
}
