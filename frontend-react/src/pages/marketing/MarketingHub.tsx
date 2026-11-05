import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { safeHttpsUrl } from "../../lib/safeHtml";
import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import Pager from "../../components/Pager";
import { confirmDialog, alertDialog } from "../../lib/dialog";
import { Link } from "react-router-dom";
import { Avatar } from "../orders/look";
import { PhoneIncoming, PhoneOutgoing } from "lucide-react";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Tabs from "../../components/Tabs";
import Button from "../../components/Button";
import Card, { Badge } from "../../components/Card";
import Drawer from "../../components/Drawer";
import { Field, Input, Select, fieldClass } from "../../components/Field";
import { TableEmpty } from "../../components/EmptyState";
import { Th, tableClass, theadRowClass } from "../../components/Table";

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
      <div className="px-6 flex-shrink-0">
        <Tabs look="underline" label="Marketing sections" value={tab} onChange={setTab}
          tabs={[{ id: "campaigns", label: "Campaigns" }, { id: "calls", label: "Call log" }]} />
      </div>
      <PageLayout>
        {/* Same toolbar on both tabs, so the table starts at the same height. */}
        <Toolbar actions={<Button variant="primary" icon={<M n="add" c="text-sm" />} disabled={tab !== "campaigns"}
          onClick={() => setDraft({ channel: "sms", segment: "all" })}>New campaign</Button>} />
        {tab === "campaigns" && (
            <Card padded={false}>
              <div className="overflow-x-auto">
              <table className={`${tableClass} min-w-[640px]`}>
                <thead><tr className={theadRowClass}><Th>Name</Th><Th>Channel</Th><Th>Segment</Th><Th>Status</Th><Th num>Recipients</Th><Th num>Action</Th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {!camps && <SkeletonRows cols={6} />}
                  {camps?.length === 0 && <TableEmpty cols={6} icon={<M n="campaign" c="text-xl" />} title="No campaigns yet" body="Write one SMS or email and send it to a group of clients." />}
                  {pageCamps.map((c) => (
                    <tr key={c.id} className="hover:bg-slate-50 text-ink-secondary">
                      <td className="py-2.5 px-4 font-semibold text-ink-primary">{c.name}</td>
                      <td className="py-2.5 px-4">{CHANNEL[c.channel] || humanize(c.channel)}</td>
                      <td className="py-2.5 px-4">{SEGMENT[c.segment] || humanize(c.segment)}</td>
                      <td className="py-2.5 px-4"><Badge tone={c.status === "sent" ? "success" : "warning"}>{humanize(c.status)}</Badge>{c.sent_at ? <span className="block text-2xs text-ink-tertiary mt-0.5">{d(c.sent_at)}</span> : null}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums whitespace-nowrap">{c.recipients ?? "—"}</td>
                      <td className="py-1.5 px-4 text-right">{c.status !== "sent" && <Button size="sm" variant="primary" onClick={() => send(c)} disabled={busy}>Send</Button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {camps && camps.length > 0 && <Pager total={camps.length} page={Math.min(mkPage, campPages)} pageSize={MK_PAGE} onPage={setMkPage} noun="campaigns" />}
            </Card>
        )}

        {tab === "calls" && (
          <Card padded={false}>
            <div className="overflow-x-auto">
            <table className={`${tableClass} min-w-[760px]`}>
              <thead><tr className={theadRowClass}><Th>When</Th><Th>Direction</Th><Th>Contact</Th><Th>Status</Th><Th num>Duration</Th><Th>Recording</Th></tr></thead>
              <tbody className="divide-y divide-slate-100 text-ink-secondary">
                {!calls && <SkeletonRows cols={6} />}
                {calls?.length === 0 && (
                  <TableEmpty cols={6} icon={<M n="call" c="text-xl" />} title="No calls yet" body="Connect a phone line and every call in and out is logged here."
                    action={<Link to="/settings?section=integrations" className="text-xs font-semibold text-primary hover:underline">Set up Calls &amp; telephony</Link>} />
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
                            <div className="text-sm font-medium text-ink-primary truncate">{name || theirs || "Unknown number"}</div>
                            {sub && <div className="text-xs text-ink-tertiary tabular-nums whitespace-nowrap">{sub}</div>}
                          </div>
                        </div>
                      </td>
                      <td className={`py-2.5 px-4 ${missed ? "text-rose-600 font-medium" : ""}`}>{humanize(c.status)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums whitespace-nowrap">{c.duration_s != null ? `${Math.floor(c.duration_s / 60)}:${String(c.duration_s % 60).padStart(2, "0")}` : "—"}</td>
                      <td className="py-2.5 px-4">{safeHttpsUrl(c.recording_url) ? <a href={safeHttpsUrl(c.recording_url)!} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-primary font-semibold hover:underline"><M n="play_arrow" c="text-sm" />Play</a> : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
            {calls && calls.length > 0 && <Pager total={calls.length} page={Math.min(mkPage, callPages)} pageSize={MK_PAGE} onPage={setMkPage} noun="calls" />}
          </Card>
        )}
      </PageLayout>

      {/* "New …" opens a side panel everywhere in the app, so a campaign does too. */}
      {draft && (
        <Drawer size="sm" label="New campaign" title="New campaign" onClose={() => setDraft(null)}
          footer={<><Button variant="primary" onClick={saveCampaign} disabled={busy || !draft.name?.trim()}>Save draft</Button><Button onClick={() => setDraft(null)}>Cancel</Button></>}>
          <Field label="Campaign name" htmlFor="mk-name"><Input id="mk-name" value={draft.name || ""} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Send by" htmlFor="mk-channel"><Select id="mk-channel" value={draft.channel} onChange={(e) => setDraft({ ...draft, channel: e.target.value })}><option value="sms">SMS</option><option value="email">Email</option></Select></Field>
            <Field label="Send to" htmlFor="mk-segment"><Select id="mk-segment" value={draft.segment} onChange={(e) => setDraft({ ...draft, segment: e.target.value })}><option value="all">All clients</option><option value="loyalty">Loyalty members</option><option value="lapsed">Lapsed clients</option></Select></Field>
          </div>
          {draft.channel === "email" && <Field label="Email subject" htmlFor="mk-subject"><Input id="mk-subject" value={draft.subject || ""} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} /></Field>}
          <Field label="Message" htmlFor="mk-body"><textarea id="mk-body" value={draft.body || ""} onChange={(e) => setDraft({ ...draft, body: e.target.value })} className={fieldClass("!h-28 py-2 resize-none")} /></Field>
        </Drawer>
      )}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-menu z-[70]">{toast}</div>}
    </Shell>
  );
}
