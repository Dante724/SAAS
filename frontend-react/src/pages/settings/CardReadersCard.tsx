import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { confirmDialog } from "../../lib/dialog";

// F2/F3/F4 — Settings › Integrations › Card readers. Register Stripe Terminal or Square Terminal
// readers (or a simulated one while testing locally), rename / move them, remove them, and see
// whether the shop is taking test or live money.

type ProviderState = { configured: boolean; test_mode: boolean; live_refused: boolean };
type Config = {
  providers: Record<"stripe" | "square" | "simulated", ProviderState>; test_mode: boolean;
  square_sandbox_devices: { id: string; label: string }[]; stripe_test_registration_code: string;
};
type Reader = { id: string; provider: string; reader_id: string; label: string; location_id?: string | null; device_type?: string | null; test_mode: boolean };
type Loc = { id: string; name: string };

const PROVIDER: Record<string, string> = { stripe: "Stripe Terminal", square: "Square Terminal", simulated: "Simulated reader" };

export default function CardReadersCard() {
  const { can } = useMe();
  const canManage = can("can_manage_terminals");
  const [cfg, setCfg] = useState<Config | null>(null);
  const [readers, setReaders] = useState<Reader[] | null>(null);
  const [locs, setLocs] = useState<Loc[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [edit, setEdit] = useState<Reader | "new" | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 2600); };

  const load = () => {
    setErr(null);
    Promise.all([api.get<Config>("/payments/terminal/config"), api.get<{ items: Reader[] }>("/payments/terminal/readers")])
      .then(([c, r]) => { setCfg(c); setReaders(r.items || []); })
      .catch((e) => setErr(e.message || "Couldn't load card readers"));
  };
  const allowed = can("can_take_card_payments") || canManage;
  useEffect(() => {
    if (!allowed) return;
    load(); api.get<{ items: Loc[] }>("/staff/locations").then((r) => setLocs(r.items || [])).catch(() => {});
  }, [allowed]);

  if (!allowed) return null;

  async function remove(r: Reader) {
    if (!(await confirmDialog(`Remove "${r.label}"? It can't take payments until you add it again.`, { danger: true, confirmLabel: "Remove" }))) return;
    try { await api.del(`/payments/terminal/readers/${r.id}`); flash(`${r.label} removed`); load(); }
    catch (e: any) { flash(e.message || "Couldn't remove the reader"); }
  }

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const anyKeys = cfg && (cfg.providers.stripe.configured || cfg.providers.square.configured || cfg.providers.simulated.configured);

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      <div className="p-3.5 border-b border-slate-100 flex items-center justify-between gap-2">
        <span className="text-xs font-bold text-ink-primary flex items-center gap-2">Card readers
          {readers && <span className="text-[11px] font-bold bg-slate-100 text-ink-secondary px-2 py-0.5 rounded-full">{readers.length}</span>}
          {cfg && (cfg.test_mode
            ? <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 text-[11px] font-bold">Test mode</span>
            : <span className="px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 text-[11px] font-bold">Live payments</span>)}
        </span>
        {canManage && anyKeys && <button onClick={() => setEdit("new")} className="text-primary text-xs font-semibold hover:underline flex items-center gap-1"><M n="add" c="text-sm" />Add reader</button>}
      </div>

      {!cfg && !err && <div className="p-4 text-xs text-ink-tertiary">Loading card readers…</div>}
      {err && <div className="p-4 text-xs text-rose-600 flex items-center gap-2"><M n="error" c="text-sm" />{err}<button onClick={load} className="ml-auto font-bold text-primary hover:underline">Retry</button></div>}

      {cfg && readers && (
        <div className="p-3.5 space-y-3">
          {(cfg.providers.stripe.live_refused || cfg.providers.square.live_refused) && (
            <p className="text-[11px] text-rose-700 bg-rose-50 border border-rose-100 rounded-lg px-2.5 py-2">Live keys are saved but refused on this server, so readers only take test payments. Use test keys, or switch on live payments in production.</p>
          )}
          {!anyKeys && <p className="text-xs text-ink-secondary">Add your Stripe or Square keys above (Integrations › Payments), then add a reader here.</p>}
          {readers.length === 0 && anyKeys && <p className="text-xs text-ink-tertiary">No card readers yet.{canManage ? " Add one to take card payments from the POS and work orders." : ""}</p>}
          {readers.length > 0 && (
            <ul className="divide-y divide-slate-100 border border-slate-200/80 rounded-xl">
              {readers.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-2 px-3 py-2.5">
                  <M n="contactless" c="text-base text-primary" />
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-bold text-ink-primary truncate">{r.label}</div>
                    <div className="text-[11px] text-ink-tertiary truncate">{PROVIDER[r.provider] || r.provider}{locs.find((l) => l.id === r.location_id) ? ` · ${locs.find((l) => l.id === r.location_id)!.name}` : ""}</div>
                  </div>
                  {r.test_mode && <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 text-[11px] font-bold">Test</span>}
                  {canManage && <>
                    <button onClick={() => setEdit(r)} className="px-2 py-1 rounded-lg text-[11px] font-semibold text-ink-secondary hover:bg-slate-100">Edit</button>
                    <button onClick={() => remove(r)} className="px-2 py-1 rounded-lg text-[11px] font-semibold text-rose-600 hover:bg-rose-50">Remove</button>
                  </>}
                </li>
              ))}
            </ul>
          )}
          {canManage && (cfg.providers.stripe.configured || cfg.providers.square.configured) && (
            <details className="text-[11px] text-ink-tertiary">
              <summary className="cursor-pointer font-semibold text-ink-secondary">Webhooks for instant results</summary>
              <div className="mt-1.5 space-y-1 break-all">
                {cfg.providers.stripe.configured && <p>Stripe: <span className="font-mono">{origin}/api/payments/stripe/webhook</span>, events payment intent succeeded, payment failed and canceled.</p>}
                {cfg.providers.square.configured && <p>Square: <span className="font-mono">{origin}/api/payments/square/webhook</span>, event terminal checkout updated.</p>}
                <p>Without webhooks the screen still checks the reader every few seconds.</p>
              </div>
            </details>
          )}
        </div>
      )}
      {edit && cfg && <ReaderDrawer cfg={cfg} reader={edit === "new" ? null : edit} locs={locs} hasStripeReader={!!readers?.some((r) => r.provider === "stripe")}
        onClose={() => setEdit(null)} onSaved={(m) => { setEdit(null); flash(m); load(); }} />}
      {msg && createPortal(<div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-drawer z-[95]">{msg}</div>, document.body)}
    </div>
  );
}

function ReaderDrawer({ cfg, reader, locs, hasStripeReader, onClose, onSaved }: {
  cfg: Config; reader: Reader | null; locs: Loc[]; hasStripeReader: boolean; onClose: () => void; onSaved: (m: string) => void;
}) {
  const options = (["stripe", "square", "simulated"] as const).filter((p) => cfg.providers[p].configured && !cfg.providers[p].live_refused);
  const [provider, setProvider] = useState<string>(reader?.provider || options[0] || "");
  const [label, setLabel] = useState(reader?.label || "");
  const [locationId, setLocationId] = useState(reader?.location_id || "");
  const p = cfg.providers[provider as keyof Config["providers"]];
  const test = p?.test_mode !== false;
  const [code, setCode] = useState(test ? cfg.stripe_test_registration_code : "");
  const [device, setDevice] = useState(cfg.square_sandbox_devices[0]?.id || "");
  const [addr, setAddr] = useState({ line1: "", city: "", state: "", postal_code: "", country: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    setBusy(true); setErr(null);
    try {
      if (reader) {
        await api.patch(`/payments/terminal/readers/${reader.id}`, { label: label.trim(), location_id: locationId || null });
        onSaved("Reader updated");
      } else {
        const body: Record<string, unknown> = { provider, label: label.trim() || "Card reader", location_id: locationId || null };
        if (provider === "stripe") { body.registration_code = code.trim(); if (!hasStripeReader) body.address = addr; }
        if (provider === "square") body.device_id = device.trim();
        await api.post("/payments/terminal/readers", body);
        onSaved("Reader added");
      }
    } catch (e: any) { setErr(e.message || "Couldn't save the reader"); } finally { setBusy(false); }
  }
  const field = "w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-1 focus:ring-primary";
  const lab = "block text-[11px] font-semibold text-ink-tertiary mb-1";

  return createPortal(
    <div className="fixed inset-0 z-[90] flex justify-end" onMouseDown={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white h-full shadow-drawer flex flex-col" onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={reader ? "Edit card reader" : "Add card reader"}>
        <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-sm font-bold text-ink-primary">{reader ? "Edit card reader" : "Add card reader"}</h3>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 grid place-items-center rounded-lg hover:bg-slate-100"><M n="close" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-3">
          {!reader && (
            <div><label htmlFor="rd-prov" className={lab}>Provider</label>
              <select id="rd-prov" value={provider} onChange={(e) => setProvider(e.target.value)} className={field}>
                {options.map((o) => <option key={o} value={o}>{PROVIDER[o]}</option>)}
              </select></div>
          )}
          <div><label htmlFor="rd-label" className={lab}>Name</label>
            <input id="rd-label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Front desk" className={field} /></div>
          <div><label htmlFor="rd-loc" className={lab}>Location</label>
            <select id="rd-loc" value={locationId} onChange={(e) => setLocationId(e.target.value)} className={field}>
              <option value="">Any location</option>
              {locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select></div>
          {!reader && provider === "stripe" && <>
            <div><label htmlFor="rd-code" className={lab}>Registration code</label>
              <input id="rd-code" value={code} onChange={(e) => setCode(e.target.value)} className={field} />
              <p className="text-[11px] text-ink-tertiary mt-1">{test ? `In test mode, ${cfg.stripe_test_registration_code} adds Stripe's simulated reader.` : "Shown on the reader under Settings › Generate pairing code."}</p></div>
            {!hasStripeReader && (
              <div className="space-y-2">
                <p className="text-[11px] font-semibold text-ink-tertiary">Shop address (Stripe registers readers to a place)</p>
                <input aria-label="Street" value={addr.line1} onChange={(e) => setAddr({ ...addr, line1: e.target.value })} placeholder="Street" className={field} />
                <div className="grid grid-cols-2 gap-2">
                  <input aria-label="City" value={addr.city} onChange={(e) => setAddr({ ...addr, city: e.target.value })} placeholder="City" className={field} />
                  <input aria-label="State or region" value={addr.state} onChange={(e) => setAddr({ ...addr, state: e.target.value })} placeholder="State / region" className={field} />
                  <input aria-label="Postal code" value={addr.postal_code} onChange={(e) => setAddr({ ...addr, postal_code: e.target.value })} placeholder="Postal code" className={field} />
                  <input aria-label="Country code" value={addr.country} maxLength={2} onChange={(e) => setAddr({ ...addr, country: e.target.value.toUpperCase() })} placeholder="Country (US)" className={field} />
                </div>
              </div>
            )}
          </>}
          {!reader && provider === "square" && (
            <div><label htmlFor="rd-dev" className={lab}>Device</label>
              {test
                ? <select id="rd-dev" value={device} onChange={(e) => setDevice(e.target.value)} className={field}>
                    {cfg.square_sandbox_devices.map((d) => <option key={d.id} value={d.id}>Sandbox: {d.label}</option>)}
                  </select>
                : <input id="rd-dev" value={device} onChange={(e) => setDevice(e.target.value)} placeholder="Device id from Square Dashboard › Devices" className={field} />}
              {test && <p className="text-[11px] text-ink-tertiary mt-1">Square's sandbox decides the outcome by device. Sandbox charges must be 25.00 USD or less.</p>}
            </div>
          )}
          {!reader && provider === "simulated" && <p className="text-[11px] text-ink-tertiary">A pretend reader for trying the flow on this computer. You approve or decline each charge on screen.</p>}
          {err && <div className="text-xs text-rose-600">{err}</div>}
        </div>
        <div className="px-5 py-3 border-t border-slate-100 flex justify-end gap-2">
          <button onClick={onClose} className="px-3 py-2 rounded-lg text-xs font-semibold text-ink-secondary hover:bg-slate-100">Cancel</button>
          <button onClick={save} disabled={busy || (!reader && !provider)} className="px-3.5 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">{busy ? "Saving…" : reader ? "Save" : "Add reader"}</button>
        </div>
      </div>
    </div>, document.body);
}
