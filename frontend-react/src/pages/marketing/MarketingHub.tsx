import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import Pager from "../../components/Pager";
import { confirmDialog, alertDialog } from "../../lib/dialog";
import { Link } from "react-router-dom";
import { Avatar } from "../orders/look";
import { PhoneIncoming, PhoneOutgoing } from "lucide-react";
import { safeHttpHref } from "../../lib/safeFileHref";

// Marketing: campaigns (SMS/email blasts by segment) + call log — legacy /comms surface.
const CHANNEL: Record<string, string> = { sms: "SMS", email: "Email" };
const SEGMENT: Record<string, string> = { all: "All clients", loyalty: "Loyalty members", lapsed: "Lapsed clients" };
const MISSED = ["missed", "no-answer", "no_answer", "failed", "busy", "canceled"];
const d = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");

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
  const pill = (s: string) => s === "sent" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700";
  const inp = "w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 text-ink-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/10";
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
      <div className="px-4 md:px-6 pt-3 border-b border-slate-200/60 flex-shrink-0">
        <nav className="flex space-x-6 -mb-px text-xs font-semibold overflow-x-auto whitespace-nowrap">
          {([["campaigns", "Campaigns"], ["calls", "Call log"]] as [typeof tab, string][]).map(([id, l]) => (
            <button key={id} onClick={() => setTab(id)} className={`pb-3 border-b-2 transition-colors ${tab === id ? "text-primary border-primary" : "text-ink-secondary hover:text-ink-primary border-transparent"}`}>{l}</button>
          ))}
        </nav>
      </div>

      <div className="flex-1 overflow-y-auto px-4 md:px-6 py-5">
        {tab === "campaigns" && (
          <div className="space-y-3">
            <div className="flex"><button onClick={() => setDraft({ channel: "sms", segment: "all" })} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-semibold hover:bg-primary-container flex items-center gap-1.5"><M n="add" c="text-sm" />New campaign</button></div>
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-xs font-semibold"><th className="py-3 px-4">Name</th><th className="py-3 px-4">Channel</th><th className="py-3 px-4">Segment</th><th className="py-3 px-4">Status</th><th className="py-3 px-4 text-right">Recipients</th><th className="py-3 px-4 text-right">Action</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {!camps && <SkeletonRows cols={6} />}
                  {camps?.length === 0 && <tr><td colSpan={6} className="py-10 px-4 text-center text-xs text-ink-secondary">No campaigns yet. Write one SMS or email and send it to a group of clients.</td></tr>}
                  {pageCamps.map((c) => (
                    <tr key={c.id} className="hover:bg-slate-50 text-ink-secondary">
                      <td className="py-2.5 px-4 font-semibold text-ink-primary">{c.name}</td>
                      <td className="py-2.5 px-4">{CHANNEL[c.channel] || humanize(c.channel)}</td>
                      <td className="py-2.5 px-4">{SEGMENT[c.segment] || humanize(c.segment)}</td>
                      <td className="py-2.5 px-4"><span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${pill(c.status)}`}>{humanize(c.status)}</span>{c.sent_at ? <span className="block text-[11px] text-ink-tertiary">{d(c.sent_at)}</span> : null}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums">{c.recipients ?? "—"}</td>
                      <td className="py-2.5 px-4 text-right">{c.status !== "sent" && <button onClick={() => send(c)} disabled={busy} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-50">Send</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {camps && camps.length > 0 && <Pager total={camps.length} page={Math.min(mkPage, campPages)} pageSize={MK_PAGE} onPage={setMkPage} noun="campaigns" />}
            </div>
          </div>
        )}

        {tab === "calls" && (
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-xs font-semibold"><th className="py-3 px-4 font-semibold">When</th><th className="py-3 px-4 font-semibold">Direction</th><th className="py-3 px-4 font-semibold">Contact</th><th className="py-3 px-4 font-semibold">Status</th><th className="py-3 px-4 font-semibold text-right">Duration</th><th className="py-3 px-4 font-semibold">Recording</th></tr></thead>
              <tbody className="divide-y divide-slate-100 text-ink-secondary">
                {!calls && <SkeletonRows cols={6} />}
                {calls?.length === 0 && (
                  <tr><td colSpan={6} className="py-10 px-4 text-center">
                    <p className="text-xs text-ink-secondary">No calls yet. Connect a phone line and every call in and out is logged here.</p>
                    <Link to="/settings?section=integrations" className="inline-block mt-2 text-xs font-semibold text-primary hover:underline">Set up Calls &amp; telephony</Link>
                  </td></tr>
                )}
                {pageCalls.map((c) => {
                  const inbound = c.direction === "inbound";
                  // The customer's number is the caller on inbound calls and the callee on outbound ones.
                  const theirs = (inbound ? c.from : c.to) || c.from || c.to;
                  const name = c.client_name || c.client?.name || "";
                  const other = inbound ? c.to : c.from;
                  const sub = [name ? theirs : null, other ? `${inbound ? "to" : "from"} ${other}` : null].filter(Boolean).join(" · ");
                  const missed = inbound && MISSED.includes(String(c.status || "").toLowerCase());
                  return (
                    <tr key={c.id} className="hover:bg-slate-50">
                      <td className="py-2.5 px-4 text-ink-tertiary whitespace-nowrap tabular-nums">{d(c.created_at)}</td>
                      <td className="py-2.5 px-4 text-ink-tertiary whitespace-nowrap"><span className="inline-flex items-center gap-1">{inbound ? <PhoneIncoming size={14} strokeWidth={2} aria-hidden /> : <PhoneOutgoing size={14} strokeWidth={2} aria-hidden />}{inbound ? "Inbound" : c.direction ? "Outbound" : "—"}</span></td>
                      <td className="py-2.5 px-4">
                        <div className="flex items-center gap-2.5">
                          <Avatar name={name} size={28} />
                          <div className="min-w-0">
                            <div className="text-[13px] font-medium text-ink-primary truncate">{name || theirs || "Unknown number"}</div>
                            {sub && <div className="text-xs text-ink-tertiary tabular-nums whitespace-nowrap">{sub}</div>}
                          </div>
                        </div>
                      </td>
                      <td className={`py-2.5 px-4 ${missed ? "text-rose-600 font-medium" : ""}`}>{humanize(c.status)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums">{c.duration_s != null ? `${Math.floor(c.duration_s / 60)}:${String(c.duration_s % 60).padStart(2, "0")}` : "—"}</td>
                      <td className="py-2.5 px-4">{safeHttpHref(c.recording_url) ? <a href={safeHttpHref(c.recording_url)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary font-semibold hover:underline"><M n="play_arrow" c="text-sm" />Play</a> : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
            {calls && calls.length > 0 && <Pager total={calls.length} page={Math.min(mkPage, callPages)} pageSize={MK_PAGE} onPage={setMkPage} noun="calls" />}
          </div>
        )}
      </div>

      {draft && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-ink-primary/40" onClick={() => setDraft(null)} />
          <div className="relative w-full max-w-md bg-white rounded-2xl shadow-menu border border-slate-200/90">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between"><h3 className="text-sm font-semibold text-ink-primary">New campaign</h3><button aria-label="Close" onClick={() => setDraft(null)} className="w-7 h-7 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
            <div className="p-5 space-y-3">
              <input value={draft.name || ""} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Campaign name" className={inp} />
              <div className="grid grid-cols-2 gap-2">
                <select value={draft.channel} onChange={(e) => setDraft({ ...draft, channel: e.target.value })} className={inp}><option value="sms">SMS</option><option value="email">Email</option></select>
                <select value={draft.segment} onChange={(e) => setDraft({ ...draft, segment: e.target.value })} className={inp}><option value="all">All clients</option><option value="loyalty">Loyalty members</option><option value="lapsed">Lapsed clients</option></select>
              </div>
              {draft.channel === "email" && <input value={draft.subject || ""} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} placeholder="Email subject" className={inp} />}
              <textarea value={draft.body || ""} onChange={(e) => setDraft({ ...draft, body: e.target.value })} placeholder="Message…" className={`${inp} h-24 resize-none`} />
              <div className="flex justify-end gap-2"><button onClick={() => setDraft(null)} className="px-4 py-2 rounded-lg border border-slate-200 bg-white text-ink-primary text-xs font-semibold hover:bg-slate-50">Cancel</button><button onClick={saveCampaign} disabled={busy || !draft.name?.trim()} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-50">Save draft</button></div>
            </div>
          </div>
        </div>
      )}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-menu z-[70]">{toast}</div>}
    </Shell>
  );
}
