// One inquiry: edit its details, see its activity (automatic entries + comments), and move it
// along — start, mark lost, reopen, or convert it to a job or an estimate.
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { autoPrintAfterCreate } from "../../lib/autoPrint";
import InquiryFields from "./InquiryFields";
import { callsLabel, changes, channelLabel, fmt, sla, SRC_ICON, type FeedItem, type Lead, type LeadType } from "./inquiries";

export function Timeline({ leadId }: { leadId: string }) {
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<{ items: FeedItem[] }>(`/feed/lead/${leadId}`).then((r) => setItems(r.items || [])).catch((e) => { setItems([]); setErr(e.message); });
  useEffect(() => { setItems(null); load(); /* eslint-disable-next-line */ }, [leadId]);
  async function add() {
    if (!text.trim()) return;
    try { await api.post(`/feed/lead/${leadId}`, { item_type: "comment", body: text.trim(), visibility: "private" }); setText(""); setErr(null); load(); }
    catch (e: any) { setErr(e.message); }
  }
  return (
    <section aria-label="Activity" className="space-y-3">
      <h4 className="text-[11px] font-bold text-ink-primary">Activity</h4>
      <div className="flex gap-2">
        <label htmlFor="inq-comment" className="sr-only">Add a note</label>
        <input id="inq-comment" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") add(); }}
          placeholder="Add a note, e.g. called back, no answer" className="glass-input flex-1 px-3 py-2 rounded-lg text-xs" />
        <button onClick={add} disabled={!text.trim()} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-bold disabled:opacity-40">Add</button>
      </div>
      {err && <p role="alert" className="text-[11px] text-rose-600">{err}</p>}
      {!items && <p className="text-[11px] text-ink-tertiary">Loading…</p>}
      {items?.length === 0 && !err && <p className="text-[11px] text-ink-tertiary">Nothing yet.</p>}
      <ol className="space-y-2">
        {items?.map((i) => (
          <li key={i.id} className="flex gap-2.5">
            <span className={`mt-0.5 w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 ${i.item_type === "system" ? "bg-slate-100 text-ink-tertiary" : "bg-blue-50 text-primary"}`}>
              <M n={i.item_type === "system" ? "bolt" : "chat_bubble"} c="text-[13px]" /></span>
            <div className="min-w-0">
              <p className="text-xs text-ink-primary whitespace-pre-line break-words">{i.body}</p>
              <p className="text-[11px] text-ink-tertiary">{i.item_type === "system" ? "Automatic" : i.author_name || "Staff"} · {fmt(i.created_at)}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function InquiryDrawer({ lead, types, onClose, onChanged, onOpenOrder }: {
  lead: Lead; types: LeadType[]; onClose: () => void; onChanged: (l: Lead | null, msg: string) => void; onOpenOrder: (id: string) => void;
}) {
  const [form, setForm] = useState<Lead>(lead);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => { setForm(lead); setErr(null); }, [lead]);
  const diff = changes(lead, form);
  const dirty = Object.keys(diff).length > 0;
  const s = sla(lead);
  const calls = callsLabel(lead);
  const closed = !!lead.converted_order_id;

  async function run(fn: () => Promise<any>, msg: string | ((r: any) => string)) {
    setBusy(true); setErr(null); setMenu(false);
    try { const r = await fn(); onChanged(r?.id ? r : null, typeof msg === "string" ? msg : msg(r)); setTick((t) => t + 1); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }
  const save = () => run(() => api.patch<Lead>(`/leads/${lead.id}`, diff), "Saved");
  const move = (status: string, msg: string) => run(() => api.patch<Lead>(`/leads/${lead.id}`, { status }), msg);
  const convert = (estimate: boolean) => run(async () => {
    if (dirty) await api.patch(`/leads/${lead.id}`, diff);
    const r = await api.post<any>(`/leads/${lead.id}/convert?is_estimate=${estimate}`, {});
    if (!estimate && r?.order_id) void autoPrintAfterCreate(r.order_id);
    return r;
  }, (r) => `${estimate ? "Estimate" : "Job"} ${r.order_number} created`);

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" aria-hidden="true" onClick={onClose} />
      <div role="dialog" aria-label={`Inquiry from ${lead.name || "a customer"}`} className="relative h-full w-full max-w-[520px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90">
        <header className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-9 h-9 rounded-lg bg-blue-50 text-primary flex items-center justify-center flex-shrink-0"><M n={SRC_ICON[lead.source || "other"] || "help"} c="text-base" /></div>
            <div className="min-w-0"><h3 className="text-base font-bold text-ink-primary truncate">{lead.name || "Inquiry"}</h3>
              <p className="text-[11px] text-ink-tertiary">{channelLabel(lead.source)}{lead.lead_type ? ` · ${lead.lead_type}` : ""} · logged {fmt(lead.created_at)}</p></div>
          </div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </header>
        <div className="flex-1 overflow-y-auto p-6 space-y-5 text-xs">
          <div className="flex flex-wrap gap-2">
            {s && <span className={`px-2.5 py-1 rounded-full border text-[11px] font-bold flex items-center gap-1 ${s.cls}`}><M n="timer" c="text-sm" />Respond by {fmt(lead.sla_due_at)} · {s.label}</span>}
            {calls && <span className="px-2.5 py-1 rounded-full border border-rose-200 bg-rose-50 text-rose-700 text-[11px] font-bold flex items-center gap-1"><M n="phone_missed" c="text-sm" />{calls}{lead.last_call_at ? ` · last ${fmt(lead.last_call_at)}` : ""}</span>}
          </div>
          {closed && (
            <div className="p-3 rounded-xl bg-emerald-50 border border-emerald-200 flex items-center justify-between">
              <span className="text-xs font-bold text-emerald-800 flex items-center gap-1.5"><M n="check_circle" c="text-sm" />Converted to {lead.converted_order_number}</span>
              <button onClick={() => onOpenOrder(lead.converted_order_id!)} className="text-xs font-bold text-primary hover:underline">Open job</button>
            </div>
          )}
          <InquiryFields value={form} onChange={setForm} types={types} creating={false} />
          {dirty && !closed && (
            <div className="flex items-center justify-end gap-2">
              <button onClick={() => setForm(lead)} className="px-3 py-1.5 rounded-lg border border-slate-200 text-ink-secondary font-semibold">Undo changes</button>
              <button onClick={save} disabled={busy} className="px-4 py-1.5 rounded-lg bg-primary text-white font-bold disabled:opacity-50">Save changes</button>
            </div>
          )}
          {err && <p role="alert" className="p-2.5 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-[11px] font-semibold">{err}</p>}
          <Timeline key={tick} leadId={lead.id} />
        </div>
        {!closed && (
          <footer className="p-4 border-t border-slate-100 bg-slate-50/70 flex items-center gap-2">
            {lead.status === "lost" ? (
              <button onClick={() => move("new", "Reopened")} disabled={busy} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs flex items-center gap-1.5"><M n="undo" c="text-sm" />Reopen</button>
            ) : (
              <>
                {lead.status === "new" && <button onClick={() => move("in_progress", "Moved to In progress")} disabled={busy} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs flex items-center gap-1.5"><M n="play_arrow" c="text-sm" />Start</button>}
                <button onClick={() => move("lost", "Marked lost")} disabled={busy} className="px-4 py-2 rounded-lg text-rose-600 hover:bg-rose-50 font-semibold text-xs">Mark lost</button>
                <div className="ml-auto relative flex">
                  <button onClick={() => convert(false)} disabled={busy} className="px-4 py-2 rounded-l-lg bg-primary text-white font-bold text-xs shadow-clay-sm hover:brightness-110 disabled:opacity-50 flex items-center gap-1.5"><M n="check" c="text-sm" />Convert to job</button>
                  <button aria-label="More ways to convert" onClick={() => setMenu((v) => !v)} disabled={busy} className="px-2 py-2 rounded-r-lg bg-primary-container text-white border-l border-white/25 hover:brightness-110 disabled:opacity-50"><M n="expand_more" c="text-sm" /></button>
                  {menu && (
                    <div className="absolute bottom-full right-0 mb-1.5 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 w-48">
                      <button onClick={() => convert(false)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs font-semibold text-ink-primary"><M n="handyman" c="text-sm text-primary" />Convert to job</button>
                      <button onClick={() => convert(true)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs font-semibold text-ink-primary"><M n="request_quote" c="text-sm text-primary" />Convert to estimate</button>
                    </div>
                  )}
                </div>
              </>
            )}
          </footer>
        )}
      </div>
    </div>
  );
}
