import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { DISPLAY_MODES } from "../invoices/billing";

/* Settings › Finance › Invoices: the default "Display on the invoice" for new invoices, and
   whether closing a work order drafts its invoice automatically. Both live on the company
   record (GET/PUT /staff/company). */

export default function InvoiceSettings() {
  const [mode, setMode] = useState<string | null>(null);
  const [autoDraft, setAutoDraft] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => {
    api.get<any>("/staff/company").then((c) => { setMode(c?.default_invoice_display || "detailed"); setAutoDraft(!!c?.auto_draft_invoice_on_close); })
      .catch((e) => setErr(e.message || "Could not load invoice settings"));
  }, []);
  async function save(patch: Record<string, unknown>, label: string) {
    setErr(null); setSaved(null);
    try { await api.put("/staff/company", patch); setSaved(label); }
    catch (e: any) { setErr(e.message || "Could not save"); }
  }
  return (
    <section aria-label="Invoice defaults" className="mb-6 bg-white rounded-2xl border border-slate-200/90 shadow-clay-card">
      <div className="flex items-start gap-3 px-5 pt-4 pb-3">
        <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="receipt_long" c="text-lg" /></span>
        <div className="min-w-0"><h3 className="text-sm font-semibold text-ink-primary">Invoices</h3>
          <p className="text-[11px] text-ink-tertiary">How new invoices show their lines. Each invoice can still be switched on its own.</p></div>
        {saved && <span className="ml-auto text-[11px] font-semibold text-emerald-700" role="status">{saved}</span>}
      </div>
      <div className="px-5 pb-5 space-y-4">
        {err && <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
        {mode === null && !err ? <div className="text-xs text-ink-tertiary">Loading…</div> : mode !== null && (
          <>
            <div role="radiogroup" aria-label="Default display on the invoice" className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {DISPLAY_MODES.map((m) => (
                <button key={m.key} type="button" role="radio" aria-checked={mode === m.key}
                  onClick={() => { setMode(m.key); save({ default_invoice_display: m.key }, "Default display saved"); }}
                  className={`text-left rounded-xl border p-3 ${mode === m.key ? "border-primary bg-primary/5" : "border-slate-200 hover:bg-slate-50"}`}>
                  <span className={`block text-sm font-semibold ${mode === m.key ? "text-primary" : "text-ink-primary"}`}>{m.label}</span>
                  <span className="block text-[11px] text-ink-tertiary mt-0.5">{m.hint}</span>
                </button>
              ))}
            </div>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={autoDraft} onChange={(e) => { setAutoDraft(e.target.checked); save({ auto_draft_invoice_on_close: e.target.checked }, "Saved"); }} className="mt-1 accent-primary" />
              <span><span className="font-semibold text-ink-primary">Draft an invoice when a work order closes</span>
                <span className="block text-[11px] text-ink-tertiary">The draft uses the default display above, ready to send or take payment on.</span></span>
            </label>
          </>
        )}
      </div>
    </section>
  );
}
