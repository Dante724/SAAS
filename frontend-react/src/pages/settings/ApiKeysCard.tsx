// API keys (Settings → Integrations). WS-C: a key can be limited to some things (inquiries,
// products…) and to reading or changing them, and can have its own requests-per-minute limit.
// The key itself is shown once, right after it's made.
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { alertDialog, confirmDialog } from "../../lib/dialog";

export const RESOURCES: [string, string][] = [
  ["inquiries", "Inquiries"], ["contacts", "Contacts"], ["orders", "Jobs"], ["estimates", "Estimates"], ["products", "Products"],
  ["services", "Services"], ["bundles", "Bundles"], ["tags", "Tags"], ["appointments", "Appointments"], ["invoices", "Invoices"],
  ["credit_notes", "Credit notes"], ["time_entries", "Time entries"], ["webhooks", "Webhooks"],
];
type Access = "all" | "read" | "some";
type Pick = Record<string, "" | "read" | "write">;

/** The scope list the server expects, from the form. */
export function scopesFrom(access: Access, pick: Pick): string {
  if (access === "all") return "read,write";
  if (access === "read") return "read";
  return Object.entries(pick).filter(([, v]) => v).map(([k, v]) => `${k}:${v}`).join(",");
}

/** "Inquiries (change), Products (read)" — readable scopes for the table. */
export function describeScopes(raw?: string | null): string {
  const s = (raw || "").split(",").map((x) => x.trim()).filter(Boolean);
  if (s.includes("*") || s.includes("write")) return "Everything";
  if (s.length === 1 && s[0] === "read") return "Read everything";
  const name = (k: string) => RESOURCES.find(([r]) => r === k)?.[1] || k;
  return s.map((x) => { const [r, a] = x.split(":"); return a ? `${name(r)} (${a === "write" ? "change" : "read"})` : name(r); }).join(", ") || "—";
}

const lbl = "block text-[11px] font-semibold text-ink-tertiary mb-1";
const field = "glass-input w-full px-3 py-2 rounded-lg text-xs";

export default function ApiKeysCard({ Card }: { Card: (p: { title: string; count?: number; children: any }) => any }) {
  const [keys, setKeys] = useState<any[] | null>(null);
  const [label, setLabel] = useState("");
  const [access, setAccess] = useState<Access>("all");
  const [pick, setPick] = useState<Pick>({});
  const [rate, setRate] = useState("");
  const [fresh, setFresh] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get<any>("/api-keys").then((r) => setKeys(r.items || r || [])).catch(() => setKeys([]));
  useEffect(() => { load(); }, []);
  const scopes = scopesFrom(access, pick);
  async function create() {
    if (!label.trim() || !scopes) return;
    setBusy(true);
    try {
      const r = await api.post<any>("/api-keys", { name: label.trim(), scopes, rate_limit_per_min: rate ? Number(rate) : null });
      setFresh(r.key || null); setLabel(""); setPick({}); setAccess("all"); setRate(""); load();
    } catch (e: any) { alertDialog(e.message); } finally { setBusy(false); }
  }
  async function revoke(id: string) {
    if (!(await confirmDialog("Revoke this key? Apps using it stop working straight away.", { danger: true, confirmLabel: "Revoke" }))) return;
    try { await api.del(`/api-keys/${id}`); load(); } catch (e: any) { alertDialog(e.message); }
  }
  return (
    <Card title="API keys" count={keys?.filter((k) => k.active !== false).length ?? undefined}>
      <div className="p-4 space-y-3 text-xs">
        {fresh && <div role="status" className="rounded-lg bg-amber-50 border border-amber-200 text-amber-800 px-3 py-2">Copy your key now. It won't be shown again:<br /><span className="tabular-nums break-all font-mono">{fresh}</span></div>}
        <div className="grid sm:grid-cols-[1fr_12rem_9rem] gap-2 items-end">
          <div><label htmlFor="key-name" className={lbl}>New key name</label><input id="key-name" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Website form" className={field} /></div>
          <div><label htmlFor="key-access" className={lbl}>What it can do</label>
            <select id="key-access" value={access} onChange={(e) => setAccess(e.target.value as Access)} className={field}>
              <option value="all">Read and change everything</option><option value="read">Read everything</option><option value="some">Only some things</option>
            </select></div>
          <div><label htmlFor="key-rate" className={lbl}>Requests a minute</label><input id="key-rate" type="number" min={1} max={6000} value={rate} onChange={(e) => setRate(e.target.value)} placeholder="120" className={field} /></div>
        </div>
        {access === "some" && (
          <div className="grid sm:grid-cols-2 gap-x-4 gap-y-1.5 p-3 rounded-xl border border-slate-200 bg-slate-50/80">
            {RESOURCES.map(([k, name]) => (
              <div key={k} className="flex items-center justify-between gap-2">
                <label htmlFor={`scope-${k}`} className="text-ink-secondary font-semibold">{name}</label>
                <select id={`scope-${k}`} value={pick[k] || ""} onChange={(e) => setPick({ ...pick, [k]: e.target.value as any })} className="glass-input px-2 py-1 rounded-md text-xs">
                  <option value="">No access</option><option value="read">Read</option><option value="write">Read and change</option>
                </select>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] text-ink-tertiary">{access === "some" && !scopes ? "Pick at least one thing this key can use." : "Keys limited to some things work with API version 2."}</p>
          <button onClick={create} disabled={busy || !label.trim() || !scopes} className="px-3 py-2 rounded-lg bg-primary text-white font-semibold disabled:opacity-60">Create key</button>
        </div>
      </div>
      <table className="w-full text-left text-xs border-t border-slate-100">
        <thead><tr className="text-[11px] text-ink-tertiary"><th className="px-4 py-2">Name</th><th className="px-4 py-2">Starts with</th><th className="px-4 py-2">Access</th><th className="px-4 py-2">Limit</th><th className="px-4 py-2"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody className="divide-y divide-slate-100">
          {keys?.length === 0 && <tr><td colSpan={5} className="px-4 py-4 text-ink-tertiary">No keys yet.</td></tr>}
          {keys?.map((k) => (
            <tr key={k.id} className={k.active === false ? "opacity-50" : ""}>
              <td className="px-4 py-2 font-semibold text-ink-primary">{k.name}</td>
              <td className="px-4 py-2 font-mono">{k.prefix}</td>
              <td className="px-4 py-2">{describeScopes(k.scopes)}</td>
              <td className="px-4 py-2 tabular-nums">{k.rate_limit_per_min ? `${k.rate_limit_per_min}/min` : "120/min"}</td>
              <td className="px-4 py-2 text-right">{k.active === false ? <span className="text-ink-tertiary">Revoked</span> : <button onClick={() => revoke(k.id)} className="text-rose-600 font-semibold hover:underline">Revoke</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
