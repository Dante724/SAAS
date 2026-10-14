import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import Pager from "../../components/Pager";
import { confirmDialog, alertDialog } from "../../lib/dialog";

// Marketing: campaigns (SMS/email blasts by segment) + call log — legacy /comms surface.
const d = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");

export default function MarketingHub() {
  const [tab, setTab] = useState<"campaigns" | "calls">("campaigns");
  const [camps, setCamps] = useState<any[] | null>(null);
  const [calls, setCalls] = useState<any[] | null>(null);
  const [draft, setDraft] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(""), 2200); };
  function loadCamps() { api.get<any>("/comms/campaigns").then((r) => setCamps(r.items || [])).catch(() => setCamps([])); }
  function loadCalls() { api.get<any>("/comms/calls").then((r) => setCalls(r.items || [])).catch(() => setCalls([])); }
  useEffect(() => { loadCamps(); }, []);
  useEffect(() => { if (tab === "calls" && !calls) loadCalls(); }, [tab]);

  async function saveCampaign() {
    if (!draft?.name?.trim()) return; setBusy(true);
    try { await api.post("/comms/campaigns", { name: draft.name, channel: draft.channel || "sms", subject: draft.subject || null, body: draft.body || "", segment: draft.segment || "all" }); setDraft(null); loadCamps(); flash("Campaign saved"); }
    catch (e: any) { await alertDialog(e.message || "Save failed"); } finally { setBusy(false); }
  }
  async function send(c: any) {
    if (!(await confirmDialog(`Send "${c.name}" to the ${c.segment} segment via ${c.channel}?`))) return;
    setBusy(true);
    try { const r = await api.post<any>(`/comms/campaigns/${c.id}/send`, {}); loadCamps(); flash(`Sent to ${r.recipients ?? 0} recipient(s)`); }
    catch (e: any) { await alertDialog(e.message || "Send failed"); } finally { setBusy(false); }
  }
  const pill = (s: string) => s === "sent" ? "bg-emerald-100 text-emerald-700 border-emerald-200" : "bg-amber-100 text-amber-700 border-amber-200";
  const inp = "glass-input w-full px-3 py-2 rounded-lg text-sm font-semibold text-ink-primary";
  // Pagination — one shared page (campaigns/calls tabs never show at once).
  const MK_PAGE = 50;
  const [mkPage, setMkPage] = useState(1);
  useEffect(() => { setMkPage(1); }, [tab]);
  const campPages = Math.max(1, Math.ceil((camps?.length ?? 0) / MK_PAGE));
  const pageCamps = (camps || []).slice((Math.min(mkPage, campPages) - 1) * MK_PAGE, Math.min(mkPage, campPages) * MK_PAGE);
  const callPages = Math.max(1, Math.ceil((calls?.length ?? 0) / MK_PAGE));
  const pageCalls = (calls || []).slice((Math.min(mkPage, callPages) - 1) * MK_PAGE, Math.min(mkPage, callPages) * MK_PAGE);

  return (
    <Shell title="Marketing">
      <div className="px-6 pt-3 border-b border-slate-200/60 bg-white/40 flex-shrink-0">
        <nav className="flex space-x-6 -mb-px text-xs font-semibold">
          {([["campaigns", "Campaigns"], ["calls", "Call log"]] as [typeof tab, string][]).map(([id, l]) => (
            <button key={id} onClick={() => setTab(id)} className={`pb-3 border-b-2 transition-colors ${tab === id ? "text-primary font-bold border-primary" : "text-ink-secondary hover:text-ink-primary border-transparent"}`}>{l}</button>
          ))}
        </nav>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {tab === "campaigns" && (
          <div className="max-w-3xl space-y-3">
            <div className="flex justify-end"><button onClick={() => setDraft({ channel: "sms", segment: "all" })} className="px-4 py-2 rounded-xl bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-sm" />New campaign</button></div>
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <table className="w-full text-left text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[10px] font-bold uppercase tracking-wider"><th className="py-3 px-4">Name</th><th className="py-3 px-4">Channel</th><th className="py-3 px-4">Segment</th><th className="py-3 px-4">Status</th><th className="py-3 px-4 text-right">Recipients</th><th className="py-3 px-4 text-right">Action</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {!camps && <SkeletonRows cols={6} />}
                  {camps?.length === 0 && <tr><td colSpan={6} className="py-6 px-4 text-ink-tertiary">No campaigns yet.</td></tr>}
                  {pageCamps.map((c) => (
                    <tr key={c.id} className="hover:bg-blue-50/30">
                      <td className="py-2.5 px-4 font-bold text-ink-primary">{c.name}</td>
                      <td className="py-2.5 px-4 capitalize">{c.channel}</td>
                      <td className="py-2.5 px-4 capitalize">{c.segment}</td>
                      <td className="py-2.5 px-4"><span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border capitalize ${pill(c.status)}`}>{humanize(c.status)}</span>{c.sent_at ? <span className="block text-[10px] text-ink-tertiary">{d(c.sent_at)}</span> : null}</td>
                      <td className="py-2.5 px-4 text-right font-mono">{c.recipients ?? "—"}</td>
                      <td className="py-2.5 px-4 text-right">{c.status !== "sent" && <button onClick={() => send(c)} disabled={busy} className="px-2.5 py-1 rounded-lg bg-primary text-white text-[11px] font-bold shadow-clay-sm disabled:opacity-50">Send</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {camps && camps.length > 0 && <Pager total={camps.length} page={Math.min(mkPage, campPages)} pageSize={MK_PAGE} onPage={setMkPage} noun="campaigns" />}
            </div>
          </div>
        )}

        {tab === "calls" && (
          <div className="max-w-3xl bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <table className="w-full text-left text-xs">
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[10px] font-bold uppercase tracking-wider"><th className="py-3 px-4">When</th><th className="py-3 px-4">Direction</th><th className="py-3 px-4">From → To</th><th className="py-3 px-4">Status</th><th className="py-3 px-4 text-right">Duration</th><th className="py-3 px-4">Recording</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {!calls && <SkeletonRows cols={6} />}
                {calls?.length === 0 && <tr><td colSpan={6} className="py-6 px-4 text-ink-tertiary">No calls logged.</td></tr>}
                {pageCalls.map((c) => (
                  <tr key={c.id} className="hover:bg-blue-50/30">
                    <td className="py-2.5 px-4 text-ink-tertiary whitespace-nowrap">{d(c.created_at)}</td>
                    <td className="py-2.5 px-4"><span className={`capitalize font-semibold ${c.direction === "inbound" ? "text-emerald-700" : "text-primary"}`}>{c.direction || "—"}</span></td>
                    <td className="py-2.5 px-4 font-mono text-[11px]">{c.from || "—"} → {c.to || "—"}</td>
                    <td className="py-2.5 px-4 capitalize">{humanize(c.status)}</td>
                    <td className="py-2.5 px-4 text-right font-mono">{c.duration_s != null ? `${Math.floor(c.duration_s / 60)}:${String(c.duration_s % 60).padStart(2, "0")}` : "—"}</td>
                    <td className="py-2.5 px-4">{c.recording_url ? <a href={c.recording_url} target="_blank" rel="noreferrer" className="text-primary font-semibold hover:underline">Play</a> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {calls && calls.length > 0 && <Pager total={calls.length} page={Math.min(mkPage, callPages)} pageSize={MK_PAGE} onPage={setMkPage} noun="calls" />}
          </div>
        )}
      </div>

      {draft && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={() => setDraft(null)} />
          <div className="relative w-full max-w-md bg-white rounded-2xl shadow-clay-drawer border border-slate-200/90">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/70"><h3 className="text-sm font-bold text-ink-primary">New campaign</h3><button aria-label="Close" onClick={() => setDraft(null)} className="w-7 h-7 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
            <div className="p-5 space-y-3">
              <input value={draft.name || ""} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Campaign name" className={inp} />
              <div className="grid grid-cols-2 gap-2">
                <select value={draft.channel} onChange={(e) => setDraft({ ...draft, channel: e.target.value })} className={inp}><option value="sms">SMS</option><option value="email">Email</option></select>
                <select value={draft.segment} onChange={(e) => setDraft({ ...draft, segment: e.target.value })} className={inp}><option value="all">All clients</option><option value="loyalty">Loyalty members</option><option value="lapsed">Lapsed clients</option></select>
              </div>
              {draft.channel === "email" && <input value={draft.subject || ""} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} placeholder="Email subject" className={inp} />}
              <textarea value={draft.body || ""} onChange={(e) => setDraft({ ...draft, body: e.target.value })} placeholder="Message…" className={`${inp} h-24 resize-none`} />
              <div className="flex justify-end gap-2"><button onClick={() => setDraft(null)} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">Cancel</button><button onClick={saveCampaign} disabled={busy || !draft.name?.trim()} className="px-4 py-2 rounded-lg bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm disabled:opacity-50">Save draft</button></div>
            </div>
          </div>
        </div>
      )}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}
