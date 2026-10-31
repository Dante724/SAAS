import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { money } from "./types";

/** 144aq — Purchasing Cockpit: an action worklist (not a Kanban). Surfaces only what needs the
 * buyer now, each row one click from a fix. Reads /inventory/purchasing/cockpit. */
type Row = any;
type Cockpit = { buckets: Record<string, Row[]>; counts: Record<string, number> };

const d = (s?: string | null) => (s ? new Date(s).toLocaleDateString() : "—");

export default function PurchasingCockpit({ onClose, onOpenPO, onReorder }: {
  onClose: () => void; onOpenPO: (id: string) => void; onReorder: () => void;
}) {
  const [c, setC] = useState<Cockpit | null>(null);
  const load = () => api.get<Cockpit>("/inventory/purchasing/cockpit").then(setC).catch(() => setC({ buckets: {}, counts: {} }));
  useEffect(() => { load(); }, []);

  const b = c?.buckets || {};
  const has = (k: string) => (b[k] || []).length > 0;
  const empty = c && !["overdue", "arriving", "recost_needed", "awaiting_invoice", "below_min", "supplier_balances"].some(has);

  const Section = ({ k, title, icon, tone, children }: { k: string; title: string; icon: string; tone: string; children: (r: Row) => any }) => {
    const rows = b[k] || [];
    if (!rows.length) return null;
    return (
      <div className="rounded-2xl border border-slate-200/80 overflow-hidden">
        <div className={`px-4 py-2.5 flex items-center gap-2 text-xs font-bold ${tone}`}>
          <M n={icon} c="text-base" />{title}<span className="ml-auto px-1.5 py-0.5 rounded-full bg-white/70 text-[11px]">{rows.length}</span>
        </div>
        <div className="divide-y divide-slate-100">{rows.map((r, i) => <div key={i} className="px-4 py-2.5 flex items-center gap-3 text-xs hover:bg-slate-50/70">{children(r)}</div>)}</div>
      </div>
    );
  };
  const poCell = (r: Row) => (<><span className="font-mono font-bold text-ink-primary">{r.po_number}</span><span className="text-ink-tertiary truncate">{r.supplier}</span></>);
  const openBtn = (id: string, label = "Open") => <button onClick={() => onOpenPO(id)} className="ml-auto px-2.5 py-1 rounded-lg bg-primary text-white text-[11px] font-bold hover:bg-primary-container">{label}</button>;

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" aria-hidden="true" onClick={onClose} />
      <div className="relative h-full w-full max-w-[640px] bg-raised shadow-clay-card flex flex-col border-l border-slate-200/90">
        <div className="px-6 pt-5 pb-3 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-10 h-10 rounded-xl bg-blue-50 text-primary grid place-items-center"><M n="target" c="text-xl" /></div>
            <div><h2 className="font-bold text-ink-primary text-lg leading-tight">Purchasing cockpit</h2>
              <span className="text-[11px] text-ink-tertiary">What needs you right now</span></div>
          </div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-full grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
          {!c ? <div className="text-sm text-ink-tertiary">Loading…</div> : empty ? (
            <div className="grid place-items-center h-64 text-center"><div><M n="check_circle" c="text-4xl text-emerald-500" /><div className="mt-2 text-sm font-semibold text-ink-secondary">All clear — nothing needs attention.</div></div></div>
          ) : <>
            <Section k="overdue" title="Overdue" icon="warning" tone="bg-rose-50 text-rose-700">
              {(r) => (<>{poCell(r)}<span className="text-rose-600 font-semibold">{r.days_late}d late</span>{openBtn(r.po_id, "Chase / receive")}</>)}
            </Section>
            <Section k="arriving" title="Arriving this week" icon="local_shipping" tone="bg-amber-50 text-amber-700">
              {(r) => (<>{poCell(r)}<span className="text-ink-tertiary">ETA {d(r.expected_at)}</span>{openBtn(r.po_id, "Receive")}</>)}
            </Section>
            <Section k="recost_needed" title="Freight not recosted" icon="paid" tone="bg-violet-50 text-violet-700">
              {(r) => (<>{poCell(r)}<span className="text-violet-600 font-semibold">+{money(r.freight_gap)}</span>{openBtn(r.po_id, "Recost")}</>)}
            </Section>
            <Section k="awaiting_invoice" title="Awaiting supplier invoice" icon="request_quote" tone="bg-blue-50 text-primary">
              {(r) => (<>{poCell(r)}<span className="font-mono text-ink-tertiary">{money(r.total)}</span>{openBtn(r.po_id, "Add invoice")}</>)}
            </Section>
            <Section k="below_min" title="Below minimum — not on order" icon="inventory_2" tone="bg-orange-50 text-orange-700">
              {(r) => (<><span className="font-semibold text-ink-primary truncate">{r.name}</span><span className="text-ink-tertiary">{r.available}/{r.min_stock}</span><button onClick={onReorder} className="ml-auto px-2.5 py-1 rounded-lg bg-primary text-white text-[11px] font-bold hover:bg-primary-container">Create PO</button></>)}
            </Section>
            <Section k="supplier_balances" title="Supplier balances owed" icon="account_balance_wallet" tone="bg-slate-100 text-ink-secondary">
              {(r) => (<><span className="font-semibold text-ink-primary truncate">{r.name}</span><span className="ml-auto tabular-nums font-bold text-rose-600">{money(r.balance)}</span></>)}
            </Section>
          </>}
        </div>
      </div>
    </div>
  );
}
