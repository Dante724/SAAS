// One inquiry: edit its details, see its activity (automatic entries + comments), and move it
// along — start, mark lost, reopen, or convert it to a job or an estimate.
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { autoPrintForJob } from "../../lib/autoPrint";
import InquiryFields from "./InquiryFields";
import Drawer from "../../components/Drawer";
import Button from "../../components/Button";
import { Badge } from "../../components/Card";
import { Input } from "../../components/Field";
import EmptyState from "../../components/EmptyState";
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
      <h3 className="text-xs font-semibold text-ink-secondary">Activity</h3>
      <div className="flex gap-2">
        <label htmlFor="inq-comment" className="sr-only">Add a note</label>
        <Input id="inq-comment" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") add(); }}
          placeholder="Add a note, e.g. called back, no answer" />
        <Button variant="primary" onClick={add} disabled={!text.trim()}>Add</Button>
      </div>
      {err && <p role="alert" className="text-2xs text-rose-600">{err}</p>}
      {!items && <div aria-hidden className="space-y-2"><span className="sk block h-3 w-2/3 rounded" /><span className="sk block h-3 w-1/2 rounded" /></div>}
      {items?.length === 0 && !err && <EmptyState size="inline" title="Nothing yet." />}
      <ol className="space-y-2">
        {items?.map((i) => (
          <li key={i.id} className="flex gap-2.5">
            <span className={`mt-0.5 w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 ${i.item_type === "system" ? "bg-slate-100 text-ink-tertiary" : "bg-blue-50 text-primary"}`}>
              <M n={i.item_type === "system" ? "bolt" : "chat_bubble"} c="text-sm" /></span>
            <div className="min-w-0">
              <p className="text-xs text-ink-primary whitespace-pre-line break-words">{i.body}</p>
              <p className="text-2xs text-ink-tertiary">{i.item_type === "system" ? "Automatic" : i.author_name || "Staff"} · {fmt(i.created_at)}</p>
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
    if (!estimate && r?.order_id) void autoPrintForJob(r.order_id);
    return r;
  }, (r) => `${estimate ? "Estimate" : "Work order"} ${r.order_number} created`);

  return (
    <Drawer label={`Inquiry from ${lead.name || "a customer"}`} onClose={onClose}
      title={<span className="flex items-center gap-2 min-w-0"><M n={SRC_ICON[lead.source || "other"] || "help"} c="text-base text-primary flex-shrink-0" /><span className="truncate">{lead.name || "Inquiry"}</span></span>}
      sub={<>{channelLabel(lead.source)}{lead.lead_type ? ` · ${lead.lead_type}` : ""} · logged {fmt(lead.created_at)}</>}
      footer={closed ? undefined : lead.status === "lost" ? (
        <Button onClick={() => move("new", "Reopened")} disabled={busy} icon={<M n="undo" c="text-sm" />}>Reopen</Button>
      ) : (
        <>
          <div className="relative flex">
            <Button variant="primary" className="rounded-r-none" onClick={() => convert(false)} disabled={busy} icon={<M n="check" c="text-sm" />}>Create work order</Button>
            <Button variant="primary" className="rounded-l-none border-l border-white/25 !px-2" aria-label="More ways to create" aria-expanded={menu} onClick={() => setMenu((v) => !v)} disabled={busy}><M n="expand_more" c="text-sm" /></Button>
            {menu && (
              <div className="absolute bottom-full left-0 mb-1.5 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 w-48 z-10">
                <button onClick={() => convert(false)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs font-semibold text-ink-primary"><M n="handyman" c="text-sm text-primary" />Create work order</button>
                <button onClick={() => convert(true)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs font-semibold text-ink-primary"><M n="request_quote" c="text-sm text-primary" />Create estimate</button>
              </div>
            )}
          </div>
          {lead.status === "new" && <Button onClick={() => move("in_progress", "Moved to In progress")} disabled={busy} icon={<M n="play_arrow" c="text-sm" />}>Start</Button>}
          <Button variant="danger" className="ml-auto" onClick={() => move("lost", "Marked lost")} disabled={busy}>Mark lost</Button>
        </>
      )}>
      <div className="space-y-5 text-xs">
          <div className="flex flex-wrap gap-2">
            {s && <span className={`h-6 px-2.5 rounded-full border text-2xs font-semibold inline-flex items-center gap-1 ${s.cls}`}><M n="timer" c="text-sm" />Respond by {fmt(lead.sla_due_at)} · {s.label}</span>}
            {calls && <Badge tone="danger"><M n="phone_missed" c="text-sm" />{calls}{lead.last_call_at ? ` · last ${fmt(lead.last_call_at)}` : ""}</Badge>}
          </div>
          {closed && (
            <div className="p-3 rounded-xl bg-emerald-50 border border-emerald-200 flex items-center justify-between">
              <span className="text-xs font-semibold text-emerald-800 flex items-center gap-1.5"><M n="check_circle" c="text-sm" />Converted to {lead.converted_order_number}</span>
              <Button variant="link" onClick={() => onOpenOrder(lead.converted_order_id!)}>Open work order</Button>
            </div>
          )}
          <InquiryFields value={form} onChange={setForm} types={types} creating={false} />
          {dirty && !closed && (
            <div className="flex items-center gap-2">
              <Button variant="primary" onClick={save} disabled={busy}>Save changes</Button>
              <Button onClick={() => setForm(lead)}>Undo changes</Button>
            </div>
          )}
          {err && <p role="alert" className="p-2.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-xs font-semibold">{err}</p>}
          <Timeline key={tick} leadId={lead.id} />
      </div>
    </Drawer>
  );
}
