import { useEffect, useState } from "react";
import { api } from "../api/client";
import { M } from "./Shell";

/** Additional (non-base) currencies — Orderry-style.
 *  Backed by /api/finance/currencies: real Currency table with an append-only
 *  rate-history trail and best-effort auto-rate lookup. Rate convention is
 *  `1 <code> = rate <base>`. Base currency is set in Company info above. */
type Cur = { id: string; code: string; symbol: string; position: string; rate: number; updated_at?: string | null; in_use?: boolean };
type Base = { code: string; symbol: string; position: string };

const lbl = "block text-[11px] font-bold text-ink-tertiary uppercase mb-1";
const field = "glass-input w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg";

export default function CurrencyManager() {
  const [base, setBase] = useState<Base | null>(null);
  const [items, setItems] = useState<Cur[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ code: "", symbol: "", position: "before", rate: "" });
  const [looking, setLooking] = useState(false);
  const [histFor, setHistFor] = useState<Cur | null>(null);

  const load = () => api.get<{ base: Base; items: Cur[] }>("/finance/currencies")
    .then((r) => { setBase(r.base); setItems(r.items || []); })
    .catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  const autoRate = async (code: string, into: (r: string) => void) => {
    if (!code) return;
    setLooking(true);
    try { const r = await api.get<{ rate: number | null }>(`/finance/currencies/lookup-rate?code=${encodeURIComponent(code.toUpperCase())}`); if (r.rate) into(String(r.rate)); }
    catch { /* operator can always type it */ } finally { setLooking(false); }
  };

  const add = async () => {
    setErr(null);
    const code = form.code.trim().toUpperCase();
    if (!code) { setErr("Currency code is required"); return; }
    try {
      await api.post("/finance/currencies", { code, symbol: form.symbol.trim() || code, position: form.position, rate: parseFloat(form.rate) || 1 });
      setForm({ code: "", symbol: "", position: "before", rate: "" }); setAdding(false); load();
    } catch (e: any) { setErr(e.message || "Could not add currency"); }
  };
  const saveRate = async (c: Cur, rate: number) => {
    try { await api.patch(`/finance/currencies/${c.id}`, { rate }); load(); }
    catch (e: any) { setErr(e.message); }
  };
  const saveField = async (c: Cur, patch: Partial<Cur>) => {
    try { await api.patch(`/finance/currencies/${c.id}`, patch); load(); }
    catch (e: any) { setErr(e.message); }
  };
  const remove = async (c: Cur) => {
    setErr(null);
    try { await api.del(`/finance/currencies/${c.id}`); load(); }
    catch (e: any) { setErr(e.message || "Could not remove"); }
  };

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-bold text-ink-primary">Additional currencies</h3>
          <p className="text-[11px] text-ink-tertiary">
            Work in more than your base currency ({base ? `${base.code} ${base.symbol}` : "—"}).
            The rate is <span className="font-semibold">1 unit = rate {base?.code || "base"}</span>; every change is kept in a history trail.
          </p>
        </div>
        {!adding && <button onClick={() => setAdding(true)} className="px-3 py-1.5 rounded-lg bg-primary/10 text-primary text-xs font-bold hover:bg-primary/15 flex items-center gap-1"><M n="add" c="text-sm" />Add currency</button>}
      </div>

      {err && <div className="text-[11px] font-semibold text-rose-600 bg-rose-50 rounded-lg px-3 py-1.5">{err}</div>}

      {adding && (
        <div className="rounded-xl border border-slate-200 p-3 grid grid-cols-12 gap-2 items-end bg-slate-50/50">
          <div className="col-span-3"><label className={lbl}>Code</label><input autoFocus value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} onBlur={(e) => { if (!form.rate) autoRate(e.target.value, (r) => setForm((f) => ({ ...f, rate: r }))); }} placeholder="EUR" className={`${field} font-mono`} /></div>
          <div className="col-span-2"><label className={lbl}>Symbol</label><input value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} placeholder="€" className={field} /></div>
          <div className="col-span-2"><label className={lbl}>Position</label>
            <select value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} className={field}>
              <option value="before">Before</option><option value="after">After</option>
            </select></div>
          <div className="col-span-3"><label className={lbl}>Rate → 1 = ? {base?.code}</label>
            <div className="flex gap-1">
              <input type="number" step="0.000001" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} placeholder="1.0" className={`${field} font-mono`} />
              <button aria-label="Auto-fetch today's rate" type="button" onClick={() => autoRate(form.code, (r) => setForm((f) => ({ ...f, rate: r }))) } disabled={looking || !form.code} title="Auto-fetch today's rate" className="px-2 rounded-lg border border-slate-200 text-primary hover:bg-primary/5 disabled:opacity-50"><M n={looking ? "hourglass_empty" : "auto_awesome"} c="text-sm" /></button>
            </div></div>
          <div className="col-span-2 flex gap-1">
            <button onClick={add} className="flex-1 px-2 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:brightness-110">Add</button>
            <button onClick={() => { setAdding(false); setErr(null); }} className="px-2 py-2 rounded-lg border border-slate-200 text-xs font-semibold text-ink-tertiary">Cancel</button>
          </div>
        </div>
      )}

      {items.length === 0 && !adding && <div className="text-xs text-ink-tertiary py-2">No additional currencies yet.</div>}
      {items.length > 0 && (
        <div className="divide-y divide-slate-100">
          <div className="grid grid-cols-12 gap-2 pb-1.5 text-[10px] font-bold text-ink-tertiary uppercase">
            <div className="col-span-2">Code</div><div className="col-span-2">Symbol</div><div className="col-span-2">Position</div>
            <div className="col-span-3">Rate (1 = {base?.code})</div><div className="col-span-3 text-right">Actions</div>
          </div>
          {items.map((c) => (
            <div key={c.id} className="grid grid-cols-12 gap-2 items-center py-2">
              <div className="col-span-2 font-mono text-xs font-bold text-ink-primary flex items-center gap-1">{c.code}{c.in_use && <span className="text-[9px] px-1 py-0.5 rounded bg-emerald-50 text-emerald-700 font-bold">in use</span>}</div>
              <div className="col-span-2"><input defaultValue={c.symbol} onBlur={(e) => { if (e.target.value !== c.symbol) saveField(c, { symbol: e.target.value }); }} className={field} /></div>
              <div className="col-span-2">
                <select value={c.position} onChange={(e) => saveField(c, { position: e.target.value })} className={field}>
                  <option value="before">Before</option><option value="after">After</option>
                </select></div>
              <div className="col-span-3">
                <input type="number" step="0.000001" defaultValue={c.rate} onBlur={(e) => { const v = parseFloat(e.target.value); if (v && v !== c.rate) saveRate(c, v); }} className={`${field} font-mono`} />
                {c.updated_at && <div className="text-[10px] text-ink-placeholder mt-0.5">as of {new Date(c.updated_at).toLocaleDateString()}</div>}
              </div>
              <div className="col-span-3 flex items-center justify-end gap-1">
                <button aria-label="Rate history" onClick={() => setHistFor(c)} title="Rate history" className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:bg-slate-100"><M n="history" c="text-base" /></button>
                <button aria-label="Delete" onClick={() => remove(c)} title={c.in_use ? "In use — can't remove" : "Remove"} className="w-7 h-7 rounded-lg grid place-items-center text-rose-600 hover:bg-rose-50"><M n="delete" c="text-base" /></button>
              </div>
            </div>
          ))}
        </div>
      )}

      {histFor && <RateHistory currency={histFor} onClose={() => setHistFor(null)} />}
    </div>
  );
}

function RateHistory({ currency, onClose }: { currency: Cur; onClose: () => void }) {
  const [rows, setRows] = useState<{ rate: number; changed_by?: string; changed_at?: string | null }[] | null>(null);
  useEffect(() => { api.get<{ items: any[] }>(`/finance/currencies/${currency.id}/history`).then((r) => setRows(r.items || [])).catch(() => setRows([])); }, [currency.id]);
  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-clay-card w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-bold text-ink-primary">{currency.code} rate history</h3>
          <button aria-label="Close" onClick={onClose} className="w-7 h-7 rounded-lg grid place-items-center hover:bg-slate-100"><M n="close" c="text-base" /></button>
        </div>
        {rows === null && <div className="text-xs text-ink-tertiary">Loading…</div>}
        {rows && rows.length === 0 && <div className="text-xs text-ink-tertiary">No history yet.</div>}
        {rows && rows.length > 0 && (
          <div className="divide-y divide-slate-100 max-h-72 overflow-auto">
            {rows.map((r, i) => (
              <div key={i} className="flex items-center justify-between py-2 text-xs">
                <span className="font-mono font-bold text-ink-primary">1 = {r.rate}</span>
                <span className="text-ink-tertiary">{r.changed_at ? new Date(r.changed_at).toLocaleString() : ""}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
