import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { Supplier, money } from "../inventory/types";

/** Build 144am — the supplier facet of a Contact, reused inside the unified contact form
 * (ClientDrawer's "Supplier" tab) and by the standalone SupplierDrawer. It owns the
 * PO-facing fields (lead time, PO currency), the balance statement (bills we owe /
 * returns), supplier performance, and the price-list import. Keyed by the mirrored
 * Supplier row's id, so everything it writes stays connected to POs, the ledger,
 * cashboxes and reports. */
type Entry = { id: string; entry_type: string; amount: number; note?: string; created_at: string };
type Ledger = { supplier_balance: number; entries: Entry[] };
type Field = "" | "sku" | "barcode" | "name" | "cost_price" | "sell_price";
type ImportResult = { dry_run: boolean; updated: number; created: number; skipped: number; errors: { row: number; error: string }[] };

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

export default function SupplierPanel({ supplierId, onSaved }: { supplierId: string; onSaved?: () => void }) {
  const [sub, setSub] = useState<"ledger" | "price">("ledger");
  const [f, setF] = useState<Supplier | null>(null);
  const [led, setLed] = useState<Ledger | null>(null);
  const [perf, setPerf] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 1800); };
  // 144bf — record a payment against what we owe this supplier
  const [accounts, setAccounts] = useState<{ id: string; name?: string }[]>([]);
  const [paying, setPaying] = useState(false);
  const [payAmt, setPayAmt] = useState<number>(0);
  const [payAcct, setPayAcct] = useState("");

  function reloadBalance() {
    api.get<{ items: Supplier[] }>("/suppliers").then((r) => setF((r.items || []).find((s) => s.id === supplierId) || null)).catch(() => {});
    api.get<Ledger>(`/suppliers/${supplierId}/ledger`).then(setLed).catch(() => setLed({ supplier_balance: 0, entries: [] }));
  }
  useEffect(() => {
    reloadBalance();
    api.get<any>(`/suppliers/${supplierId}/performance`).then(setPerf).catch(() => {});
    api.get<{ items: { id: string; name?: string }[] }>("/cash/accounts").then((r) => { setAccounts(r.items || []); if (r.items?.[0]) setPayAcct(r.items[0].id); }).catch(() => {});
  }, [supplierId]);

  async function paySupplier() {
    if (!payAmt || payAmt <= 0 || !payAcct) { flash("Enter an amount and account"); return; }
    setBusy(true);
    try {
      const r = await api.post<any>(`/suppliers/${supplierId}/pay`, { amount: payAmt, account_id: payAcct });
      flash(`Paid ${money(payAmt)} · balance ${money(r.balance)}`);
      setPaying(false); setPayAmt(0); reloadBalance(); onSaved?.();
    } catch (e: any) { flash(e.message || "Payment failed"); } finally { setBusy(false); }
  }

  async function saveHeader() {
    if (!f) return;
    setBusy(true);
    try {
      await api.put(`/suppliers/${supplierId}`, {
        name: f.name, contact_name: f.contact_name || null, phone: f.phone || null,
        email: f.email || null, lead_days: f.lead_days ?? null, currency: f.currency || null,
      });
      flash("Supplier details saved"); onSaved?.();
    } catch (e: any) { flash(e.message || "Couldn't save"); } finally { setBusy(false); }
  }

  const fld = "glass-input rounded-lg px-3 py-2 text-sm w-full";
  const lbl = "block text-[10px] font-bold uppercase text-ink-tertiary mb-1";

  if (!f) return <div className="text-xs text-ink-tertiary py-4">Loading supplier…</div>;

  return (
    <div className="space-y-5 relative">
      <div className="flex gap-1">
        {(["ledger", "price"] as const).map((t) => (
          <button key={t} onClick={() => setSub(t)} className={`px-3 py-1.5 rounded-lg text-xs font-bold ${sub === t ? "bg-primary text-white" : "text-ink-secondary hover:bg-slate-100"}`}>
            {t === "ledger" ? "Balance & terms" : "Price list"}</button>
        ))}
      </div>

      {sub === "ledger" && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl}>Contact person</label>
              <input value={f.contact_name || ""} onChange={(e) => setF({ ...f, contact_name: e.target.value })} className={fld} /></div>
            <div><label className={lbl}>Lead time (days)</label>
              <input type="number" min={0} value={f.lead_days ?? ""} onChange={(e) => setF({ ...f, lead_days: e.target.value === "" ? undefined : Number(e.target.value) })} className={fld} /></div>
            <div><label className={lbl}>PO currency</label>
              <input value={f.currency || ""} onChange={(e) => setF({ ...f, currency: e.target.value })} placeholder="$" className={fld} /></div>
          </div>
          <div className="flex justify-end"><button onClick={saveHeader} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : "Save supplier details"}</button></div>

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
              <span className="text-[10px] font-bold uppercase text-ink-tertiary tracking-wide">Statement — what we owe</span>
              <div className="flex items-center gap-2">
                <span className="text-lg font-bold text-ink-primary">{money(led?.supplier_balance ?? f.balance ?? 0)}</span>
                <button onClick={() => setPaying((v) => !v)} className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-bold text-primary border border-primary/30 hover:bg-primary/10"><M n="payments" c="text-[13px]" />Record payment</button>
              </div>
            </div>
            {paying && (
              <div className="flex items-end gap-2 mb-3 rounded-xl border border-slate-200/80 bg-white/70 p-2.5">
                <div><label className={lbl}>Amount</label><input type="number" min={0} value={payAmt || ""} onChange={(e) => setPayAmt(Math.max(0, Number(e.target.value)))} className="glass-input rounded-lg px-2 py-1 text-xs w-24 text-right font-mono" /></div>
                <div className="flex-1"><label className={lbl}>From account</label>
                  <select value={payAcct} onChange={(e) => setPayAcct(e.target.value)} className="glass-input rounded-lg px-2 py-1 text-xs w-full">
                    {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select></div>
                <button onClick={paySupplier} disabled={busy} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-60">{busy ? "…" : "Pay"}</button>
              </div>
            )}
            {!led ? <div className="text-xs text-ink-tertiary py-4">Loading…</div>
              : led.entries.length === 0 ? <div className="text-xs text-ink-tertiary py-4">No ledger activity yet. Received POs and returns for this supplier post here.</div>
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

      {sub === "price" && <PriceListTab supplierId={supplierId} onDone={flash} />}

      {toast && <div className="fixed bottom-5 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2 rounded-lg shadow-clay-card z-[60]">{toast}</div>}
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
      <p className="text-xs text-ink-tertiary">Paste the supplier's price list as CSV (first row = column headers). Match on SKU or barcode — costs update on the matching products. Preview shows exactly what changes before anything is written.</p>
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
