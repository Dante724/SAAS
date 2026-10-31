import { useEffect, useRef, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import { hueText, tint } from "../../lib/tint";
import { money } from "../orders/types";
import { api } from "../../api/client";
import { deliveryText, deliveryTone, type Delivery, type Tone } from "../../lib/delivery";

/* Shared bits for the billing drawers (invoice view, record payment, credit notes). */

export type Payment = { id?: string; method: string; kind?: string; amount: number; reference?: string | null; paid_at?: string };
export type CreditNoteRef = { id: string; number?: string | null; status: string; total: number; issued_at?: string | null };
export type InvoiceLine = { description?: string; qty?: number; unit_price?: number; total?: number; item?: string | null };
export type InvoiceDetail = {
  id: string; invoice_number?: string; status?: string; display_mode?: string;
  subtotal?: number; discount?: number; tax?: number; total?: number;
  amount_paid?: number; credited?: number; balance_due?: number;
  issued_at?: string | null; due_at?: string | null; comment?: string;
  client_id?: string | null; customer?: { id: string; name?: string; email?: string } | null;
  payer?: { id: string; name?: string; email?: string } | null;
  lines?: InvoiceLine[]; orders?: { id: string; order_number?: string }[];
  payments?: Payment[]; credit_notes?: CreditNoteRef[];
};

export const DISPLAY_MODES: { key: string; label: string; hint: string }[] = [
  { key: "detailed", label: "Detailed", hint: "Every service and product as its own line" },
  { key: "simplified", label: "Simplified", hint: "One line per work order" },
  { key: "one_line", label: "One line", hint: "A single line for the whole invoice" },
];

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

export const inputCls = "w-full bg-white border border-slate-200 rounded-xl text-sm px-3 py-2 outline-none focus:border-primary focus:ring-2 focus:ring-primary/10";
export const labelCls = "block text-xs font-medium text-ink-secondary mb-1";

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
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap" style={{ background: tint(hue), color: hueText(hue) }}>{label}</span>;
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
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setPos(null); };
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
      <button ref={btn} type="button" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={!!pos} onClick={toggle}
        className="w-9 h-9 rounded-lg grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100 transition-colors"><M n="more_vert" c="text-lg" /></button>
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

/* Right-hand drawer used by every billing panel. `z` lets a drawer open on top of another. */
export function Drawer({ title, sub, onClose, children, footer, z = 50, width = 560, label }: {
  title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; z?: number; width?: number; label: string;
}) {
  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: z }} role="dialog" aria-modal="true" aria-label={label}>
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" aria-hidden="true" onClick={onClose} />
      <div className="relative max-w-full bg-white h-full shadow-drawer flex flex-col" style={{ width }}>
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-slate-200">
          <div className="min-w-0"><h2 className="text-base font-semibold text-ink-primary truncate">{title}</h2>{sub && <div className="text-xs text-ink-tertiary mt-0.5">{sub}</div>}</div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 flex-shrink-0 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">{children}</div>
        {footer && <div className="px-5 py-4 border-t border-slate-200 flex flex-wrap items-center justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

export const btnGhost = "inline-flex items-center justify-center gap-1.5 h-9 px-3.5 rounded-xl bg-white border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs disabled:opacity-50";
export const btnPrimary = "inline-flex items-center justify-center gap-1.5 h-9 px-3.5 rounded-xl bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60";
export const btnDanger = "inline-flex items-center justify-center gap-1.5 h-9 px-3.5 rounded-xl bg-white border border-rose-200 text-rose-700 hover:bg-rose-50 font-semibold text-xs disabled:opacity-50";

export function ErrorNote({ msg }: { msg: string | null }) {
  return msg ? <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{msg}</div> : null;
}
