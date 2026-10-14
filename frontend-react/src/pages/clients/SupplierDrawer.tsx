import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { Supplier, money } from "../inventory/types";

/** Phase 1 E — supplier detail: an editable header, a Balance ledger (bills we owe /
 * returns as a running statement), and a price-list import that reuses the backend's
 * column-mapping engine with a dry-run preview before it rewrites any cost basis. */
type Entry = { id: string; entry_type: string; amount: number; note?: string; created_at: string };
type Ledger = { supplier_balance: number; entries: Entry[] };
type Field = "" | "sku" | "barcode" | "name" | "cost_price" | "sell_price";
type ImportResult = { dry_run: boolean; updated: number; created: number; skipped: number; errors: { row: number; error: string }[] };

// tiny CSV parse — comma-separated, optional double-quotes. Good enough for pasted price lists.
function parseCsv(text: string): string[][] {
  return text.trim().split(/\r?\n/).map((line) => {
    const out: string[] = []; let cur = "", q = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
      else if (c === '"') q = true; else if (c === ",") { out.push(cur); cur = ""; } else cur += c;
    }
    out.push(cur); return out.map((s) => s.trim());
  }).filter((r) => r.some((c) => c !== ""));
}

export default function SupplierDrawer({ supplier, onClose, onSaved }: { supplier: Supplier; onClose: () => void; onSaved: () => void }) {
  const [tab, setTab] = useState<"ledger" | "price">("ledger");
  const [f, setF] = useState<Supplier>(supplier);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [led, setLed] = useState<Ledger | null>(null);

  const [perf, setPerf] = useState<any>(null);
  useEffect(() => {
    api.get<Ledger>(`/suppliers/${supplier.id}/ledger`).then(setLed).catch(() => setLed({ supplier_balance: supplier.balance ?? 0, entries: [] }));
    api.get<any>(`/suppliers/${supplier.id}/performance`).then(setPerf).catch(() => {});
  }, [supplier.id]);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 1800); };

  async function saveHeader() {
    if (!f.name?.trim()) { flash("Name is required"); return; }
    setBusy(true);
    try {
      await api.put(`/suppliers/${supplier.id}`, { name: f.name, contact_name: f.contact_name || null, phone: f.phone || null, email: f.email || null, lead_days: f.lead_days ?? null });
      flash("Saved"); onSaved();
    } catch (e: any) { flash(e.message || "Couldn't save"); } finally { setBusy(false); }
  }

  const fld = "glass-input rounded-lg px-3 py-2 text-sm w-full";
  const lbl = "block text-[10px] font-bold uppercase text-ink-tertiary mb-1";

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[680px] bg-white dark:bg-[#151b2e] shadow-clay-card flex flex-col border-l border-slate-200/90">
        {/* header */}
        <div className="px-6 pt-5 pb-3 border-b border-slate-100">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-10 h-10 rounded-xl bg-blue-50 text-primary grid place-items-center"><M n="local_shipping" c="text-xl" /></div>
              <div><h2 className="font-bold text-ink-primary text-lg leading-tight">{f.name || "Supplier"}</h2>
                <span className="text-[11px] text-ink-tertiary">Supplier · balance {money(led?.supplier_balance ?? f.balance ?? 0)}</span></div>
            </div>
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-full grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
          </div>
          <div className="flex gap-1 mt-4">
            {(["ledger", "price"] as const).map((t) => (
              <button key={t} onClick={() => setTab(t)} className={`px-3 py-1.5 rounded-lg text-xs font-bold ${tab === t ? "bg-primary text-white" : "text-ink-secondary hover:bg-slate-100"}`}>
                {t === "ledger" ? "Balance & details" : "Price list"}</button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {tab === "ledger" && (
            <>
              <div className="grid grid-cols-2 gap-3">
                {([["name", "Supplier name"], ["contact_name", "Contact person"], ["phone", "Phone"], ["email", "Email"]] as const).map(([k, l]) => (
                  <div key={k}><label className={lbl}>{l}</label>
                    <input value={(f as any)[k] || ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} className={fld} /></div>
                ))}
                <div><label className={lbl}>Lead time (days)</label>
                  <input type="number" value={f.lead_days ?? ""} onChange={(e) => setF({ ...f, lead_days: e.target.value === "" ? undefined : Number(e.target.value) })} className={fld} /></div>
              </div>
              <div className="flex justify-end"><button onClick={saveHeader} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : "Save details"}</button></div>

              {perf && perf.total_pos > 0 && (
                <div className="rounded-2xl border border-slate-200/80 p-4 space-y-3">
                  <span className="text-[10px] font-bold uppercase text-ink-tertiary tracking-wide flex items-center gap-1.5"><M n="insights" c="text-sm text-primary" />Performance</span>
                  <div className="grid grid-cols-4 gap-2 text-center">
                    {[["Spend", money(perf.total_spend)], ["On-time", perf.on_time_pct == null ? "—" : `${perf.on_time_pct}%`], ["Open POs", String(perf.open_pos)], ["POs", String(perf.total_pos)]].map(([l, v]) => (
                      <div key={l} className="rounded-xl bg-slate-50/70 py-2"><div className="text-sm font-bold text-ink-primary">{v}</div><div className="text-[10px] text-ink-tertiary uppercase">{l}</div></div>
                    ))}
                  </div>
                  {perf.price_trend?.length > 0 && (
                    <div className="space-y-1">
                      <div className="text-[10px] font-bold uppercase text-ink-tertiary">Price trend</div>
                      {perf.price_trend.map((t: any, i: number) => (
                        <div key={i} className="flex items-center justify-between text-[11px]">
                          <span className="text-ink-secondary truncate flex-1">{t.part}</span>
                          <span className="font-mono text-ink-tertiary">{money(t.first)} → {money(t.latest)}</span>
                          <span className={`font-mono font-bold w-14 text-right ${t.change_pct > 0 ? "text-rose-600" : "text-emerald-600"}`}>{t.change_pct > 0 ? "+" : ""}{t.change_pct}%</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <div className="rounded-2xl bg-slate-50/70 border border-slate-200/80 p-4">
                <div className="flex items-baseline justify-between mb-3">
                  <span className="text-[10px] font-bold uppercase text-ink-tertiary tracking-wide">Statement</span>
                  <span className="text-lg font-bold text-ink-primary">{money(led?.supplier_balance ?? 0)}</span>
                </div>
                {!led ? <div className="text-xs text-ink-tertiary py-4">Loading…</div>
                  : led.entries.length === 0 ? <div className="text-xs text-ink-tertiary py-4">No ledger activity yet. Received POs and returns for this supplier will post here.</div>
                    : <div className="divide-y divide-slate-100">
                      {led.entries.map((e) => (
                        <div key={e.id} className="flex items-center gap-3 py-2 text-xs">
                          <span className="text-ink-tertiary font-mono w-[92px] shrink-0">{(e.created_at || "").slice(0, 10)}</span>
                          <span className="text-ink-secondary flex-1 truncate">{e.note || e.entry_type}</span>
                          <span className={`font-mono font-bold ${e.amount < 0 ? "text-rose-600" : "text-emerald-600"}`}>{money(e.amount)}</span>
                        </div>
                      ))}
                    </div>}
              </div>
            </>
          )}

          {tab === "price" && <PriceListTab supplierId={supplier.id} onDone={(m) => flash(m)} />}
        </div>

        {toast && <div className="absolute bottom-5 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2 rounded-lg shadow-clay-card">{toast}</div>}
      </div>
    </div>
  );
}

function PriceListTab({ supplierId, onDone }: { supplierId: string; onDone: (m: string) => void }) {
  const [raw, setRaw] = useState("");
  const [rows, setRows] = useState<string[][] | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [map, setMap] = useState<Record<number, Field>>({});
  const [matchOn, setMatchOn] = useState<"sku" | "barcode">("sku");
  const [createMissing, setCreateMissing] = useState(false);
  const [res, setRes] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);

  function analyze() {
    const parsed = parseCsv(raw);
    if (parsed.length < 2) { onDone("Need a header row and at least one data row"); return; }
    const hs = parsed[0];
    setHeaders(hs); setRows(parsed.slice(1)); setRes(null);
    // best-effort auto-map by header name
    const guess: Record<number, Field> = {};
    hs.forEach((h, i) => {
      const k = h.toLowerCase();
      if (/sku|code|part\s*no/.test(k)) guess[i] = "sku";
      else if (/barcode|ean|upc/.test(k)) guess[i] = "barcode";
      else if (/name|desc|title/.test(k)) guess[i] = "name";
      else if (/cost|buy|wholesale/.test(k)) guess[i] = "cost_price";
      else if (/sell|retail|price|rrp/.test(k)) guess[i] = "sell_price";
    });
    setMap(guess);
  }

  async function run(dry: boolean) {
    if (!rows) return;
    const mapping: Record<string, string> = {};
    Object.entries(map).forEach(([i, field]) => { if (field) mapping[headers[+i]] = field; });
    if (!Object.values(mapping).includes(matchOn)) { onDone(`Map a column to “${matchOn}” to match on it`); return; }
    const objs = rows.map((r) => Object.fromEntries(headers.map((h, i) => [h, r[i] ?? ""])));
    setBusy(true);
    try {
      const r = await api.post<ImportResult>(`/suppliers/${supplierId}/import-price-list`, { rows: objs, mapping, match_on: matchOn, create_missing: createMissing, dry_run: dry });
      setRes(r);
      if (!dry) onDone(`Imported — ${r.updated} updated, ${r.created} created, ${r.skipped} skipped`);
    } catch (e: any) { onDone(e.message || "Import failed"); } finally { setBusy(false); }
  }

  const opts: Field[] = ["", "sku", "barcode", "name", "cost_price", "sell_price"];
  const lbl = "block text-[10px] font-bold uppercase text-ink-tertiary mb-1";

  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-tertiary">Paste the supplier's price list as CSV (first row = column headers). Match on SKU or barcode — costs are updated on the matching products. Preview shows exactly what changes before anything is written.</p>
      <div>
        <label className={lbl}>Price list (CSV)</label>
        <textarea value={raw} onChange={(e) => setRaw(e.target.value)} rows={5} placeholder="sku,name,cost&#10;OF-77,Oil filter,4.20" className="glass-input rounded-lg px-3 py-2 text-xs w-full font-mono" />
      </div>
      <button onClick={analyze} disabled={!raw.trim()} className="px-3 py-2 rounded-lg bg-slate-100 text-ink-primary text-xs font-bold hover:bg-slate-200 disabled:opacity-50">Read columns</button>

      {rows && (
        <>
          <div className="rounded-xl border border-slate-200/80 p-3 space-y-2">
            <span className="text-[10px] font-bold uppercase text-ink-tertiary">Map columns ({rows.length} rows)</span>
            {headers.map((h, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className="font-mono text-ink-secondary flex-1 truncate">{h || `col ${i + 1}`}</span>
                <span className="text-ink-tertiary">→</span>
                <select value={map[i] || ""} onChange={(e) => setMap({ ...map, [i]: e.target.value as Field })} className="glass-input rounded-lg px-2 py-1 text-xs">
                  {opts.map((o) => <option key={o} value={o}>{o === "" ? "— ignore —" : o}</option>)}
                </select>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-4 text-xs">
            <span className="text-ink-tertiary font-bold uppercase text-[10px]">Match on</span>
            {(["sku", "barcode"] as const).map((m) => (
              <label key={m} className="flex items-center gap-1 cursor-pointer"><input type="radio" checked={matchOn === m} onChange={() => setMatchOn(m)} />{m}</label>
            ))}
            <label className="flex items-center gap-1 cursor-pointer ml-auto"><input type="checkbox" checked={createMissing} onChange={(e) => setCreateMissing(e.target.checked)} />Create missing products</label>
          </div>
          <div className="flex gap-2">
            <button onClick={() => run(true)} disabled={busy} className="px-3 py-2 rounded-lg bg-slate-100 text-ink-primary text-xs font-bold hover:bg-slate-200 disabled:opacity-50">{busy ? "…" : "Preview"}</button>
            <button onClick={() => run(false)} disabled={busy || !res} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-50">Apply import</button>
          </div>
          {res && (
            <div className="rounded-xl bg-slate-50/70 border border-slate-200/80 p-3 text-xs space-y-1">
              <div className="font-bold text-ink-primary">{res.dry_run ? "Preview" : "Imported"} — {res.updated} updated · {res.created} created · {res.skipped} skipped</div>
              {res.errors.length > 0 && <div className="text-rose-600">{res.errors.slice(0, 5).map((e) => `Row ${e.row}: ${e.error}`).join(" · ")}{res.errors.length > 5 ? " …" : ""}</div>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
