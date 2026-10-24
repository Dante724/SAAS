import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import Pager from "../../components/Pager";
import InquiryDrawer from "./InquiryDrawer";
import InquiryFields from "./InquiryFields";
import { callsLabel, channelLabel, deviceLine, fmt, newIdempotencyKey, sla, SRC_ICON, type Column, type Lead, type LeadType } from "./inquiries";

const COL_ACCENT: Record<string, string> = { new: "bg-blue-500", in_progress: "bg-amber-500", won: "bg-emerald-500", lost: "bg-slate-400" };
const STATUS_LABEL: Record<string, string> = { new: "New", in_progress: "In progress", won: "Won", lost: "Lost" };
const blank = (): Lead & { sla_minutes?: number } => ({ id: "", name: "", phone: "", email: "", source: "phone", inquiry: "", sla_minutes: 60, custom_data: {} });

export function TypesManager({ types, onClose, onChanged }: { types: LeadType[]; onClose: () => void; onChanged: () => void }) {
  const [name, setName] = useState("");
  const [mins, setMins] = useState("60");
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<any>) => { try { setErr(null); await fn(); onChanged(); } catch (e: any) { setErr(e.message); } };
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-label="Inquiry types" className="relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-md p-5 space-y-4 text-xs">
        <div className="flex items-center justify-between"><h3 className="text-sm font-bold text-ink-primary">Inquiry types</h3>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
        <p className="text-ink-tertiary">Each type has its own respond-by time, for example Repair in 30 minutes or Sale in 2 hours.</p>
        <ul className="divide-y divide-slate-100 border border-slate-200 rounded-xl">
          {types.length === 0 && <li className="p-3 text-ink-tertiary">No types yet.</li>}
          {types.map((t) => (
            <li key={t.id} className={`p-3 flex items-center gap-3 ${t.active ? "" : "opacity-60"}`}>
              <span className="flex-1 font-semibold text-ink-primary">{t.name}{!t.active && " (archived)"}</span>
              <span className="text-ink-tertiary tabular-nums">{t.sla_minutes} min</span>
              {t.active
                ? <button onClick={() => act(() => api.del(`/leads/types/${t.id}`))} className="text-rose-600 font-semibold hover:underline">Remove</button>
                : <button onClick={() => act(() => api.patch(`/leads/types/${t.id}`, { active: true }))} className="text-primary font-semibold hover:underline">Restore</button>}
            </li>
          ))}
        </ul>
        <div className="flex items-end gap-2">
          <div className="flex-1"><label htmlFor="type-name" className="block font-semibold text-ink-secondary mb-1">New type</label><input id="type-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Repair" className="glass-input w-full px-3 py-2 rounded-lg" /></div>
          <div className="w-28"><label htmlFor="type-mins" className="block font-semibold text-ink-secondary mb-1">Respond within (min)</label><input id="type-mins" type="number" min={1} value={mins} onChange={(e) => setMins(e.target.value)} className="glass-input w-full px-3 py-2 rounded-lg font-mono" /></div>
          <button disabled={!name.trim()} onClick={() => act(async () => { await api.post("/leads/types", { name: name.trim(), sla_minutes: Number(mins) || 60 }); setName(""); })}
            className="px-4 py-2 rounded-lg bg-primary text-white font-bold disabled:opacity-40">Add</button>
        </div>
        {err && <p role="alert" className="text-rose-600 font-semibold">{err}</p>}
      </div>
    </div>
  );
}

export default function InquiriesHub() {
  const navigate = useNavigate();
  const [cols, setCols] = useState<Column[] | null>(null);
  const [types, setTypes] = useState<LeadType[]>([]);
  const [view, setView] = useState<"board" | "list">("board");
  const [sel, setSel] = useState<Lead | null>(null);
  const [create, setCreate] = useState(false);
  const [showTypes, setShowTypes] = useState(false);
  const [cf, setCf] = useState(blank);
  const [idem, setIdem] = useState(newIdempotencyKey);
  const [saving, setSaving] = useState(false);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  function load() { api.get<{ columns: Column[] }>("/leads/board").then((r) => setCols(r.columns || [])).catch(() => setCols([])); }
  const loadTypes = () => api.get<{ items: LeadType[] }>("/leads/types").then((r) => setTypes(r.items || [])).catch(() => setTypes([]));
  useEffect(() => { load(); loadTypes(); }, []);
  // A missed-call alert links here with ?lead=<id>
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("lead");
    if (id) api.get<Lead>(`/leads/${id}`).then(setSel).catch(() => flash("That inquiry isn't available"));
  }, []);

  async function createLead() {
    if (!(cf.name || "").trim()) { setCreateErr("Enter the customer's name"); return; }
    setSaving(true); setCreateErr(null);
    try {
      await api.post("/leads", { name: cf.name!.trim(), phone: cf.phone || null, email: cf.email || null, source: cf.source,
        inquiry: cf.inquiry || null, sla_minutes: Number(cf.sla_minutes) || 60, lead_type_id: cf.lead_type_id || null,
        device_brand: cf.device_brand || null, device_model: cf.device_model || null, serial: cf.serial || null,
        custom_data: cf.custom_data || {}, idempotency_key: idem });
      setCreate(false); setCf(blank()); setIdem(newIdempotencyKey()); load(); flash("Inquiry logged");
    } catch (e: any) { setCreateErr(e.message); }    // keep the key: a retry can't create a second one
    finally { setSaving(false); }
  }
  const openOrder = (id: string) => navigate(`/orders?open=${encodeURIComponent(id)}`);
  const LEAD_PAGE = 50;
  const [leadPage, setLeadPage] = useState(1);
  useEffect(() => { setLeadPage(1); }, [view]);
  const allLeads = (cols || []).flatMap((c) => c.cards);
  const leadPages = Math.max(1, Math.ceil(allLeads.length / LEAD_PAGE));
  const pageLeads = allLeads.slice((Math.min(leadPage, leadPages) - 1) * LEAD_PAGE, Math.min(leadPage, leadPages) * LEAD_PAGE);

  const Chips = ({ l }: { l: Lead }) => {
    const s = sla(l); const calls = callsLabel(l);
    return <>
      {calls && <span className="text-[11px] px-1.5 py-0.5 rounded-full font-bold border border-rose-200 bg-rose-50 text-rose-700 flex items-center gap-0.5"><M n="phone_missed" c="text-[12px]" />{(l.call_count || 0) > 1 ? l.call_count : ""}</span>}
      {s && <span className={`text-[11px] px-1.5 py-0.5 rounded-full font-bold border ${s.cls}`}>{s.label}</span>}
    </>;
  };

  return (
    <Shell title="Inquiries">
      <div className="flex-1 overflow-hidden flex flex-col">
        <div className="px-6 pt-4 pb-2 flex flex-wrap items-center gap-2 flex-shrink-0">
          <button onClick={() => { setCreate(true); setCreateErr(null); }} className="h-[38px] px-4 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-sm" />New inquiry</button>
          <div className="flex items-center bg-slate-100/90 p-0.5 rounded-lg border border-slate-200/60 text-xs ml-1">
            <button onClick={() => setView("board")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1.5 ${view === "board" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="view_kanban" c="text-xs" />Pipeline</button>
            <button onClick={() => setView("list")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1.5 ${view === "list" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="table_rows" c="text-xs" />List</button>
          </div>
          <button onClick={() => setShowTypes(true)} className="h-[30px] px-3 rounded-lg text-xs font-semibold text-ink-secondary hover:bg-slate-100 flex items-center gap-1"><M n="label" c="text-sm" />Types</button>
          <p className="text-[11px] text-ink-tertiary ml-1">Missed calls show up here on their own. Convert one to open a job.</p>
        </div>

        {view === "list" && (
          <div className="flex-1 overflow-y-auto px-6 pb-6">
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <table className="w-full text-left border-collapse text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold ">
                  <th className="py-3 px-4">Inquiry</th><th className="py-3 px-4">Channel</th><th className="py-3 px-4">Device</th><th className="py-3 px-4">Message</th><th className="py-3 px-4">Status</th><th className="py-3 px-4">Respond by</th><th className="py-3 px-4">Result</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {allLeads.length === 0 && <tr><td colSpan={7} className="py-8 px-4 text-ink-tertiary text-center">No inquiries.</td></tr>}
                  {pageLeads.map((l) => (
                    <tr key={l.id} onClick={() => setSel(l)} className="hover:bg-blue-50/30 cursor-pointer">
                      <td className="py-3 px-4 font-bold text-ink-primary">{l.name || "Inquiry"}{l.lead_type && <span className="block text-[11px] font-semibold text-ink-tertiary">{l.lead_type}</span>}</td>
                      <td className="py-3 px-4"><span className="inline-flex items-center gap-1.5"><M n={SRC_ICON[l.source || "other"] || "help"} c="text-xs text-primary" />{channelLabel(l.source)}</span></td>
                      <td className="py-3 px-4">{deviceLine(l) || "—"}</td>
                      <td className="py-3 px-4 text-ink-tertiary truncate max-w-[240px]">{l.inquiry || "—"}</td>
                      <td className="py-3 px-4"><span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${l.status === "won" ? "bg-emerald-100 text-emerald-800" : l.status === "lost" ? "bg-slate-100 text-slate-600" : l.status === "in_progress" ? "bg-amber-100 text-amber-800" : "bg-blue-100 text-blue-800"}`}>{STATUS_LABEL[l.status || "new"] || "New"}</span></td>
                      <td className="py-3 px-4"><div className="flex items-center gap-1"><Chips l={l} />{!sla(l) && !callsLabel(l) && "—"}</div></td>
                      <td className="py-3 px-4">{l.converted_order_number ? <span className="text-primary font-bold">{l.converted_order_number}</span> : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {cols && allLeads.length > 0 && <Pager total={allLeads.length} page={Math.min(leadPage, leadPages)} pageSize={LEAD_PAGE} onPage={setLeadPage} noun="inquiries" />}
            </div>
          </div>
        )}

        {view === "board" && <div className="flex-1 overflow-x-auto overflow-y-hidden px-6 pb-6">
          <div className="flex gap-4 h-full min-w-max">
            {!cols && <div className="text-xs text-ink-tertiary p-4">Loading…</div>}
            {cols?.map((c) => (
              <div key={c.key} className="w-[80vw] sm:w-72 flex-shrink-0 flex flex-col">
                <div className="flex items-center gap-2 px-1 pb-2">
                  <span className={`w-2 h-2 rounded-full ${COL_ACCENT[c.key] || "bg-slate-400"}`} />
                  <span className="text-xs font-bold text-ink-primary ">{c.label}</span>
                  <span className="text-[11px] font-bold bg-slate-100 text-ink-secondary px-1.5 py-0.5 rounded-full">{c.cards.length}</span>
                </div>
                <div className="flex-1 overflow-y-auto space-y-2.5 pr-1">
                  {c.cards.length === 0 && <div className="text-[11px] text-ink-tertiary px-1 py-3">Nothing here.</div>}
                  {c.cards.map((l) => (
                    <button key={l.id} onClick={() => setSel(l)} className="w-full text-left bg-white rounded-xl border border-slate-200/90 shadow-clay-sm p-3 hover:border-primary/50 hover:-translate-y-0.5 transition-all">
                      <div className="flex items-center gap-2 mb-1.5">
                        <div className="w-7 h-7 rounded-lg bg-blue-50 text-primary flex items-center justify-center flex-shrink-0"><M n={(l.call_count || 0) > 0 ? "phone_missed" : SRC_ICON[l.source || "other"] || "help"} c="text-sm" /></div>
                        <span className="text-xs font-bold text-ink-primary truncate flex-1">{l.name || "Inquiry"}</span>
                        <Chips l={l} />
                      </div>
                      {(l.lead_type || deviceLine(l)) && <p className="text-[11px] font-semibold text-ink-secondary truncate">{[l.lead_type, deviceLine(l)].filter(Boolean).join(" · ")}</p>}
                      {l.inquiry && <p className="text-[11px] text-ink-secondary line-clamp-2">{l.inquiry}</p>}
                      <div className="flex items-center justify-between mt-2 text-[11px] text-ink-tertiary">
                        <span>{channelLabel(l.source)}</span>
                        {l.converted_order_number ? <span className="text-primary font-bold">{l.converted_order_number}</span> : <span>{fmt(l.created_at)}</span>}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>}
      </div>

      {sel && <InquiryDrawer lead={sel} types={types} onClose={() => setSel(null)} onOpenOrder={openOrder}
        onChanged={(l, msg) => { flash(msg); if (l) setSel(l); else setSel(null); load(); }} />}

      {create && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 55 }}>
          <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={() => setCreate(false)} />
          <div role="dialog" aria-label="New inquiry" className="relative h-full w-full max-w-[460px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90">
            <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70"><h3 className="text-base font-bold text-ink-primary">New inquiry</h3><button aria-label="Close" onClick={() => setCreate(false)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
            <div className="flex-1 overflow-y-auto p-6"><InquiryFields value={cf} onChange={setCf} types={types} creating showSla /></div>
            {createErr && <p role="alert" className="mx-6 mb-2 p-2.5 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-[11px] font-semibold">{createErr}</p>}
            <div className="p-4 border-t border-slate-100 bg-slate-50/70 flex items-center justify-end gap-2"><button onClick={() => setCreate(false)} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs">Cancel</button><button onClick={createLead} disabled={saving} className="px-5 py-2 rounded-lg bg-primary text-white font-bold text-xs shadow-clay-sm disabled:opacity-50">Log inquiry</button></div>
          </div>
        </div>
      )}
      {showTypes && <TypesManager types={types} onClose={() => setShowTypes(false)} onChanged={loadTypes} />}
      {toast && <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}
