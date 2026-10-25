import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { KeyRound } from "lucide-react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { confirmDialog } from "../../lib/dialog";
import { Drawer, ErrorNote, MoreMenu, btnGhost, btnPrimary, inputCls, labelCls } from "../invoices/billing";

// Settings › Integrations › API keys. Create a key with the access it needs (read only by
// default), an expiry, and optionally cost prices; the key is shown once. Rotate issues a new
// secret while the old one keeps working for 24 hours; Revoke stops it at once.

export type ApiKey = {
  id: string; name: string; prefix: string; scopes: string; scope_list?: string[];
  status?: "active" | "expired" | "revoked"; active: boolean;
  expires_at?: string | null; previous_expires_at?: string | null; last_used_at?: string | null;
  created_at?: string | null; created_by_name?: string | null; creator_inactive?: boolean;
};
type Fresh = { name: string; key: string; rotated: boolean };

// The API sends UTC without an offset.
const utc = (iso: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
const day = (iso?: string | null) => (iso ? utc(iso).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "");

export function accessText(k: Pick<ApiKey, "scopes" | "scope_list">): string {
  const s = new Set(k.scope_list || (k.scopes || "").split(",").map((x) => x.trim()).filter(Boolean));
  const base = s.has("write") || s.has("*") ? "Read and write" : s.has("read") ? "Read only" : "No access";
  return s.has("costs:read") ? `${base}, with cost prices` : base;
}

export function lastUsedText(iso?: string | null): string {
  if (!iso) return "Never used";
  const mins = Math.round((Date.now() - utc(iso).getTime()) / 60000);
  if (mins < 2) return "Used just now";
  if (mins < 60) return `Used ${mins} minutes ago`;
  const h = Math.round(mins / 60);
  if (h < 24) return `Used ${h} ${h === 1 ? "hour" : "hours"} ago`;
  const d = Math.round(h / 24);
  return d < 60 ? `Used ${d} ${d === 1 ? "day" : "days"} ago` : `Last used ${day(iso)}`;
}

function StatusPill({ k }: { k: ApiKey }) {
  const status = k.status || (k.active ? "active" : "revoked");
  if (status === "active") return null;
  const cls = status === "expired" ? "bg-amber-50 text-amber-800 border-amber-200" : "bg-slate-100 text-ink-secondary border-slate-200";
  return <span className={`px-2 py-0.5 rounded-full border text-[11px] font-semibold ${cls}`}>{status === "expired" ? "Expired" : "Revoked"}</span>;
}

export default function ApiKeysCard() {
  const { can } = useMe();
  const canManage = can("can_manage_settings");
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [fresh, setFresh] = useState<Fresh | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 2600); };

  const load = () => {
    setErr(null);
    api.get<{ items: ApiKey[] }>("/api-keys").then((r) => setKeys(r.items || [])).catch((e) => setErr(e.message || "Couldn't load API keys"));
  };
  useEffect(() => { if (canManage) load(); }, [canManage]);
  if (!canManage) return null;

  async function rotate(k: ApiKey) {
    if (!(await confirmDialog(`Rotate "${k.name}"? You get a new key now, and the current one keeps working for 24 hours so you can swap it in.`, { confirmLabel: "Rotate" }))) return;
    try { const r = await api.post<{ key: string }>(`/api-keys/${k.id}/rotate`, {}); setFresh({ name: k.name, key: r.key, rotated: true }); load(); }
    catch (e: any) { flash(e.message || "Couldn't rotate the key"); }
  }
  async function revoke(k: ApiKey) {
    if (!(await confirmDialog(`Revoke "${k.name}"? Anything using it stops working straight away.`, { danger: true, confirmLabel: "Revoke" }))) return;
    try { await api.del(`/api-keys/${k.id}`); flash(`${k.name} revoked`); load(); }
    catch (e: any) { flash(e.message || "Couldn't revoke the key"); }
  }

  const live = (keys || []).filter((k) => (k.status || (k.active ? "active" : "revoked")) !== "revoked");
  const gone = (keys || []).filter((k) => (k.status || (k.active ? "active" : "revoked")) === "revoked");

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      <div className="p-3.5 border-b border-slate-100 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink-primary">API keys</h3>
          {keys && <div className="text-xs text-ink-tertiary mt-0.5 tabular-nums">{live.length} {live.length === 1 ? "key" : "keys"}</div>}
        </div>
        {keys && keys.length > 0 && <button onClick={() => setCreating(true)} className={`${btnGhost} bg-white`}><M n="add" c="text-sm" />Create key</button>}
      </div>

      {!keys && !err && <div className="p-4 text-xs text-ink-tertiary">Loading API keys…</div>}
      {err && <div role="alert" className="p-4 text-xs text-rose-600 flex items-center gap-2"><M n="error" c="text-sm" />{err}<button onClick={load} className="ml-auto font-semibold text-primary hover:underline">Retry</button></div>}

      {keys && (
        <div className="p-3.5 space-y-3">
          {keys.length === 0 && (
            <div className="py-4 text-center space-y-3">
              <p className="text-xs text-ink-secondary">No API keys yet. A key lets another system, like Zapier or your own website, read your jobs and customers or add new ones.</p>
              <button onClick={() => setCreating(true)} className={btnPrimary}><M n="add" c="text-sm" />Create key</button>
            </div>
          )}
          {live.length > 0 && <KeyList keys={live} onRotate={rotate} onRevoke={revoke} />}
          {gone.length > 0 && (
            <details className="text-xs text-ink-tertiary">
              <summary className="cursor-pointer font-semibold text-ink-secondary">Revoked keys ({gone.length})</summary>
              <div className="mt-2"><KeyList keys={gone} /></div>
            </details>
          )}
          <p className="text-xs text-ink-tertiary leading-relaxed">
            Send the key as <span className="font-mono">Authorization: Bearer rok_…</span>. The full reference is at <a href="/api/v1/openapi.json" target="_blank" rel="noreferrer" className="font-semibold text-primary hover:underline">/api/v1/openapi.json</a>.
          </p>
        </div>
      )}

      {creating && <CreateKeyDrawer onClose={() => setCreating(false)} onCreated={(f) => { setCreating(false); setFresh(f); load(); }} />}
      {fresh && <ShowKeyOnce fresh={fresh} onClose={() => setFresh(null)} />}
      {msg && createPortal(<div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-drawer z-[95]">{msg}</div>, document.body)}
    </div>
  );
}

function KeyList({ keys, onRotate, onRevoke }: { keys: ApiKey[]; onRotate?: (k: ApiKey) => void; onRevoke?: (k: ApiKey) => void }) {
  return (
    <ul className="divide-y divide-slate-100 border border-slate-200/80 rounded-xl overflow-hidden">
      {keys.map((k) => {
        const active = (k.status || (k.active ? "active" : "revoked")) === "active";
        return (
          <li key={k.id} className="flex items-center gap-2.5 pl-3 pr-1.5 py-2.5">
            <KeyRound size={16} strokeWidth={2} aria-hidden className="text-ink-tertiary flex-shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-sm font-semibold text-ink-primary truncate">{k.name}</span>
                <span className="font-mono text-xs text-ink-tertiary flex-shrink-0">{k.prefix}…</span>
                <StatusPill k={k} />
              </div>
              <div className="text-xs text-ink-tertiary">
                {accessText(k)} · {lastUsedText(k.last_used_at)}
                {active && (k.expires_at ? ` · Expires ${day(k.expires_at)}` : " · Never expires")}
              </div>
              {k.previous_expires_at && active && <div className="text-xs text-ink-secondary">The previous key still works until {utc(k.previous_expires_at).toLocaleString([], { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}.</div>}
              {k.creator_inactive && active && <div className="text-xs text-amber-800">Made by {k.created_by_name || "someone"}, who no longer works here. Check it is still needed.</div>}
            </div>
            {onRotate && onRevoke && (
              <MoreMenu label={`More actions for ${k.name}`} items={[
                { label: "Rotate", icon: "autorenew", onClick: () => onRotate(k), hidden: !active },
                { label: "Revoke", icon: "block", danger: true, onClick: () => onRevoke(k) },
              ]} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

const EXPIRY: [string, number | null][] = [["30 days", 30], ["90 days", 90], ["1 year", 365], ["Never", null]];

function CreateKeyDrawer({ onClose, onCreated }: { onClose: () => void; onCreated: (f: Fresh) => void }) {
  const { can } = useMe();
  const canCosts = can("can_view_purchase_price");
  const [name, setName] = useState("");
  const [access, setAccess] = useState<"read" | "write">("read");
  const [costs, setCosts] = useState(false);
  const [expiry, setExpiry] = useState("1 year");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    if (!name.trim()) { setErr("Give the key a name, for example the app that will use it."); return; }
    setBusy(true); setErr(null);
    const scopes = access === "write" ? ["read", "write"] : ["read"];
    if (costs && canCosts) scopes.push("costs:read");
    const pick = EXPIRY.find(([l]) => l === expiry);
    const days = pick ? pick[1] : 365;   // null = never expires
    try {
      const r = await api.post<{ key: string }>("/api-keys", { name: name.trim(), scopes, expires_in_days: days });
      onCreated({ name: name.trim(), key: r.key, rotated: false });
    } catch (e: any) { setErr(e.message || "Couldn't create the key"); setBusy(false); }
  }

  const radio = (value: "read" | "write", title: string, hint: string) => (
    <label className={`flex items-start gap-2.5 p-3 rounded-xl border cursor-pointer ${access === value ? "border-primary bg-primary/5" : "border-slate-200 hover:bg-slate-50"}`}>
      <input type="radio" name="ak-access" value={value} checked={access === value} onChange={() => setAccess(value)} className="mt-0.5" />
      <span><span className="block text-sm font-semibold text-ink-primary">{title}</span><span className="block text-xs text-ink-tertiary">{hint}</span></span>
    </label>
  );

  return createPortal(
    <Drawer z={90} width={448} label="Create API key" title="Create API key" onClose={onClose}
      footer={<>
        <button onClick={onClose} className={btnGhost}>Cancel</button>
        <button onClick={create} disabled={busy} className={btnPrimary}>{busy ? "Creating…" : "Create key"}</button>
      </>}>
      <div><label htmlFor="ak-name" className={labelCls}>Name</label>
        <input id="ak-name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="e.g. Zapier" className={inputCls} /></div>
      <fieldset className="space-y-2">
        <legend className={labelCls}>Access</legend>
        {radio("read", "Read only", "See customers, jobs, estimates, products, appointments, invoices and time.")}
        {radio("write", "Read and write", "Also add customers, jobs, appointments and time entries.")}
      </fieldset>
      {canCosts && (
        <label className="flex items-start gap-2.5 text-sm text-ink-primary cursor-pointer">
          <input type="checkbox" checked={costs} onChange={(e) => setCosts(e.target.checked)} className="mt-0.5" />
          <span><span className="block font-semibold">Include cost prices</span><span className="block text-xs text-ink-tertiary">Product costs and staff pay rates. Leave off unless the other system needs them.</span></span>
        </label>
      )}
      <div><label htmlFor="ak-exp" className={labelCls}>Expires after</label>
        <select id="ak-exp" value={expiry} onChange={(e) => setExpiry(e.target.value)} className={inputCls}>
          {EXPIRY.map(([l]) => <option key={l} value={l}>{l}</option>)}
        </select></div>
      <ErrorNote msg={err} />
    </Drawer>, document.body);
}

function ShowKeyOnce({ fresh, onClose }: { fresh: Fresh; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(fresh.key); setCopied(true); } catch { setCopied(false); }
  }
  return createPortal(
    <Drawer z={91} width={448} label="Your new API key" title="Your new API key" sub={fresh.name} onClose={onClose}
      footer={<button onClick={onClose} className={btnPrimary}>I've saved it</button>}>
      <p className="text-sm text-ink-secondary">Copy it now and keep it somewhere safe. You won't be able to see it again.</p>
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
        <div className="font-mono text-xs text-ink-primary break-all select-all" data-testid="fresh-key">{fresh.key}</div>
      </div>
      <button onClick={copy} className={btnGhost}><M n={copied ? "check" : "content_copy"} c="text-sm" />{copied ? "Copied" : "Copy key"}</button>
      {fresh.rotated && <p className="text-xs text-ink-tertiary">The old key keeps working for 24 hours, so you have time to swap this one in.</p>}
    </Drawer>, document.body);
}
