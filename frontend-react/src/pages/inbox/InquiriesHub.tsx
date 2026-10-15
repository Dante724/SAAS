import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import Pager from "../../components/Pager";

type Lead = { id: string; name?: string; phone?: string; email?: string; source?: string; inquiry?: string; status?: string; converted_order_id?: string | null; converted_order_number?: string | null; sla_due_at?: string | null; sla_overdue?: boolean; created_at?: string };
type Column = { key: string; label: string; cards: Lead[] };

const SRC_ICON: Record<string, string> = { phone: "call", whatsapp: "chat", sms: "sms", email: "mail", instagram: "photo_camera", facebook: "thumb_up", web: "public", walk_in: "directions_walk" };
const COL_ACCENT: Record<string, string> = { new: "bg-blue-500", in_progress: "bg-amber-500", won: "bg-emerald-500", lost: "bg-slate-400" };
const fmt = (iso?: string | null) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");
function sla(l: Lead) {
  if (!l.sla_due_at || l.status === "won" || l.status === "lost") return null;
  const ms = new Date(l.sla_due_at).getTime() - Date.now();
  if (l.sla_overdue || ms < 0) return { cls: "bg-rose-100 text-rose-700 border-rose-200", label: "SLA overdue" };
  const mins = Math.round(ms / 60000);
  return { cls: "bg-amber-50 text-amber-700 border-amber-200/60", label: mins > 90 ? `${Math.round(mins / 60)}h left` : `${mins}m left` };
}

export default function InquiriesHub() {
  const [cols, setCols] = useState<Column[] | null>(null);
  const [view, setView] = useState<"board" | "list">("board");
  const [sel, setSel] = useState<Lead | null>(null);
  const [create, setCreate] = useState(false);
  const [cf, setCf] = useState<any>({ name: "", phone: "", email: "", source: "phone", inquiry: "", sla_minutes: 60 });
  const [convMenu, setConvMenu] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  function load() { api.get<{ columns: Column[] }>("/leads/board").then((r) => setCols(r.columns || [])).catch(() => setCols([])); }
  useEffect(load, []);

  async function setStatus(l: Lead, status: string) {
    try { await api.patch(`/leads/${l.id}`, { status }); flash(`Moved to ${status.replace(/_/g, " ")}`); setSel({ ...l, status }); load(); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  async function convert(l: Lead, isEstimate: boolean) {
    setBusy(true); setConvMenu(false);
    try {
      const r = await api.post<any>(`/leads/${l.id}/convert?is_estimate=${isEstimate}`, {});
      flash(`${isEstimate ? "Estimate" : "Work order"} ${r.order_number} created`);
      setSel(null); load();
    } catch (e: any) { flash(`Failed: ${e.message}`); } finally { setBusy(false); }
  }
  async function createLead() {
    if (!cf.name.trim()) { flash("Name required"); return; }
    try { await api.post("/leads", { name: cf.name.trim(), phone: cf.phone || null, email: cf.email || null, source: cf.source, inquiry: cf.inquiry || null, sla_minutes: Number(cf.sla_minutes) || 60 }); setCreate(false); setCf({ name: "", phone: "", email: "", source: "phone", inquiry: "", sla_minutes: 60 }); load(); flash("Inquiry logged"); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  const openOrder = (id: string) => { window.location.hash = "#/orders"; flash("Opening work order…"); void id; };
  // List view pagination — 50/page (board view is unpaginated kanban columns).
  const LEAD_PAGE = 50;
  const [leadPage, setLeadPage] = useState(1);
  useEffect(() => { setLeadPage(1); }, [view]);
  const allLeads = (cols || []).flatMap((c) => c.cards);
  const leadPages = Math.max(1, Math.ceil(allLeads.length / LEAD_PAGE));
  const pageLeads = allLeads.slice((Math.min(leadPage, leadPages) - 1) * LEAD_PAGE, Math.min(leadPage, leadPages) * LEAD_PAGE);

  return (
    <Shell title="Inquiries">
      <div className="flex-1 overflow-hidden flex flex-col">
        <div className="px-6 pt-4 pb-2 flex items-center gap-2 flex-shrink-0">
          <button onClick={() => setCreate(true)} className="h-[38px] px-4 rounded-xl bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-sm" />New inquiry</button>
          <div className="flex items-center bg-slate-100/90 p-0.5 rounded-lg border border-slate-200/60 text-xs ml-1">
            <button onClick={() => setView("board")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1.5 ${view === "board" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="view_kanban" c="text-xs" />Pipeline</button>
            <button onClick={() => setView("list")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1.5 ${view === "list" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="table_rows" c="text-xs" />List</button>
          </div>
          <p className="text-[11px] text-ink-tertiary ml-1">Win one to turn it into a work order or estimate.</p>
        </div>

        {/* list view */}
        {view === "list" && (
          <div className="flex-1 overflow-y-auto px-6 pb-6">
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <table className="w-full text-left border-collapse text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[10px] font-bold uppercase tracking-wider">
                  <th className="py-3 px-4">Inquiry</th><th className="py-3 px-4">Channel</th><th className="py-3 px-4">Message</th><th className="py-3 px-4">Status</th><th className="py-3 px-4">SLA</th><th className="py-3 px-4">Result</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {allLeads.length === 0 && <tr><td colSpan={6} className="py-8 px-4 text-ink-tertiary text-center">No inquiries.</td></tr>}
                  {pageLeads.map((l) => {
                    const s = sla(l);
                    return (
                      <tr key={l.id} onClick={() => setSel(l)} className="hover:bg-blue-50/30 cursor-pointer">
                        <td className="py-3 px-4 font-bold text-ink-primary">{l.name || "Inquiry"}</td>
                        <td className="py-3 px-4"><span className="inline-flex items-center gap-1.5 capitalize"><M n={SRC_ICON[l.source || "web"] || "public"} c="text-xs text-primary" />{(l.source || "web").replace(/_/g, " ")}</span></td>
                        <td className="py-3 px-4 text-ink-tertiary truncate max-w-[260px]">{l.inquiry || "—"}</td>
                        <td className="py-3 px-4"><span className={`px-2 py-0.5 rounded-full text-[10px] font-bold capitalize ${l.status === "won" ? "bg-emerald-100 text-emerald-800" : l.status === "lost" ? "bg-slate-100 text-slate-600" : l.status === "in_progress" ? "bg-amber-100 text-amber-800" : "bg-blue-100 text-blue-800"}`}>{(l.status || "new").replace(/_/g, " ")}</span></td>
                        <td className="py-3 px-4">{s ? <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-bold border ${s.cls}`}>{s.label}</span> : "—"}</td>
                        <td className="py-3 px-4">{l.converted_order_number ? <span className="text-primary font-bold">{l.converted_order_number}</span> : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {cols && allLeads.length > 0 && <Pager total={allLeads.length} page={Math.min(leadPage, leadPages)} pageSize={LEAD_PAGE} onPage={setLeadPage} noun="inquiries" />}
            </div>
          </div>
        )}

        {/* board */}
        {view === "board" && <div className="flex-1 overflow-x-auto overflow-y-hidden px-6 pb-6">
          <div className="flex gap-4 h-full min-w-max">
            {!cols && <div className="text-xs text-ink-tertiary p-4">Loading…</div>}
            {cols?.map((c) => (
              <div key={c.key} className="w-72 flex-shrink-0 flex flex-col">
                <div className="flex items-center gap-2 px-1 pb-2">
                  <span className={`w-2 h-2 rounded-full ${COL_ACCENT[c.key] || "bg-slate-400"}`} />
                  <span className="text-xs font-bold text-ink-primary uppercase tracking-wide">{c.label}</span>
                  <span className="text-[10px] font-bold bg-slate-100 text-ink-secondary px-1.5 py-0.5 rounded-full">{c.cards.length}</span>
                </div>
                <div className="flex-1 overflow-y-auto space-y-2.5 pr-1">
                  {c.cards.length === 0 && <div className="text-[11px] text-ink-tertiary px-1 py-3">Nothing here.</div>}
                  {c.cards.map((l) => {
                    const s = sla(l);
                    return (
                      <button key={l.id} onClick={() => setSel(l)} className="w-full text-left bg-white rounded-xl border border-slate-200/90 shadow-clay-sm p-3 hover:border-primary/50 hover:-translate-y-0.5 transition-all">
                        <div className="flex items-center gap-2 mb-1.5">
                          <div className="w-7 h-7 rounded-lg bg-blue-50 text-primary flex items-center justify-center flex-shrink-0"><M n={SRC_ICON[l.source || "web"] || "public"} c="text-sm" /></div>
                          <span className="text-xs font-bold text-ink-primary truncate flex-1">{l.name || "Inquiry"}</span>
                          {s && <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-bold border ${s.cls}`}>{s.label}</span>}
                        </div>
                        {l.inquiry && <p className="text-[11px] text-ink-secondary line-clamp-2">{l.inquiry}</p>}
                        <div className="flex items-center justify-between mt-2 text-[10px] text-ink-tertiary">
                          <span className="capitalize">{(l.source || "web").replace(/_/g, " ")}</span>
                          {l.converted_order_number ? <span className="text-primary font-bold">{l.converted_order_number} →</span> : <span>{fmt(l.created_at)}</span>}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>}
      </div>

      {/* detail drawer */}
      {sel && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
          <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={() => setSel(null)} />
          <div className="relative h-full w-full max-w-[480px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
            <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
            <header className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
              <div className="flex items-center gap-2.5">
                <div className="w-9 h-9 rounded-lg bg-blue-50 text-primary flex items-center justify-center"><M n={SRC_ICON[sel.source || "web"] || "public"} c="text-base" /></div>
                <div><h3 className="text-base font-bold text-ink-primary">{sel.name || "Inquiry"}</h3><p className="text-[11px] text-ink-tertiary capitalize">{(sel.source || "web").replace(/_/g, " ")} inquiry</p></div>
              </div>
              <button aria-label="Close" onClick={() => setSel(null)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
            </header>
            <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
              {(() => { const s = sla(sel); return s ? <div className={`px-3 py-2 rounded-xl border text-[11px] font-bold flex items-center gap-1.5 ${s.cls}`}><M n="timer" c="text-sm" />Respond-by {s.label} · due {fmt(sel.sla_due_at)}</div> : null; })()}
              <div className="grid grid-cols-2 gap-3">
                <div><label className="block font-semibold text-ink-tertiary uppercase mb-1">Phone</label><div className="glass-input px-3 py-2 rounded-lg font-mono">{sel.phone || "—"}</div></div>
                <div><label className="block font-semibold text-ink-tertiary uppercase mb-1">Email</label><div className="glass-input px-3 py-2 rounded-lg truncate">{sel.email || "—"}</div></div>
              </div>
              <div><label className="block font-semibold text-ink-tertiary uppercase mb-1">Inquiry / message</label><div className="p-3 bg-slate-50/80 rounded-xl border border-slate-200 text-xs leading-relaxed text-ink-primary min-h-[80px]">{sel.inquiry || "No details captured."}</div></div>
              <div className="flex items-center justify-between text-[11px] text-ink-tertiary"><span>Logged {fmt(sel.created_at)}</span><span className="capitalize font-semibold text-ink-secondary">{(sel.status || "new").replace(/_/g, " ")}</span></div>
              {sel.converted_order_id && (
                <div className="p-3 rounded-xl bg-emerald-50 border border-emerald-200 flex items-center justify-between"><span className="text-xs font-bold text-emerald-800 flex items-center gap-1.5"><M n="check_circle" c="text-sm" />Converted to {sel.converted_order_number}</span><button onClick={() => openOrder(sel.converted_order_id!)} className="text-xs font-bold text-primary hover:underline">Open →</button></div>
              )}
            </div>
            {/* status-driven footer */}
            {!sel.converted_order_id && (
              <footer className="p-4 border-t border-slate-100 bg-slate-50/70 flex items-center gap-2">
                {sel.status === "lost" ? (
                  <button onClick={() => setStatus(sel, "new")} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs flex items-center gap-1.5"><M n="undo" c="text-sm" />Reopen</button>
                ) : (
                  <>
                    {sel.status === "new" && <button onClick={() => setStatus(sel, "in_progress")} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs flex items-center gap-1.5"><M n="play_arrow" c="text-sm" />Start</button>}
                    <button onClick={() => setStatus(sel, "lost")} className="px-4 py-2 rounded-lg text-rose-600 hover:bg-rose-50 font-semibold text-xs">Mark lost</button>
                    {/* split convert button */}
                    <div className="ml-auto relative flex">
                      <button onClick={() => convert(sel, false)} disabled={busy} className="px-4 py-2 rounded-l-lg bg-gradient-to-r from-primary to-primary-container text-white font-bold text-xs shadow-clay-sm hover:brightness-110 disabled:opacity-50 flex items-center gap-1.5"><M n="check" c="text-sm" />Win → Work order</button>
                      <button aria-label="Expand" onClick={() => setConvMenu((v) => !v)} disabled={busy} className="px-2 py-2 rounded-r-lg bg-primary-container text-white border-l border-white/25 hover:brightness-110 disabled:opacity-50"><M n="expand_more" c="text-sm" /></button>
                      {convMenu && (
                        <div className="absolute bottom-full right-0 mb-1.5 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 w-44">
                          <button onClick={() => convert(sel, false)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs font-semibold text-ink-primary"><M n="handyman" c="text-sm text-primary" />Convert to work order</button>
                          <button onClick={() => convert(sel, true)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs font-semibold text-ink-primary"><M n="request_quote" c="text-sm text-primary" />Convert to estimate</button>
                        </div>
                      )}
                    </div>
                  </>
                )}
              </footer>
            )}
          </div>
        </div>
      )}

      {/* new inquiry drawer */}
      {create && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 55 }}>
          <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={() => setCreate(false)} />
          <div className="relative h-full w-full max-w-[440px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
            <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70"><h3 className="text-base font-bold text-ink-primary">New inquiry</h3><button aria-label="Close" onClick={() => setCreate(false)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
            <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
              <div><label className="block font-semibold text-ink-secondary mb-1">Name <span className="text-rose-500">*</span></label><input autoFocus value={cf.name} onChange={(e) => setCf({ ...cf, name: e.target.value })} className="glass-input w-full px-3 py-2.5 rounded-lg text-sm font-semibold" /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className="block font-semibold text-ink-secondary mb-1">Phone</label><input value={cf.phone} onChange={(e) => setCf({ ...cf, phone: e.target.value })} className="glass-input w-full px-3 py-2 rounded-lg font-mono" /></div>
                <div><label className="block font-semibold text-ink-secondary mb-1">Email</label><input value={cf.email} onChange={(e) => setCf({ ...cf, email: e.target.value })} className="glass-input w-full px-3 py-2 rounded-lg" /></div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className="block font-semibold text-ink-secondary mb-1">Channel</label><select value={cf.source} onChange={(e) => setCf({ ...cf, source: e.target.value })} className="glass-input w-full px-3 py-2 rounded-lg font-semibold appearance-none"><option value="phone">Phone</option><option value="whatsapp">WhatsApp</option><option value="sms">SMS</option><option value="email">Email</option><option value="instagram">Instagram</option><option value="facebook">Facebook</option><option value="web">Web form</option><option value="walk_in">Walk-in</option></select></div>
                <div><label className="block font-semibold text-ink-secondary mb-1">Respond-by (min)</label><input type="number" value={cf.sla_minutes} onChange={(e) => setCf({ ...cf, sla_minutes: e.target.value })} className="glass-input w-full px-3 py-2 rounded-lg font-mono" /></div>
              </div>
              <div><label className="block font-semibold text-ink-secondary mb-1">Inquiry / message</label><textarea value={cf.inquiry} onChange={(e) => setCf({ ...cf, inquiry: e.target.value })} className="glass-input w-full px-3 py-2.5 rounded-lg h-24 resize-none" placeholder="What is the customer asking about?" /></div>
            </div>
            <div className="p-4 border-t border-slate-100 bg-slate-50/70 flex items-center justify-end gap-2"><button onClick={() => setCreate(false)} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs">Cancel</button><button onClick={createLead} className="px-5 py-2 rounded-lg bg-gradient-to-r from-primary to-primary-container text-white font-bold text-xs shadow-clay-sm">Log inquiry</button></div>
          </div>
        </div>
      )}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}
