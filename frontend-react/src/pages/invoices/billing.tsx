import type { ReactNode } from "react";
import { M } from "../../components/Shell";
import { money } from "../orders/types";

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

export const inputCls = "w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 outline-none focus:border-primary focus:ring-2 focus:ring-primary/10";
export const labelCls = "block text-[11px] font-semibold text-ink-tertiary mb-1";

const STATUS_PILL: Record<string, string> = {
  paid: "bg-emerald-100 text-emerald-800", partial: "bg-amber-100 text-amber-800",
  issued: "bg-sky-100 text-sky-800", draft: "bg-slate-100 text-slate-700",
  canceled: "bg-rose-100 text-rose-700", cancelled: "bg-rose-100 text-rose-700",
};
export function StatusPill({ status, label }: { status?: string; label: string }) {
  return <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_PILL[status || ""] || "bg-slate-100 text-slate-700"}`}>{label}</span>;
}

/* Right-hand drawer used by every billing panel. `z` lets a drawer open on top of another. */
export function Drawer({ title, sub, onClose, children, footer, z = 50, width = 560, label }: {
  title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; z?: number; width?: number; label: string;
}) {
  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: z }} role="dialog" aria-modal="true" aria-label={label}>
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative max-w-full bg-white h-full shadow-drawer flex flex-col" style={{ width }}>
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-slate-200">
          <div className="min-w-0"><h2 className="font-bold text-ink-primary truncate">{title}</h2>{sub && <div className="text-xs text-ink-tertiary mt-0.5">{sub}</div>}</div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 flex-shrink-0 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">{children}</div>
        {footer && <div className="px-5 py-4 border-t border-slate-200 flex flex-wrap justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

export const btnGhost = "inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs disabled:opacity-50";
export const btnPrimary = "inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:brightness-110 disabled:opacity-60";
export const btnDanger = "inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-rose-200 text-rose-700 hover:bg-rose-50 font-semibold text-xs disabled:opacity-50";

export function ErrorNote({ msg }: { msg: string | null }) {
  return msg ? <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{msg}</div> : null;
}
