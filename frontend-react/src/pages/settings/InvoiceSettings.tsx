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
      <div className="px-5 pt-4 pb-3">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <h3 className="text-sm font-semibold text-ink-primary">Invoices</h3>
          {saved && <span className="inline-flex items-center gap-1 text-xs text-ink-tertiary" role="status"><M n="check" c="text-sm text-emerald-600" />{saved}</span>}
        </div>
        <p className="text-xs text-ink-tertiary mt-0.5">How new invoices show their lines. Each invoice can still be switched on its own.</p>
      </div>
      <div className="px-5 pb-5 space-y-4">
        {err && <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
        {mode === null && !err ? <div className="text-xs text-ink-tertiary">Loading…</div> : mode !== null && (
          <>
            <div role="radiogroup" aria-label="Default display on the invoice" className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {DISPLAY_MODES.map((m) => (
                <button key={m.key} type="button" role="radio" aria-checked={mode === m.key}
                  onClick={() => { setMode(m.key); save({ default_invoice_display: m.key }, "Default display saved"); }}
                  className={`text-left rounded-xl border p-3 transition-colors ${mode === m.key ? "border-primary bg-primary/5" : "border-slate-200 hover:bg-slate-50"}`}>
                  <span className={`block text-sm font-semibold ${mode === m.key ? "text-primary" : "text-ink-primary"}`}>{m.label}</span>
                  <span className="block text-xs text-ink-tertiary mt-0.5">{m.hint}</span>
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3 pt-4 border-t border-slate-100">
              <span className="min-w-0"><span id="inv-auto-draft" className="block text-sm font-semibold text-ink-primary">Draft an invoice when a work order closes</span>
                <span className="block text-xs text-ink-tertiary">The draft uses the default display above, ready to send or take payment on.</span></span>
              <button type="button" role="switch" aria-checked={autoDraft} aria-labelledby="inv-auto-draft"
                onClick={() => { const v = !autoDraft; setAutoDraft(v); save({ auto_draft_invoice_on_close: v }, "Saved"); }}
                className={`ml-auto w-10 h-6 rounded-full flex-shrink-0 relative transition-colors ${autoDraft ? "bg-primary" : "bg-slate-300"}`}>
                <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${autoDraft ? "left-[18px]" : "left-0.5"}`} />
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
