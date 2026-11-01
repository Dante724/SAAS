import NameSuggestInput from "../../components/NameSuggestInput";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { balanceView } from "../../lib/balance";
import { sectionCard } from "../../components/Section";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useClientSearch } from "../../hooks/useClientSearch";
import { confirmDialog } from "../../lib/dialog";
import { money } from "./types";
import CustomFields from "../../components/CustomFields";
import { useTerminology, assetNoun } from "../../lib/useTerminology";
import { identifierFields } from "../../lib/assetIdentifiers";
import ScanButton from "../../components/ScanButton";
import { assetStatusTone } from "../inventory/statusTone";
import { useFormLayout, LayoutFields, reqStar, missingRequired, has } from "../../lib/formLayout";
import { loadFailed } from "../../lib/notice";
import { MembershipsCard } from "../memberships/shared";
import { MembershipDrawer } from "../memberships/MembershipsView";

const TABS = ["General", "Work orders", "History"];

type HistOrder = { id: string; order_number?: string; status?: string; total?: number; created_at?: string };
type Asset = { id?: string; owner_type?: string; owner_name?: string; client_id?: string; client_balance?: number;
  asset_type?: string; asset_label?: string; template_key?: string; brand?: string; model?: string; serial?: string; imei?: string; vin?: string; hin?: string; color?: string; condition?: string; asset_status?: string; custom_data?: Record<string, any>; history?: HistOrder[] };
type Movement = { event_type?: string; from_zone?: string; to_zone?: string; status_key?: string; note?: string; by?: string; at?: string };
type ClientLite = { id: string; name?: string; phone?: string };
const fmtDate = (s?: string) => (s ? new Date(s).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "");

// A read-only value: plain label and text, so it doesn't look like a field you can type in.
function F({ l, children }: { l: string; children: ReactNode }) {
  return (
    <div><div className="text-xs font-medium text-ink-secondary mb-1">{l}</div>
      <div className="text-sm font-semibold text-ink-primary">{children}</div></div>
  );
}

export default function AssetDrawer({ woNum, assetId, onClose, onSaved }: { woNum?: string; assetId?: string; onClose: () => void; onSaved?: () => void }) {
  const term = useTerminology();
  const nav = useNavigate();
  // Open a work order in the work orders hub (it reads ?open=<id>), and close this drawer.
  const openOrder = (id: string) => { onClose(); nav(`/orders?open=${encodeURIComponent(id)}`); };
  const creating = !assetId;
  const [tab, setTab] = useState(0);
  const [a, setA] = useState<Asset>(creating ? { owner_type: "client" } : {});
  const [moves, setMoves] = useState<Movement[] | null>(null);
  const [plan, setPlan] = useState<string | null>(null);
  useEffect(() => { if (!assetId) return; setMoves(null); api.get<Asset>(`/assets/${assetId}`).then(setA).catch(loadFailed("the asset")); }, [assetId]);
  useEffect(() => {
    if (assetId && tab === 2 && moves === null)
      api.get<{ items: Movement[] }>(`/assets/${assetId}/movements`).then((r) => setMoves(r.items || [])).catch(() => setMoves([]));
  }, [tab, assetId, moves]);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [q, setQ] = useState(""); const [picking, setPicking] = useState(false);
  const set = (k: keyof Asset, v: any) => setA((s) => ({ ...s, [k]: v }));
  const results = useClientSearch<ClientLite>(q, picking);   // opens with the newest clients, searches as you type
  const layout = useFormLayout("device");
  async function save() {
    const m = missingRequired(layout, { condition: has(a.condition), brand: has(a.brand), model: has(a.model), color: has(a.color),
      identifiers: [a.serial, a.imei, a.vin, a.hin].some(has) }, a.custom_data);
    if (m) { setToast(m); return; }
    setBusy(true);
    try {
      const body = { owner_type: a.owner_type || "client", client_id: (a.owner_type || "client") === "client" ? a.client_id : null,
        asset_type: a.asset_type, brand: a.brand,
        model: a.model, serial: a.serial, imei: a.imei, vin: a.vin, hin: a.hin, color: a.color, condition: a.condition, custom_data: a.custom_data || {} };
      if (creating) {
        if ((body.owner_type === "client") && !body.client_id) { setToast("Choose a customer first, or set the owner to Company."); setBusy(false); return; }
        await api.post(`/assets`, body);
      } else {
        await api.put(`/assets/${assetId}`, body);
      }
      setToast(creating ? "Created" : "Saved"); onSaved?.(); setTimeout(() => onClose(), 700);
    } catch (e: any) { setToast(e.message || "Couldn't save. Check the details and try again."); setBusy(false); }
  }
  async function archive() {
    if (a.asset_status === "written_off") { setToast("Already archived"); return; }
    if (!(await confirmDialog(`Archive this ${assetNoun(a, term).toLowerCase()}? Staff can restore it later.`, { danger: true, confirmLabel: "Archive" }))) return;
    setBusy(true);
    try { await api.post(`/assets/${assetId}/write-off`, { note: "Archived from asset drawer" }); setToast("Archived"); setTimeout(() => onClose(), 700); }
    catch (e: any) { setToast(e.message || "Couldn't archive. Try again."); setBusy(false); }
  }
  const title = [a.brand, a.model].filter(Boolean).join(" ") || assetNoun(a, term);
  const isClient = (a.owner_type || "client") === "client";
  const E = ({ l, k, mono, scan, req }: { l: string; k: keyof Asset; mono?: boolean; scan?: boolean; req?: string }) => (
    <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">{l}{req && reqStar(layout, req)}</label>
      <div className="flex items-center gap-1.5">
        <input value={(a[k] as string) || ""} onChange={(e) => set(k, e.target.value)} className={`glass-input rounded-xl px-3 py-2.5 text-sm font-semibold text-ink-primary w-full ${mono ? "tabular-nums" : ""}`} />
        {scan && <ScanButton title={`Scan ${l}`} onScan={(code) => set(k, code)} />}
      </div></div>
  );

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 60 }}>
      <div className="absolute inset-0 bg-ink-primary/30" aria-hidden="true" onClick={onClose} />
      <div className="relative h-full w-full max-w-[680px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <header className="px-6 pt-4 border-b border-slate-200/90 bg-white">
          <div className="flex items-center gap-2 mb-3">
            <button onClick={onClose} className="flex items-center gap-1.5 text-xs font-semibold text-primary"><M n="arrow_back" c="text-sm" /> {woNum ? `Back to ${woNum}` : "Back"}</button>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-100 text-ink-secondary border border-slate-200">{assetNoun(a, term)}</span>
            <div className="flex-1" />
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-full flex items-center justify-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
          </div>
          <div className="flex items-center gap-3 pb-3">
            <div className="w-11 h-11 rounded-xl bg-slate-100 text-ink-secondary grid place-items-center border border-slate-200"><M n="smartphone" c="text-xl" /></div>
            <div><div className="text-base font-bold text-ink-primary">{creating ? `New ${assetNoun(a, term).toLowerCase()}` : title}{!creating && a.color ? ` (${a.color})` : ""}</div><div className="text-xs text-ink-tertiary">{creating ? `Register a ${assetNoun(a, term).toLowerCase()}` : (a.owner_name ? `Owned by ${a.owner_name}` : "Company-owned")}</div></div>
          </div>
          <div className="flex gap-6 text-xs font-semibold">
            {(creating ? [TABS[0]] : TABS).map((t, i) => <button key={t} onClick={() => setTab(i)} className={`py-3 border-b-2 transition-colors ${tab === i ? "text-primary border-primary font-bold" : "text-ink-secondary border-transparent hover:text-ink-primary"}`}>{t}</button>)}
          </div>
        </header>
        <div className="flex-1 overflow-y-auto p-6 bg-canvas space-y-4">
          {tab === 0 ? (
            <>
              {/* Memberships: the plan on this boat / vehicle / device (docs/SPEC_memberships.md) */}
              {assetId && a.owner_type === "client" && a.client_id && (
                <MembershipsCard clientId={a.client_id} deviceId={assetId} onOpen={setPlan} />
              )}
              {plan && <MembershipDrawer id={plan} onClose={() => setPlan(null)} />}
              <div className="text-xs font-medium text-ink-secondary">Ownership</div>
              <div className="flex items-center gap-2">
                <div className="flex bg-slate-100 rounded-lg p-0.5 border border-slate-200/60">
                  <button onClick={() => set("owner_type", "company")} className={`px-3 py-1.5 rounded-md text-xs ${!isClient ? "font-bold bg-white text-primary shadow-xs" : "font-semibold text-ink-secondary"}`}>Company</button>
                  <button onClick={() => set("owner_type", "client")} className={`px-3 py-1.5 rounded-md text-xs ${isClient ? "font-bold bg-white text-primary shadow-xs" : "font-semibold text-ink-secondary"}`}>Customer</button>
                </div>
                <span className="text-xs text-ink-tertiary">{isClient ? "Belongs to a customer" : "Owned by the workshop"}</span>
              </div>
              {isClient && (
                <div className="relative">
                  <div className="text-xs font-medium text-ink-secondary mb-1">Customer</div>
                  {!picking ? (
                    <div className="text-sm font-semibold text-ink-primary flex items-center gap-3">
                      <span className={a.owner_name ? "" : "text-ink-tertiary font-normal"}>{a.owner_name || "No customer"}</span>
                      <button onClick={() => { setPicking(true); setQ(""); }} className="text-xs text-primary font-semibold hover:underline">{a.owner_name ? "Change" : "Choose customer"}</button>
                    </div>
                  ) : (
                    <div className="relative">
                      <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setPicking(false); }} autoComplete="off" aria-label="Search customers" placeholder="Search customer by name or phone…" className="glass-input rounded-xl px-3 py-2.5 text-sm font-semibold text-ink-primary w-full" />
                      {(results.length > 0 || q.trim()) && (
                        <div className="absolute z-10 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 max-h-72 overflow-y-auto">
                          {!q.trim() && <div className="px-2.5 pt-1 pb-0.5 text-[11px] text-ink-tertiary">Recent customers</div>}
                          {q.trim() && results.length === 0 && <div className="px-2.5 py-2 text-xs text-ink-tertiary">No customer matches "{q.trim()}"</div>}
                          {results.map((r) => <button key={r.id} onClick={() => { set("client_id", r.id); setA((s) => ({ ...s, owner_name: r.name })); setPicking(false); }} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-sm"><span className="font-semibold text-ink-primary">{r.name}</span><span className="text-ink-tertiary font-mono text-xs">{r.phone}</span></button>)}
                        </div>
                      )}
                      <button onClick={() => setPicking(false)} className="absolute right-2 top-2.5 text-xs text-ink-tertiary">Cancel</button>
                    </div>
                  )}
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                {isClient && a.client_id && (() => { const bv = balanceView(a.client_balance); return (
                  <F l="Customer balance">{a.client_balance != null
                    ? <>{money(bv.amount)} <span className={`text-xs font-semibold ${bv.className}`}>({bv.label})</span></>
                    : <span className="text-ink-tertiary font-normal">Not available</span>}</F>
                ); })()}
                <F l="Status">
                  {(() => { const tone = a.asset_status === "written_off" ? { cls: "bg-slate-100 text-ink-secondary border-slate-200/70", dot: "bg-slate-400", label: "Archived" } : assetStatusTone(a.asset_status); return (
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border ${tone.cls}`}><span className={`w-1.5 h-1.5 rounded-full ${tone.dot}`} />{tone.label}</span>
                  ); })()}
                </F>
              </div>
              <div className="text-xs font-medium text-ink-secondary">Details</div>
              {/* Built-in and custom fields in the order set in Settings → Forms → Customize. */}
              <LayoutFields layout={layout} blocks={{
                asset_type: { half: true, el: E({ l: "Type", k: "asset_type" }) },
                condition: { half: true, el: E({ l: "Condition", k: "condition", req: "condition" }) },
                brand: { half: true, el: <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Brand{reqStar(layout, "brand")}</label>
                  <NameSuggestInput field="brand" group={(a.asset_type as string) || undefined} template={(a.template_key as string) || undefined} value={(a.brand as string) || ""} onChange={(v) => set("brand", v)} className="glass-input rounded-xl px-3 py-2.5 text-sm font-semibold text-ink-primary w-full" /></div> },
                model: { half: true, el: <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Model{reqStar(layout, "model")}</label>
                  <NameSuggestInput field="model" brand={(a.brand as string) || ""} group={(a.asset_type as string) || undefined} template={(a.template_key as string) || undefined} value={(a.model as string) || ""} onChange={(v) => set("model", v)} className="glass-input rounded-xl px-3 py-2.5 text-sm font-semibold text-ink-primary w-full" /></div> },
                identifiers: { el: <div className="grid grid-cols-2 gap-3">{identifierFields(term.identifier?.allowed, term.identifier?.primary).map((f) =>
                  <div key={f.kind}>{E({ l: f.label, k: f.kind as keyof Asset, mono: true, scan: true, req: "identifiers" })}</div>)}</div> },
                color: { half: true, el: E({ l: "Color", k: "color", req: "color" }) },
              }} custom={(ids) => <CustomFields entity="device" only={ids} bare={!!ids} value={a.custom_data || {}} onChange={(cd) => set("custom_data", cd)} creating={creating} inputSize="sm" />} />
              {woNum && (<>
                <div className="text-xs font-medium text-ink-secondary">Active work order</div>
                {/* Opened from this work order, so going back is the way to open it. */}
                <button type="button" onClick={onClose} className={`${sectionCard} w-full text-left p-3.5 flex items-center gap-3 hover:border-slate-300 transition-colors`}><span className="w-9 h-9 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center"><M n="assignment" c="text-base" /></span><div className="flex-1"><div className="text-xs font-bold text-ink-primary">{woNum}</div><div className="text-[11px] text-ink-tertiary">Linked to this {assetNoun(a, term).toLowerCase()}</div></div><M n="chevron_right" c="text-ink-tertiary text-base" /></button>
              </>)}
            </>
          ) : null}

          {tab === 1 && (
            (a.history && a.history.length > 0)
              ? <div className={`${sectionCard} overflow-hidden`}>
                  <div className="grid grid-cols-[1fr_auto_auto] gap-3 px-4 py-2.5 border-b border-slate-100 text-[11px] font-bold text-ink-tertiary"><span>Work order</span><span className="text-right">Total</span><span className="text-right">Date</span></div>
                  <div className="divide-y divide-slate-100">
                    {a.history.map((o) => (
                      <button type="button" key={o.id} onClick={() => openOrder(o.id)} aria-label={`Open work order ${o.order_number || ""}`.trim()} className="w-full text-left grid grid-cols-[1fr_auto_auto] gap-3 px-4 py-2.5 items-center text-xs hover:bg-slate-50 transition-colors">
                        <div><span className="font-bold text-ink-primary font-mono">{o.order_number || o.id.slice(0, 8)}</span>{o.status && <span className="ml-2 px-1.5 py-0.5 rounded-full text-[11px] font-bold bg-slate-100 text-ink-secondary capitalize">{o.status.replace(/_/g, " ")}</span>}</div>
                        <span className="text-right tabular-nums font-semibold text-ink-primary">{money(o.total ?? 0)}</span>
                        <span className="text-right text-ink-tertiary whitespace-nowrap">{fmtDate(o.created_at)}</span>
                      </button>
                    ))}
                  </div>
                </div>
              : <p className="text-xs text-ink-tertiary">No work orders reference this {assetNoun(a, term).toLowerCase()} yet.</p>
          )}

          {tab === 2 && (
            moves === null
              ? <p className="text-xs text-ink-tertiary">Loading history…</p>
              : moves.length === 0
                ? <p className="text-xs text-ink-tertiary">No lifecycle events logged yet.</p>
                : <div className={`${sectionCard} divide-y divide-slate-100`}>
                    {moves.map((m, i) => (
                      <div key={i} className="px-4 py-3 flex items-start gap-3">
                        <span className="w-8 h-8 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center border border-slate-200 shrink-0"><M n="history" c="text-sm" /></span>
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-bold text-ink-primary capitalize">{(m.event_type || "event").replace(/_/g, " ")}{m.status_key ? ` · ${m.status_key}` : ""}</div>
                          {(m.from_zone || m.to_zone) && <div className="text-[11px] text-ink-secondary">{m.from_zone || "Unknown"} → {m.to_zone || "Unknown"}</div>}
                          {m.note && <div className="text-[11px] text-ink-tertiary">{m.note}</div>}
                          <div className="text-[11px] text-ink-tertiary mt-0.5">{m.by ? `${m.by} · ` : ""}{fmtDate(m.at)}</div>
                        </div>
                      </div>
                    ))}
                  </div>
          )}
        </div>
        <footer className="px-6 py-3.5 border-t border-slate-200/90 bg-white flex items-center gap-2">
          <button onClick={save} disabled={busy} className="h-9 px-4 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm hover:brightness-110 disabled:opacity-60">{busy ? "Saving…" : creating ? `Create ${assetNoun(a, term).toLowerCase()}` : "Save"}</button>
          <button onClick={onClose} className="h-9 px-3.5 rounded-xl text-xs font-semibold text-ink-secondary bg-white border border-slate-200 hover:bg-slate-50">Cancel</button>
          {!creating && <button onClick={archive} disabled={busy} className="ml-auto h-9 px-3 rounded-xl text-xs font-semibold text-rose-600 hover:bg-rose-50 disabled:opacity-50">Archive {assetNoun(a, term).toLowerCase()}</button>}
        </footer>
        {toast && <div className="absolute bottom-16 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-drawer">{toast}</div>}
      </div>
    </div>
  );
}
