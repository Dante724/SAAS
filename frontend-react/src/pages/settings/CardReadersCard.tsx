import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { confirmDialog } from "../../lib/dialog";
import { Drawer, MoreMenu, btnGhost, btnPrimary, inputCls, labelCls } from "../invoices/billing";
import ModePill from "./ModePill";

// F2/F3/F4 — Settings › Integrations › Card readers. Register Stripe Terminal or Square Terminal
// readers (or a simulated one while testing locally), rename / move them, remove them, and see
// whether the shop is taking test or live money.

type ProviderState = { configured: boolean; test_mode: boolean; live_refused: boolean };
type Config = {
  providers: Record<"stripe" | "square" | "simulated", ProviderState>; test_mode: boolean;
  square_sandbox_devices: { id: string; label: string }[]; stripe_test_registration_code: string;
  webhook_paths: { stripe: string; square: string };
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

  const where = (r: Reader) => locs.find((l) => l.id === r.location_id)?.name;
  const addBtn = canManage && anyKeys ? <button onClick={() => setEdit("new")} className={btnPrimary}><M n="add" c="text-sm" />Add reader</button> : null;

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      <div className="p-3.5 border-b border-slate-100 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-ink-primary">Card readers</h3>
            {cfg && <ModePill mode={cfg.test_mode ? "test" : "live"} />}
          </div>
          {readers && <div className="text-xs text-ink-tertiary mt-0.5 tabular-nums">{readers.length} {readers.length === 1 ? "reader" : "readers"}</div>}
        </div>
        {readers && readers.length > 0 && canManage && anyKeys && <button onClick={() => setEdit("new")} className={`${btnGhost} bg-white`}><M n="add" c="text-sm" />Add reader</button>}
      </div>

      {!cfg && !err && <div className="p-4 text-xs text-ink-tertiary">Loading card readers…</div>}
      {err && <div role="alert" className="p-4 text-xs text-rose-600 flex items-center gap-2"><M n="error" c="text-sm" />{err}<button onClick={load} className="ml-auto font-semibold text-primary hover:underline">Retry</button></div>}

      {cfg && readers && (
        <div className="p-3.5 space-y-3">
          {(cfg.providers.stripe.live_refused || cfg.providers.square.live_refused) && (
            <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 leading-relaxed">Live keys are saved but refused on this server, so readers only take test payments. Use test keys, or switch on live payments in production.</p>
          )}
          {!anyKeys && <p className="text-xs text-ink-secondary">Add your Stripe or Square keys above (Integrations › Payments), then add a reader here.</p>}
          {readers.length === 0 && anyKeys && (
            <div className="py-4 text-center space-y-3">
              <p className="text-xs text-ink-secondary">No card readers yet.{canManage ? " Add one to take card payments from the POS and work orders." : ""}</p>
              {addBtn}
            </div>
          )}
          {readers.length > 0 && (
            <ul className="divide-y divide-slate-100 border border-slate-200/80 rounded-xl overflow-hidden">
              {readers.map((r) => {
                const body = (
                  <>
                    <M n="contactless" c="text-base text-ink-tertiary flex-shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold text-ink-primary truncate">{r.label}</span>
                      <span className="block text-xs text-ink-tertiary truncate">{PROVIDER[r.provider] || r.provider}{where(r) ? ` · ${where(r)}` : ""}</span>
                    </span>
                    {r.test_mode && <ModePill mode="test" />}
                  </>
                );
                return (
                  <li key={r.id} className={`flex items-center gap-1 pr-1.5 ${canManage ? "hover:bg-slate-50" : ""}`}>
                    {canManage
                      ? <button type="button" onClick={() => setEdit(r)} aria-label={`Edit ${r.label}`} className="flex-1 min-w-0 flex items-center gap-2.5 px-3 py-2.5 text-left">{body}</button>
                      : <div className="flex-1 min-w-0 flex items-center gap-2.5 px-3 py-2.5">{body}</div>}
                    {canManage && <MoreMenu label={`More actions for ${r.label}`} items={[
                      { label: "Edit", icon: "edit", onClick: () => setEdit(r) },
                      { label: "Remove", icon: "delete", danger: true, onClick: () => remove(r) },
                    ]} />}
                  </li>
                );
              })}
            </ul>
          )}
          {canManage && (cfg.providers.stripe.configured || cfg.providers.square.configured) && (
            <details className="text-xs text-ink-tertiary">
              <summary className="cursor-pointer font-semibold text-ink-secondary">Webhooks for instant results</summary>
              <div className="mt-1.5 space-y-1 break-all leading-relaxed">
                {cfg.providers.stripe.configured && <p>Stripe: <span className="font-mono">{origin}{cfg.webhook_paths.stripe}</span>, events payment intent succeeded, payment failed and canceled.</p>}
                {cfg.providers.square.configured && <p>Square: <span className="font-mono">{origin}{cfg.webhook_paths.square}</span>, event terminal checkout updated.</p>}
                <p>Without webhooks the screen still checks the reader every few seconds.</p>
              </div>
            </details>
          )}
        </div>
      )}
      {edit && cfg && <ReaderDrawer cfg={cfg} reader={edit === "new" ? null : edit} locs={locs} hasStripeReader={!!readers?.some((r) => r.provider === "stripe")}
        onClose={() => setEdit(null)} onSaved={(m) => { setEdit(null); flash(m); load(); }} />}
      {msg && createPortal(<div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-drawer z-[95]">{msg}</div>, document.body)}
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
  const field = inputCls;
  const lab = labelCls;
  const title = reader ? "Edit card reader" : "Add card reader";

  return createPortal(
    <Drawer z={90} width={448} label={title} title={title} sub={reader ? PROVIDER[reader.provider] || reader.provider : undefined} onClose={onClose}
      footer={<>
        <button onClick={onClose} className={btnGhost}>Cancel</button>
        <button onClick={save} disabled={busy || (!reader && !provider)} className={btnPrimary}>{busy ? "Saving…" : reader ? "Save" : "Add reader"}</button>
      </>}>
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
          <p className="text-xs text-ink-tertiary mt-1">{test ? `In test mode, ${cfg.stripe_test_registration_code} adds Stripe's simulated reader.` : "Shown on the reader under Settings › Generate pairing code."}</p></div>
        {!hasStripeReader && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-ink-secondary">Shop address (Stripe registers readers to a place)</p>
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
          {test && <p className="text-xs text-ink-tertiary mt-1">Square's sandbox decides the outcome by device. Sandbox charges must be 25.00 USD or less.</p>}
        </div>
      )}
      {!reader && provider === "simulated" && <p className="text-xs text-ink-tertiary">A pretend reader for trying the flow on this computer. You approve or decline each charge on screen.</p>}
      {err && <div role="alert" className="text-xs text-rose-600">{err}</div>}
    </Drawer>, document.body);
}
