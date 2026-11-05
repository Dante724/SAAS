import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { DISPLAY_MODES } from "../invoices/billing";
import Switch from "../../components/Switch";
import Section from "../../components/Section";
import { SettingRow } from "../../components/SettingRow";
import { settingsToast } from "./kit";

/* Settings › Finance › Invoices: the default "Display on the invoice" for new invoices, and
   whether closing a work order drafts its invoice automatically. Both live on the company
   record (GET/PUT /staff/company). Each is one choice, so each saves the moment it changes
   and confirms with the page toast. */

export default function InvoiceSettings() {
  const [mode, setMode] = useState<string | null>(null);
  const [autoDraft, setAutoDraft] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<any>("/staff/company").then((c) => { setMode(c?.default_invoice_display || "detailed"); setAutoDraft(!!c?.auto_draft_invoice_on_close); })
      .catch((e) => setErr(e.message || "Could not load invoice settings"));
  }, []);
  async function save(patch: Record<string, unknown>, label: string) {
    setErr(null);
    try { await api.put("/staff/company", patch); settingsToast(label); }
    catch (e: any) { setErr(e.message || "Could not save"); }
  }
  return (
    <Section label="Invoice defaults" icon="receipt_long" title="Invoices" description="How new invoices show their lines. Each invoice can still be switched on its own.">
      <div className="space-y-4">
        {err && <div role="alert" className="rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
        {mode === null && !err ? <div className="grid grid-cols-1 sm:grid-cols-3 gap-2" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="sk h-16 rounded-xl" />)}</div> : mode !== null && (
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
            <div className="pt-4 border-t border-slate-100">
              <SettingRow label="Draft an invoice when a work order closes" help="The draft uses the default display above, ready to send or take payment on.">
                <Switch label="Draft an invoice when a work order closes" on={autoDraft}
                  onChange={(v) => { setAutoDraft(v); save({ auto_draft_invoice_on_close: v }, "Saved"); }} />
              </SettingRow>
            </div>
          </>
        )}
      </div>
    </Section>
  );
}
