import { useEffect, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog } from "../../lib/dialog";
import Switch from "../../components/Switch";

/* Settings › Finance & numbering — document numbers, sales tax, and the business details printed
   on invoices. Same endpoints as before (/settings/numbering, /catalog/taxes, /settings/tax-rules,
   /settings/tax-exceptions, /settings/legal-entities). Number examples render as you type, so
   there's no separate Preview step. */

const DOC_LABEL: Record<string, string> = {
  order: "Work orders", invoice: "Invoices", inquiry: "Inquiries", sale: "Counter sales", po: "Purchase orders",
  transfer: "Stock transfers", estimate: "Estimates", credit_note: "Credit notes",
};
const docLabel = (d: string) => DOC_LABEL[d] || (d.charAt(0).toUpperCase() + d.slice(1)).replace(/_/g, " ");

// Mirrors backend services/numbering.render: {C} / {C:n} counter, {D} {M} {Y} date parts.
export function renderNumber(template: string, counter: number, now = new Date()) {
  return (template || "").replace(/\{(D|M|Y|C(?::(\d+))?)\}/g, (_m, tok: string, w?: string) => {
    if (tok === "D") return String(now.getDate()).padStart(2, "0");
    if (tok === "M") return String(now.getMonth() + 1).padStart(2, "0");
    if (tok === "Y") return String(now.getFullYear());
    const n = String(Math.max(0, Math.floor(counter) || 0));
    return w ? n.padStart(Number(w), "0") : n;
  });
}
// A simple pattern is PREFIX + optional {Y}- + {C:n}. Anything else is edited as a custom pattern.
type Simple = { prefix: string; year: boolean; sep?: string; digits: number };
export function parsePattern(t: string): Simple | null {
  const m = /^([^{}]*?)(?:\{Y\}([-/]?))?\{C(?::(\d+))?\}$/.exec(t || "");
  return m ? { prefix: m[1], year: /\{Y\}/.test(t), sep: m[2] ?? "-", digits: Number(m[3] || 0) } : null;
}
export const buildPattern = (p: Simple) =>
  `${p.prefix}${p.year ? `{Y}${p.sep ?? "-"}` : ""}{C${p.digits ? `:${p.digits}` : ""}}`;

const inp = "glass-input w-full rounded-xl px-3 py-2 text-sm text-ink-primary";

// `actions` sit under the title on the left, main action first.
function Card({ icon, title, hint, actions, children }: { icon: string; title: string; hint?: string; actions?: ReactNode; children?: ReactNode }) {
  return (
    <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card">
      <div className="flex items-start gap-3 px-5 pt-4 pb-3">
        <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n={icon} c="text-lg" /></span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink-primary">{title}</h3>{hint && <p className="text-xs text-ink-tertiary">{hint}</p>}
          {actions && <div className="mt-3 flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      </div>
      {children && <div className="px-5 pb-5">{children}</div>}
    </section>
  );
}
const Field = ({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) => (
  <label className="block"><span className="block text-sm font-medium text-ink-primary mb-1.5">{label}</span>{children}{hint && <span className="block text-xs text-ink-tertiary mt-1">{hint}</span>}</label>
);

const DOC_ICON: Record<string, string> = { order: "build", invoice: "receipt_long", inquiry: "contact_support", sale: "point_of_sale", po: "shopping_cart", transfer: "swap_horiz", estimate: "request_quote", credit_note: "undo" };

export default function FinanceNumbering({ currency, shop }: { currency: string; shop?: { name?: string; address?: string; phone?: string; email?: string; logo?: string | null } }) {
  const [num, setNum] = useState<any[] | null>(null);
  const [taxes, setTaxes] = useState<any[] | null>(null);
  const [ents, setEnts] = useState<any[] | null>(null);
  const [stacks, setStacks] = useState<Record<string, string[]>>({});
  const [exc, setExc] = useState<any[] | null>(null);
  const [locations, setLocations] = useState<any[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2000); };
  function reload() {
    api.get<any>("/settings/numbering").then((r) => setNum(r.items || [])).catch(() => setNum([]));
    api.get<any>("/catalog/taxes").then((r) => setTaxes(r.items || [])).catch(() => setTaxes([]));
    api.get<any>("/settings/legal-entities").then((r) => setEnts(r.items || [])).catch(() => setEnts([]));
    api.get<any>("/settings/tax-rules").then((r) => setStacks(r.stacks || {})).catch(() => setStacks({}));
    api.get<any>("/settings/tax-exceptions").then((r) => setExc(r.items || [])).catch(() => setExc([]));
  }
  useEffect(() => { reload(); api.get<any>("/staff/locations").then((r) => setLocations(r.items || r || [])).catch(() => {}); }, []);

  const cur = currency || "$";
  const inv = (num || []).find((r) => r.doc_type === "invoice" && r.scope_key === "org");
  const invNo = inv ? renderNumber(inv.template, Number(inv.next_value) || 1) : "INV-00001";
  const jobTaxes = (stacks.tickets_sales || []).map((id) => (taxes || []).find((t) => t.id === id)).filter(Boolean) as any[];
  const ent = (ents || []).find((e) => e.is_default) || (ents || [])[0];
  const lines: [string, number][] = [["Screen replacement", 189], ["Labour", 40]];
  const net = lines.reduce((a, [, v]) => a + v, 0);
  const taxLines = jobTaxes.map((t) => { const r = (Number(t.rate) || 0) / 100; return { t, amount: t.mode === "included" ? net - net / (1 + r) : net * r }; });
  const added = taxLines.filter((x) => x.t.mode !== "included").reduce((a, x) => a + x.amount, 0);
  const money = (v: number) => `${cur}${v.toFixed(2)}`;
  const go = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const taxSummary = jobTaxes.length ? `${jobTaxes.reduce((a, t) => a + (Number(t.rate) || 0), 0)}% ${jobTaxes.every((t) => t.mode === "included") ? "included" : "added"}` : "Not charged";
  const bizName = ent?.name || shop?.name || "Your shop";

  return (
    <div className="max-w-5xl pb-16">
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
      <div className="mb-5">
        <h2 className="text-base font-semibold text-ink-primary">Finance & numbering</h2>
        <p className="text-xs text-ink-tertiary">Your document numbers, the tax you charge, and who the invoice is from. The invoice on the right updates as you change things.</p>
      </div>

      <div className="grid sm:grid-cols-3 gap-3 mb-5">
        {([["sec-numbers", "tag", "Next invoice", invNo, true], ["sec-tax", "percent", "Tax on jobs", taxSummary, !jobTaxes.length], ["sec-biz", "badge", "Invoices are from", bizName, false]] as [string, string, string, string, boolean][]).map(([id, icon, label, value, mono]) => (
          <button key={id} onClick={() => go(id)} className="text-left bg-white rounded-2xl border border-slate-200/90 shadow-clay-card px-4 py-3.5 hover:border-slate-300 transition-colors">
            <div className="flex items-center gap-1.5 text-[11px] text-ink-tertiary"><M n={icon} c="text-[13px]" />{label}</div>
            <div className={`mt-1 text-base font-semibold truncate ${id === "sec-tax" && !jobTaxes.length ? "text-amber-700" : "text-ink-primary"} ${mono && id === "sec-numbers" ? "tabular-nums" : ""}`}>{value}</div>
          </button>
        ))}
      </div>

      <div className="grid xl:grid-cols-[minmax(0,1fr)_300px] gap-5 items-start">
        <div className="space-y-5 min-w-0">
          <div id="sec-numbers" className="scroll-mt-4"><Numbering rows={num} onSaved={(m) => { flash(m); reload(); }} /></div>
          <div id="sec-tax" className="scroll-mt-4"><Taxes taxes={taxes} stacks={stacks} exc={exc} locations={locations} currency={cur} onChanged={(m) => { if (m) flash(m); reload(); }} /></div>
          <div id="sec-biz" className="scroll-mt-4"><Entities ents={ents} shopName={shop?.name} onChanged={(m) => { if (m) flash(m); reload(); }} /></div>
        </div>

        <aside className="xl:sticky xl:top-4" aria-label="Invoice preview">
          <div className="text-[11px] font-semibold text-ink-tertiary mb-2 px-1">Your next invoice</div>
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="h-1.5 bg-primary" />
            <div className="p-4 text-[11px]">
              <div className="flex items-start gap-2.5">
                <span className="w-9 h-9 rounded-lg bg-slate-50 border border-slate-100 grid place-items-center overflow-hidden flex-shrink-0">
                  {shop?.logo ? <img src={shop.logo} alt="" className="w-full h-full object-contain" /> : <span className="text-xs font-semibold text-ink-secondary">{bizName.slice(0, 1).toUpperCase()}</span>}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-ink-primary truncate">{bizName}</div>
                  <div className="text-ink-tertiary leading-snug">{ent?.address || shop?.address || ""}</div>
                  {ent?.tax_id && <div className="text-ink-tertiary">Tax ID {ent.tax_id}</div>}
                </div>
                <div className="text-right flex-shrink-0">
                  <div className="text-[11px] font-semibold text-ink-tertiary">INVOICE</div>
                  <div className="tabular-nums text-xs font-semibold text-ink-primary">{invNo}</div>
                </div>
              </div>
              <div className="mt-4 space-y-1.5">
                {lines.map(([l, v]) => <div key={l} className="flex justify-between text-ink-secondary"><span>{l}</span><span>{money(v)}</span></div>)}
              </div>
              <div className="mt-3 pt-2.5 border-t border-dashed border-slate-200 space-y-1">
                <div className="flex justify-between text-ink-secondary"><span>Subtotal</span><span>{money(net)}</span></div>
                {taxLines.map(({ t, amount }) => (
                  <div key={t.id} className="flex justify-between text-ink-secondary"><span>{(() => { const n = t.name_for_document || t.name; return n.includes(`${Number(t.rate)}%`) ? n : `${n} ${Number(t.rate)}%`; })()}{t.mode === "included" ? " (included)" : ""}</span><span>{money(amount)}</span></div>
                ))}
                {!taxLines.length && <div className="flex justify-between text-amber-700"><span>No tax</span><span>{money(0)}</span></div>}
                <div className="flex justify-between text-xs font-semibold text-ink-primary pt-1.5"><span>Total</span><span>{money(net + added)}</span></div>
              </div>
              {ent?.bank_name && <div className="mt-3 text-[11px] text-ink-tertiary">Pay to {ent.bank_name}{ent.bank_account ? ` · ${ent.bank_account}` : ""}</div>}
            </div>
          </div>
          <p className="text-[11px] text-ink-tertiary text-center mt-2">Sample lines. Tax and totals follow your settings.</p>
        </aside>
      </div>
    </div>
  );
}

function Numbering({ rows, onSaved }: { rows: any[] | null; onSaved: (m: string) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const editing = (rows || []).find((r) => `${r.doc_type}:${r.scope_key}` === open);
  return (
    <Card icon="tag" title="Document numbers" hint="Each new document takes the next number. Pick one to change how it looks.">
      {!rows && <div className="text-xs text-ink-tertiary">Loading…</div>}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
        {(rows || []).map((r) => {
          const id = `${r.doc_type}:${r.scope_key}`; const on = open === id;
          return (
            <button key={id} onClick={() => setOpen(on ? null : id)} aria-pressed={on}
              className={`text-left rounded-xl border p-3 transition-colors ${on ? "border-primary bg-primary/5 ring-2 ring-primary/15" : "border-slate-200 hover:border-slate-300 hover:bg-slate-50/60"}`}>
              <div className="flex items-center gap-1.5 text-[11px] text-ink-tertiary">
                <M n={DOC_ICON[r.doc_type] || "description"} c="text-[13px]" />{docLabel(r.doc_type)}{r.scope_key !== "org" ? " · one location" : ""}
              </div>
              <div className="mt-1.5 tabular-nums text-[15px] font-semibold text-ink-primary truncate">{renderNumber(r.template, Number(r.next_value) || 1)}</div>
            </button>
          );
        })}
      </div>
      {editing && <div className="mt-3"><NumberEditor key={open!} row={editing} onClose={() => setOpen(null)} onSaved={(m) => { setOpen(null); onSaved(m); }} /></div>}
    </Card>
  );
}

function NumberEditor({ row, onClose, onSaved }: { row: any; onClose: () => void; onSaved: (m: string) => void }) {
  const parsed = parsePattern(row.template);
  const [custom, setCustom] = useState(!parsed);
  const [p, setP] = useState<Simple>(parsed || { prefix: "", year: false, digits: 5 });
  const [raw, setRaw] = useState<string>(row.template);
  const [next, setNext] = useState<string>(String(row.next_value ?? 1));
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const template = custom ? raw : buildPattern(p);
  const counter = Number(next) || 1;
  const valid = /\{C(?::\d+)?\}/.test(template);
  async function save() {
    if (!valid) { setErr("The pattern needs a counter, like {C:5}"); return; }
    setBusy(true); setErr(null);
    try {
      await api.put("/settings/numbering", { doc_type: row.doc_type, template, begins_from: counter, scope_key: row.scope_key || "org" });
      onSaved(`${docLabel(row.doc_type)} numbering saved`);
    } catch (e: any) { setErr(e?.message || "Couldn't save"); setBusy(false); }
  }
  return (
    <div className="rounded-xl bg-slate-50 ring-1 ring-slate-200 p-4 my-1 mx-1 space-y-4">
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium text-ink-primary">{docLabel(row.doc_type)}</span>
        <span className="ml-auto text-[11px] text-ink-tertiary">Next one will be</span>
        <span className="tabular-nums text-base font-semibold text-primary">{valid ? renderNumber(template, counter) : "—"}</span>
      </div>
      {custom ? (
        <Field label="Pattern" hint="{C:5} counter with 5 digits · {Y} year · {M} month · {D} day">
          <input value={raw} onChange={(e) => setRaw(e.target.value)} className={`${inp} font-mono`} aria-label="Number pattern" />
        </Field>
      ) : (
        <div className="grid sm:grid-cols-3 gap-3">
          <Field label="Starts with"><input value={p.prefix} onChange={(e) => setP({ ...p, prefix: e.target.value })} placeholder="INV-" className={`${inp} tabular-nums`} /></Field>
          <Field label="Digits">
            <select value={p.digits} onChange={(e) => setP({ ...p, digits: Number(e.target.value) })} className={inp}>
              {[0, 3, 4, 5, 6, 7].map((d) => <option key={d} value={d}>{d === 0 ? "No padding (1, 2, 3)" : `${d} (${"0".repeat(d - 1)}1)`}</option>)}
            </select>
          </Field>
          <Field label="Add the year">
            <div className="inline-flex rounded-xl bg-white border border-slate-200 p-1 w-full">
              {([[false, "No"], [true, "Yes"]] as [boolean, string][]).map(([v, l]) => (
                <button key={l} type="button" onClick={() => setP({ ...p, year: v })} aria-pressed={p.year === v}
                  className={`flex-1 px-3 py-1.5 rounded-lg text-xs font-semibold ${p.year === v ? "bg-slate-100 text-ink-primary" : "text-ink-secondary"}`}>{l}</button>
              ))}
            </div>
          </Field>
        </div>
      )}
      <div className="grid sm:grid-cols-3 gap-3 items-end">
        <Field label="Next number" hint="Can only go up from the last one issued">
          <input type="number" min={1} value={next} onChange={(e) => setNext(e.target.value)} className={`${inp} font-mono`} />
        </Field>
        <div className="sm:col-span-2 flex items-center gap-2 justify-end pb-0.5">
          <button onClick={() => { if (custom && parsePattern(raw)) setP(parsePattern(raw)!); if (!custom) setRaw(buildPattern(p)); setCustom(!custom); }}
            className="mr-auto text-[11px] font-semibold text-ink-tertiary hover:text-ink-secondary">{custom ? "Use the simple editor" : "Custom pattern"}</button>
          <button onClick={onClose} className="px-3.5 py-2 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50">Cancel</button>
          <button onClick={save} disabled={busy} className="px-4 py-2 rounded-xl bg-ink-primary text-white text-xs font-semibold hover:opacity-90 disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>
        </div>
      </div>
      {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
    </div>
  );
}

const FAMILIES: [string, string][] = [["tickets_sales", "What customers pay"], ["posting", "Stock you receive"]];

function Taxes({ taxes, stacks, exc, locations, currency, onChanged }: {
  taxes: any[] | null; stacks: Record<string, string[]>; exc: any[] | null; locations: any[]; currency: string; onChanged: (m?: string) => void;
}) {
  const [fam, setFam] = useState("tickets_sales");
  const [edit, setEdit] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);
  const list = taxes || [];
  const on = stacks[fam] || [];
  const nameOf = (id: string) => list.find((t) => t.id === id)?.name || "A removed tax";
  async function toggle(id: string) {
    const next = on.includes(id) ? on.filter((x) => x !== id) : [...on, id];
    setBusy(true);
    try { await api.put("/settings/tax-rules", { doc_family: fam, tax_ids: next }); onChanged(); }
    catch (e: any) { onChanged(e?.message || "Couldn't save"); } finally { setBusy(false); }
  }
  async function delExc(id: string) { try { await api.del(`/settings/tax-exceptions/${id}`); onChanged("Exception removed"); } catch (e: any) { onChanged(e?.message); } }
  const sample = 100;
  const addedRate = on.reduce((a, id) => { const t = list.find((x) => x.id === id); return a + (t && t.mode !== "included" ? Number(t.rate) || 0 : 0); }, 0);

  return (
    <Card icon="percent" title="Tax" hint="Add the rates you charge, then switch on the ones that apply."
      actions={<button onClick={() => setEdit({ name: "", rate: "", mode: "imposed", location_scope: "all", location_ids: [] })} className="inline-flex items-center gap-1.5 h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold hover:opacity-90"><M n="add" c="text-sm" />Add a rate</button>}>
      {taxes == null ? <div className="text-xs text-ink-tertiary">Loading…</div> : (<>
        {list.length > 0 && (
          <div className="flex items-center gap-3 mb-3">
            <div className="inline-flex rounded-xl bg-slate-100 p-1">
              {FAMILIES.map(([v, l]) => (
                <button key={v} onClick={() => { setFam(v); setEdit(null); }} aria-pressed={fam === v}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${fam === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
              ))}
            </div>
            <span className={`ml-auto text-xs ${on.length ? "text-ink-secondary" : "text-amber-700"}`}>
              {on.length ? (addedRate ? <>{currency}{sample} becomes <span className="font-semibold text-ink-primary">{currency}{(sample * (1 + addedRate / 100)).toFixed(2)}</span></> : "Tax is included in your prices") : "No tax is charged"}
            </span>
          </div>
        )}
        {list.length === 0 && !edit && (
          <div className="rounded-2xl border border-dashed border-slate-300 p-6 text-center">
            <span className="w-10 h-10 rounded-xl bg-primary/10 text-primary grid place-items-center mx-auto mb-2"><M n="percent" c="text-lg" /></span>
            <div className="text-sm text-ink-primary">Add your sales tax</div>
            <div className="text-xs text-ink-tertiary mt-0.5 mb-3">For example your state rate, or GST and PST in Canada.</div>
            <button onClick={() => setEdit({ name: "Sales tax", rate: "", mode: "imposed", location_scope: "all", location_ids: [] })} className="h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold hover:opacity-90">Add a rate</button>
          </div>
        )}
        <div className="rounded-xl border border-slate-200 divide-y divide-slate-100 overflow-hidden">
          {list.map((t) => {
            const pos = on.indexOf(t.id); const sel = pos >= 0;
            return edit?.id === t.id ? <div key={t.id} className="p-1"><TaxEditor tax={edit} locations={locations} onClose={() => setEdit(null)} onSaved={(m) => { setEdit(null); onChanged(m); }} /></div> : (
              <div key={t.id} className={`flex items-center gap-3 px-3.5 py-3 ${sel ? "bg-primary/[0.03]" : ""}`}>
                <Switch on={sel} label={`Charge ${t.name}`} disabled={busy} onChange={() => toggle(t.id)} />
                <button onClick={() => setEdit({ ...t })} className="min-w-0 flex-1 text-left group">
                  <span className="block text-sm text-ink-primary truncate group-hover:text-primary">{t.name}</span>
                  <span className="block text-[11px] text-ink-tertiary">{t.mode === "included" ? "Already included in prices" : "Added on top of prices"}{t.location_scope === "selected" ? ` · ${(t.location_ids || []).length} location${(t.location_ids || []).length === 1 ? "" : "s"}` : ""}{sel && on.length > 1 ? ` · applied ${pos + 1}${["st", "nd", "rd"][pos] || "th"}` : ""}</span>
                </button>
                <span className={`px-2.5 py-1 rounded-lg tabular-nums text-sm font-semibold ${sel ? "bg-primary/10 text-primary" : "bg-slate-100 text-ink-secondary"}`}>{Number(t.rate)}%</span>
              </div>
            );
          })}
          {edit && !edit.id && <div className="p-1"><TaxEditor tax={edit} locations={locations} onClose={() => setEdit(null)} onSaved={(m) => { setEdit(null); onChanged(m); }} /></div>}
        </div>
        {(exc || []).filter((e) => e.doc_family === fam).length > 0 && (
          <div className="mt-4">
            <div className="text-[11px] font-semibold text-ink-tertiary mb-1">Exceptions</div>
            {(exc || []).filter((e) => e.doc_family === fam).map((e) => (
              <div key={e.id} className="flex items-center gap-2 py-1.5 text-xs">
                <span className="text-ink-secondary">{e.target_type === "labor" ? "Labour" : e.target_type.charAt(0).toUpperCase() + e.target_type.slice(1)}</span>
                <span className="ml-auto font-semibold text-ink-primary">{e.exempt ? "No tax" : nameOf(e.tax_id)}</span>
                <button aria-label="Remove exception" onClick={() => delExc(e.id)} className="w-6 h-6 rounded-md grid place-items-center text-ink-tertiary hover:bg-rose-50 hover:text-rose-600"><M n="close" c="text-sm" /></button>
              </div>
            ))}
          </div>
        )}
      </>)}
    </Card>
  );
}

function TaxEditor({ tax, locations, onClose, onSaved }: { tax: any; locations: any[]; onClose: () => void; onSaved: (m: string) => void }) {
  const [t, setT] = useState<any>({ ...tax, location_ids: tax.location_ids || [] });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: any) => setT((s: any) => ({ ...s, [k]: v }));
  async function save() {
    if (!String(t.name || "").trim()) { setErr("Give the tax a name, like Texas sales tax"); return; }
    if (t.location_scope !== "all" && !t.location_ids.length) { setErr("Pick at least one location, or use it everywhere"); return; }
    setBusy(true); setErr(null);
    // The API replaces every field, so send them all back.
    const body = { name: t.name.trim(), rate: Number(t.rate) || 0, mode: t.mode || "imposed", is_default: !!t.is_default, code: t.code ?? null,
      name_for_document: t.name_for_document ?? null, location_scope: t.location_scope || "all", location_ids: t.location_scope === "all" ? [] : t.location_ids };
    try {
      if (t.id) await api.put(`/catalog/taxes/${t.id}`, body); else await api.post("/catalog/taxes", body);
      onSaved(t.id ? "Tax saved" : "Tax added");
    } catch (e: any) { setErr(e?.message || "Couldn't save"); setBusy(false); }
  }
  async function remove() {
    if (!(await confirmDialog(`Remove "${t.name}"? Documents already issued keep their tax.`, { title: "Remove tax", confirmLabel: "Remove", danger: true }))) return;
    try { await api.del(`/catalog/taxes/${t.id}`); onSaved("Tax removed"); } catch (e: any) { setErr(e?.message || "Couldn't remove"); }
  }
  return (
    <div className="rounded-xl bg-slate-50 ring-1 ring-slate-200 p-4 my-1 mx-1 space-y-4">
      <div className="grid sm:grid-cols-[minmax(0,1fr)_120px] gap-3">
        <Field label="Name"><input value={t.name} onChange={(e) => set("name", e.target.value)} placeholder="Texas sales tax" className={inp} autoFocus={!t.id} /></Field>
        <Field label="Rate"><div className="relative"><input type="number" step="0.001" min={0} value={t.rate} onChange={(e) => set("rate", e.target.value)} placeholder="8.25" className={`${inp} pr-8 font-mono`} /><span className="absolute right-3 top-2.5 text-sm text-ink-tertiary">%</span></div></Field>
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <div>
          <div className="text-sm font-medium text-ink-primary mb-1.5">On prices</div>
          <div className="inline-flex rounded-xl bg-white border border-slate-200 p-1">
            {([["imposed", "Added on top"], ["included", "Already included"]] as [string, string][]).map(([v, l]) => (
              <button key={v} type="button" onClick={() => set("mode", v)} aria-pressed={t.mode === v} className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${t.mode === v ? "bg-slate-100 text-ink-primary" : "text-ink-secondary"}`}>{l}</button>
            ))}
          </div>
        </div>
        {locations.length > 1 && (
          <div>
            <div className="text-sm font-medium text-ink-primary mb-1.5">Where</div>
            <div className="inline-flex rounded-xl bg-white border border-slate-200 p-1">
              {([["all", "Every location"], ["selected", "Some locations"]] as [string, string][]).map(([v, l]) => (
                <button key={v} type="button" onClick={() => set("location_scope", v)} aria-pressed={(t.location_scope || "all") === v} className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${(t.location_scope || "all") === v ? "bg-slate-100 text-ink-primary" : "text-ink-secondary"}`}>{l}</button>
              ))}
            </div>
          </div>
        )}
      </div>
      {t.location_scope === "selected" && (
        <div className="flex flex-wrap gap-1.5">
          {locations.map((l) => { const on = t.location_ids.includes(l.id); return (
            <button key={l.id} type="button" onClick={() => set("location_ids", on ? t.location_ids.filter((x: string) => x !== l.id) : [...t.location_ids, l.id])} aria-pressed={on}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border ${on ? "bg-white border-primary/40 text-ink-primary shadow-sm" : "border-dashed border-slate-300 text-ink-tertiary"}`}>{on && <M n="check" c="text-[12px] text-primary" />}{l.name}</button>
          ); })}
        </div>
      )}
      {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
      <div className="flex items-center gap-2">
        {t.id && <button onClick={remove} className="px-3 py-2 rounded-xl text-xs font-semibold text-rose-600 hover:bg-rose-50">Remove</button>}
        <button onClick={onClose} className="ml-auto px-3.5 py-2 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50">Cancel</button>
        <button onClick={save} disabled={busy} className="px-4 py-2 rounded-xl bg-ink-primary text-white text-xs font-semibold hover:opacity-90 disabled:opacity-60">{busy ? "Saving…" : t.id ? "Save" : "Add tax"}</button>
      </div>
    </div>
  );
}

const ENT_MORE: [string, string][] = [["vat_no", "VAT number"], ["registration_no", "Registration number"], ["bank_name", "Bank name"], ["bank_account", "Bank account"], ["phone", "Phone"], ["email", "Email"], ["director", "Owner or director"]];

function Entities({ ents, shopName, onChanged }: { ents: any[] | null; shopName?: string; onChanged: (m?: string) => void }) {
  const [edit, setEdit] = useState<any | null>(null);
  const list = ents || [];
  async function makeDefault(e: any) { try { await api.put(`/settings/legal-entities/${e.id}`, { ...e, is_default: true }); onChanged("Default updated"); } catch (er: any) { onChanged(er?.message); } }
  return (
    <Card icon="badge" title="Who the invoice is from" hint="The registered business, tax number and bank details printed on invoices.">
      {ents == null ? <div className="text-xs text-ink-tertiary">Loading…</div> : (<>
        {list.length === 0 && !edit && (
          <div className="flex items-center gap-3 rounded-xl bg-slate-50 px-4 py-3.5">
            <M n="storefront" c="text-lg text-ink-tertiary" />
            <div className="min-w-0 flex-1"><div className="text-sm text-ink-primary">{shopName || "Your shop"}</div><div className="text-[11px] text-ink-tertiary">Using your shop details from General</div></div>
            <button onClick={() => setEdit({ name: shopName || "", tax_id: "", address: "", is_default: true })} className="px-3 py-2 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-primary hover:bg-slate-50 flex-shrink-0">Add tax ID and bank details</button>
          </div>
        )}
        <div className="grid sm:grid-cols-2 gap-2.5">
          {list.map((e) => (
            edit?.id === e.id ? null : (
              <button key={e.id} onClick={() => setEdit({ ...e })} className="group text-left rounded-xl border border-slate-200 p-3.5 hover:border-slate-300 hover:bg-slate-50/60">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-ink-primary truncate">{e.name}</span>
                  {e.is_default ? <span className="ml-auto text-[11px] font-semibold px-1.5 py-0.5 rounded-md bg-emerald-50 text-emerald-700">Default</span>
                    : <span role="button" tabIndex={0} onClick={(ev) => { ev.stopPropagation(); makeDefault(e); }} onKeyDown={(ev) => { if (ev.key === "Enter") { ev.stopPropagation(); makeDefault(e); } }} className="ml-auto text-[11px] font-semibold text-primary opacity-0 group-hover:opacity-100">Make default</span>}
                </div>
                <div className="text-[11px] text-ink-tertiary mt-1 truncate">{e.tax_id ? `Tax ID ${e.tax_id}` : "No tax ID yet"}</div>
                <div className="text-[11px] text-ink-tertiary truncate">{e.address || "No address yet"}</div>
              </button>
            )
          ))}
          {list.length > 0 && !edit && (
            <button onClick={() => setEdit({ name: "", tax_id: "", address: "", is_default: false })} className="rounded-xl border border-dashed border-slate-300 p-3.5 text-xs font-semibold text-ink-secondary hover:bg-slate-50 grid place-items-center min-h-[84px]">
              <span className="flex items-center gap-1.5"><M n="add" c="text-sm" />Another business</span>
            </button>
          )}
        </div>
        {edit && <div className="mt-2"><EntityEditor ent={edit} onClose={() => setEdit(null)} onSaved={(m) => { setEdit(null); onChanged(m); }} /></div>}
      </>)}
    </Card>
  );
}

function EntityEditor({ ent, onClose, onSaved }: { ent: any; onClose: () => void; onSaved: (m: string) => void }) {
  const [e, setE] = useState<any>(ent);
  const [more, setMore] = useState(ENT_MORE.some(([k]) => ent[k]));
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: any) => setE((s: any) => ({ ...s, [k]: v }));
  async function save() {
    if (!String(e.name || "").trim()) { setErr("Add the registered business name"); return; }
    setBusy(true); setErr(null);
    try { if (e.id) await api.put(`/settings/legal-entities/${e.id}`, e); else await api.post("/settings/legal-entities", e); onSaved(e.id ? "Saved" : "Business added"); }
    catch (er: any) { setErr(er?.message || "Couldn't save"); setBusy(false); }
  }
  async function remove() {
    if (!(await confirmDialog(`Remove "${e.name}"?`, { title: "Remove business", confirmLabel: "Remove", danger: true }))) return;
    try { await api.del(`/settings/legal-entities/${e.id}`); onSaved("Removed"); } catch (er: any) { setErr(er?.message || "Couldn't remove"); }
  }
  return (
    <div className="rounded-xl bg-slate-50 ring-1 ring-slate-200 p-4 my-1 mx-1 space-y-3">
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Registered business name"><input value={e.name || ""} onChange={(x) => set("name", x.target.value)} placeholder="Fix-It Pro Repairs LLC" className={inp} autoFocus={!e.id} /></Field>
        <Field label="Tax ID" hint="EIN, GST/HST, VAT…"><input value={e.tax_id || ""} onChange={(x) => set("tax_id", x.target.value)} className={inp} /></Field>
      </div>
      <Field label="Address"><input value={e.address || ""} onChange={(x) => set("address", x.target.value)} className={inp} /></Field>
      {more ? (
        <div className="grid sm:grid-cols-2 gap-3">
          {ENT_MORE.map(([k, l]) => <Field key={k} label={l}><input value={e[k] || ""} onChange={(x) => set(k, x.target.value)} className={inp} /></Field>)}
        </div>
      ) : <button onClick={() => setMore(true)} className="text-xs font-semibold text-primary">Add bank and registration details</button>}
      {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
      <div className="flex items-center gap-2">
        {e.id && <button onClick={remove} className="px-3 py-2 rounded-xl text-xs font-semibold text-rose-600 hover:bg-rose-50">Remove</button>}
        <span className="flex items-center gap-2 text-xs text-ink-secondary"><Switch size="sm" label="Use by default" on={!!e.is_default} onChange={(v) => set("is_default", v)} />Use by default</span>
        <button onClick={onClose} className="ml-auto px-3.5 py-2 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50">Cancel</button>
        <button onClick={save} disabled={busy} className="px-4 py-2 rounded-xl bg-ink-primary text-white text-xs font-semibold hover:opacity-90 disabled:opacity-60">{busy ? "Saving…" : e.id ? "Save" : "Add business"}</button>
      </div>
    </div>
  );
}
