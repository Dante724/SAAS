import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { fmtServerTime } from "../../lib/serverTime";
import { useEffect, useRef, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Avatar } from "../orders/look";
import { hueText, tint } from "../../lib/tint";
import { clickableRow } from "../../lib/clickable";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Button, { IconButton } from "../../components/Button";
import Tabs from "../../components/Tabs";
import Card from "../../components/Card";
import EmptyState, { ErrorState } from "../../components/EmptyState";
import { Input, Select } from "../../components/Field";
import { tableClass, theadRowClass, Th } from "../../components/Table";
import SearchBox from "../orders/SearchBox";

export type ChatStatus = "new" | "reply_needed" | "replied" | "completed";
type Thread = { id: string; channel?: string; contact_phone?: string; contact_name?: string; client_name?: string; last_body?: string; last_message_at?: string; order_id?: string | null; status?: ChatStatus; is_spam?: boolean };
type Counts = Partial<Record<ChatStatus | "all" | "spam", number>>;
// A tab is a status filter over the inbox, or the Spam folder.
export type Tab = "all" | ChatStatus | "spam";
type Msg = { id: string; direction?: string; body?: string; status?: string; created_at?: string };

const CH: Record<string, string> = { whatsapp: "WhatsApp", sms: "SMS", email: "Email", instagram: "Instagram", facebook: "Facebook", telegram: "Telegram", web: "Web" };
const QUICK = ["On our way to diagnose it now.", "It's ready for pickup!", "Your estimate is approved — starting the repair.", "Can you confirm a good time to call?"];
// Soft-tint pills in fixed hues (same palette as status colours), so every status reads the same in light and dark.
export const STATUS: Record<ChatStatus, { label: string; hue: string }> = {
  new: { label: "New", hue: "#378ADD" },
  reply_needed: { label: "Reply needed", hue: "#BA7517" },
  replied: { label: "Replied", hue: "#1D9E75" },
  completed: { label: "Completed", hue: "#888780" },
};
const SPAM_HUE = "#D85A30";
export const TABS: [Tab, string][] = [["all", "All"], ["new", "New"], ["reply_needed", "Reply needed"], ["replied", "Replied"], ["completed", "Completed"], ["spam", "Spam"]];
export const threadsUrl = (tab: Tab) => (tab === "spam" ? "/chats/threads?folder=spam" : `/chats/threads?status=${tab}`);
const EMPTY: Record<Tab, string> = {
  all: "No conversations yet. Messages from customers land here.", new: "No new conversations.", reply_needed: "Nobody is waiting on a reply.",
  replied: "No replied conversations.", completed: "No completed conversations.", spam: "Spam is empty.",
};
const fmt = (iso?: string) => fmtServerTime(iso);

function Pill({ hue, children }: { hue: string; children: React.ReactNode }) {
  return <span className="inline-flex items-center h-6 px-2 rounded-full text-2xs font-semibold whitespace-nowrap" style={{ background: tint(hue), color: hueText(hue) }}>{children}</span>;
}
export function StatusBadge({ status }: { status?: ChatStatus }) {
  const s = STATUS[status || "new"];
  return <Pill hue={s.hue}>{s.label}</Pill>;
}

export default function ChatsHub() {
  const { can } = useMe();
  const canManage = can("can_manage_chats");
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("all");
  const [counts, setCounts] = useState<Counts>({});
  const [busy, setBusy] = useState(false);
  const [layout, setLayout] = useState<"split" | "list">("split");
  const [channel, setChannel] = useState("");
  const [sel, setSel] = useState<Thread | null>(null);
  // Messages are kept with the chat they belong to, so a slow reply for one customer can never show
  // under another's name (B10): only the open chat's messages render.
  const [thread, setThread] = useState<{ id: string | null; items: Msg[] }>({ id: null, items: [] });
  const msgs = thread.id && thread.id === sel?.id ? thread.items : [];
  const clearMsgs = () => setThread({ id: null, items: [] });
  // The latest list / conversation request; an answer to an older one is dropped.
  const loadSeq = useRef(0);
  const openSeq = useRef(0);
  const selId = useRef<string | null>(null);
  selId.current = sel?.id ?? null;
  const [text, setText] = useState("");
  const [convMenu, setConvMenu] = useState(false);
  const [more, setMore] = useState(false);
  // Escape closes whichever thread menu is open (keyboard users can't reach the backdrop).
  useEffect(() => {
    if (!more && !convMenu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setMore(false); setConvMenu(false); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [more, convMenu]);
  const [toast, setToast] = useState<string | null>(null);
  const composerRef = useRef<HTMLInputElement>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  // Reload the current tab (and its counts); keep the open conversation in sync with the server copy.
  function load(t: Tab = tab, keep: string | null = sel?.id ?? null) {
    setLoadErr(null);
    const seq = ++loadSeq.current;
    return api.get<{ items: Thread[]; counts?: Counts }>(threadsUrl(t)).then((r) => {
      if (seq !== loadSeq.current) return;
      const items = r.items || [];
      setThreads(items); setCounts(r.counts || {});
      const still = keep ? items.find((x) => x.id === keep) : null;
      if (still) { if (still.id === selId.current) setSel(still); }
      else if (keep === null && items[0] && window.matchMedia?.("(min-width: 768px)")?.matches !== false) open(items[0]);
    }).catch((e: any) => { if (seq !== loadSeq.current) return; setThreads([]); setLoadErr(e?.message || "Couldn't load conversations"); });
  }
  useEffect(() => { load(); }, []);  // eslint-disable-line
  function pickTab(t: Tab) { setTab(t); setThreads(null); setSel(null); clearMsgs(); load(t, null); }
  async function update(patch: { status?: ChatStatus; is_spam?: boolean }, done: string) {
    if (!sel || busy) return;
    setBusy(true); setMore(false);
    try {
      const t = await api.patch<Thread>(`/chats/threads/${sel.id}`, patch);
      flash(done);
      // Spam / restore moves the chat out of this list; a status change may too (filtered tab).
      const leaves = patch.is_spam !== undefined || (tab !== "all" && tab !== "spam" && t.status !== tab);
      if (selId.current !== sel.id) { await load(tab, selId.current); return; }   // moved on to another chat meanwhile
      if (leaves) { setSel(null); clearMsgs(); await load(tab, ""); } else { setSel((cur) => (cur?.id === t.id ? { ...cur, ...t } : cur)); await load(tab, sel.id); }
    } catch (e: any) { flash(`Failed: ${e.message}`); }
    finally { setBusy(false); }
  }
  function open(t: Thread) {
    const seq = ++openSeq.current;
    selId.current = t.id;
    setSel(t); setConvMenu(false); setMore(false);
    setThread((cur) => (cur.id === t.id ? cur : { id: t.id, items: [] }));   // a reload of the same chat keeps what's shown
    const got = (items: Msg[]) => { if (seq === openSeq.current) setThread({ id: t.id, items }); };
    api.get<{ items: Msg[] }>(`/chats/threads/${t.id}/messages`).then((r) => got(r.items || [])).catch(() => got([]));
  }
  async function send(body?: string) {
    const b = (body ?? text).trim(); if (!b || !sel) return;
    try {
      const r = await api.post<{ thread_status?: ChatStatus }>(`/chats/threads/${sel.id}/send`, { body: b });
      setText("");
      // The reply may move the chat out of a filtered tab; keep its header status true anyway.
      const now = { ...sel, status: r?.thread_status || sel.status };
      if (selId.current !== sel.id) { load(tab, selId.current); return; }   // moved on to another chat meanwhile
      open(now); load(tab, sel.id);
    } catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  // A quick reply is a starting point: it lands in the composer so it can be edited before sending.
  function insertQuick(q: string) {
    setText((t) => (t.trim() ? `${t.trimEnd()} ${q}` : q));
    requestAnimationFrame(() => composerRef.current?.focus());
  }
  async function convert(kind: "order" | "estimate" | "inquiry") {
    if (!sel) return; setConvMenu(false);
    try {
      if (kind === "inquiry") { await api.post(`/chats/threads/${sel.id}/to-lead`, {}); flash("Inquiry created from chat"); }
      else { const r = await api.post<any>(`/chats/threads/${sel.id}/to-order${kind === "estimate" ? "?is_estimate=true" : ""}`, {}); flash(`${kind === "estimate" ? "Estimate" : "Work order"} ${r.order_number} created`); }
      load();
    } catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  const nameOf = (t: Thread) => t.client_name || t.contact_name || t.contact_phone || "Unknown";
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  // Search runs over the loaded conversations: name, phone and the last message.
  const shown = (threads || []).filter((t) => (!channel || t.channel === channel)
    && (!needle || [nameOf(t), t.contact_phone, t.last_body].some((v) => (v || "").toLowerCase().includes(needle))));
  const chName = (t: Thread) => CH[t.channel || "web"] || t.channel;

  const emptyNote = needle
    ? <EmptyState icon={<M n="search" c="text-lg" />} title={`Nothing matches “${q.trim()}”`} body="Try a name, a phone number or words from the message." />
    : <EmptyState icon={<M n={tab === "spam" ? "report" : "chat"} c="text-lg" />} title={EMPTY[tab]} />;
  const errorNote = <ErrorState body={loadErr} onRetry={() => load()} />;

  return (
    <Shell title="Chats">
      {/* One toolbar and one status row for every tab and both layouts: nothing above the
          conversations ever moves. View switch on the left, channel filter and search on the right. */}
      <div className="px-6 pt-5 space-y-3 bg-canvas flex-shrink-0">
        <Toolbar
          view={<Tabs look="segmented" label="Chat layout" value={layout} onChange={setLayout} tabs={[
            { id: "split", label: "Conversation", icon: <M n="chat_bubble" c="text-xs" /> },
            { id: "list", label: "List", icon: <M n="table_rows" c="text-xs" /> },
          ]} />}
          filters={<Select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)} className="sm:!w-40"><option value="">All channels</option>{Object.entries(CH).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>}
          search={<SearchBox value={q} onChange={setQ} label="Search conversations" placeholder="Search name, phone or message…" />}
        />
        <Tabs label="Chat status" value={tab} onChange={pickTab}
          tabs={TABS.map(([k, label]) => ({ id: k, label, icon: k === "spam" ? <M n="report" c="text-xs" /> : undefined, count: (counts[k] ?? 0) > 0 ? counts[k] : null }))} />
      </div>

      {layout === "list" ? (
        <PageLayout>
          <Card padded={false}>
            <div className="overflow-x-auto">
            <table className={`${tableClass} min-w-[720px]`}>
              <thead><tr className={theadRowClass}>
                <Th>Contact</Th><Th>Status</Th><Th>Channel</Th><Th>Last message</Th><Th>When</Th><Th>Linked</Th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 text-ink-secondary">
                {!threads && <SkeletonRows cols={6} />}
                {loadErr && <tr><td colSpan={6} className="p-0">{errorNote}</td></tr>}
                {threads && !loadErr && shown.length === 0 && <tr><td colSpan={6} className="p-0">{emptyNote}</td></tr>}
                {shown.map((t) => {
                  const waiting = !t.is_spam && t.status === "reply_needed";
                  return (
                    <tr key={t.id} {...clickableRow(() => { setLayout("split"); open(t); })} className="hover:bg-slate-50 cursor-pointer">
                      <td className="py-2.5 px-4 relative">
                        {waiting && <span aria-hidden className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full" style={{ background: STATUS.reply_needed.hue }} />}
                        <span className="flex items-center gap-2.5"><Avatar name={nameOf(t)} size={26} /><span className={`text-ink-primary ${waiting ? "font-semibold" : "font-medium"}`}>{nameOf(t)}</span></span>
                      </td>
                      <td className="py-2.5 px-4">{t.is_spam ? <Pill hue={SPAM_HUE}>Spam</Pill> : <StatusBadge status={t.status} />}</td>
                      <td className="py-2.5 px-4 text-ink-tertiary">{chName(t)}</td>
                      <td className="py-2.5 px-4 text-ink-tertiary truncate max-w-[320px]">{t.last_body || "—"}</td>
                      <td className="py-2.5 px-4 tabular-nums whitespace-nowrap">{fmt(t.last_message_at)}</td>
                      <td className="py-2.5 px-4">{t.order_id ? <span className="text-primary font-medium">Work order</span> : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          </Card>
        </PageLayout>
      ) : (
      <PageLayout mode="split">
        {/* thread list */}
        <aside className={`w-full md:w-80 flex-shrink-0 border-r border-slate-200/80 bg-white flex-col min-h-0 ${sel ? "hidden md:flex" : "flex"}`}>
          <div className="px-4 py-2.5 border-b border-slate-100 text-xs font-semibold text-ink-secondary">
            Conversations{threads && !loadErr ? <span className="text-ink-tertiary font-medium tabular-nums"> · {shown.length}</span> : null}
          </div>
          <div className="flex-1 overflow-y-auto">
            {!threads && <div aria-hidden className="p-4 space-y-4">{[0, 1, 2, 3].map((i) => <div key={i} className="flex items-center gap-3"><span className="sk block h-9 w-9 rounded-full" /><span className="flex-1 space-y-1.5"><span className="sk block h-3 w-2/3 rounded" /><span className="sk block h-3 w-1/2 rounded" /></span></div>)}</div>}
            {!threads && <span className="sr-only" role="status">Loading…</span>}
            {loadErr && errorNote}
            {threads && !loadErr && shown.length === 0 && emptyNote}
            {shown.map((t) => {
              const waiting = !t.is_spam && t.status === "reply_needed";
              return (
                <button key={t.id} onClick={() => open(t)} className={`relative w-full text-left px-4 py-3 border-b border-slate-100 transition-colors ${sel?.id === t.id ? "bg-slate-100/80" : "hover:bg-slate-50"}`}>
                  {waiting && <span aria-hidden className="absolute left-0 top-2.5 bottom-2.5 w-[3px] rounded-r-full" style={{ background: STATUS.reply_needed.hue }} />}
                  <div className="flex items-start gap-3">
                    <Avatar name={nameOf(t)} size={36} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className={`text-sm text-ink-primary truncate ${waiting ? "font-semibold" : "font-medium"}`}>{nameOf(t)}</span>
                        <span className="text-2xs text-ink-tertiary tabular-nums flex-shrink-0">{fmt(t.last_message_at)}</span>
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className={`text-xs truncate flex-1 min-w-0 ${waiting ? "text-ink-secondary" : "text-ink-tertiary"}`}><span className="font-medium">{chName(t)}</span> · {t.last_body || t.contact_phone}</span>
                        {!t.is_spam && t.status && t.status !== "completed" && <span className="flex-shrink-0"><StatusBadge status={t.status} /></span>}
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </aside>

        {/* conversation */}
        <div className={`flex-1 min-w-0 min-h-0 flex-col ${sel ? "flex" : "hidden md:flex"}`}>
          {!sel ? (
            <div className="flex-1 grid place-items-center bg-canvas">
              <EmptyState icon={<M n="chat" c="text-lg" />} title="No conversation open" body="Pick a conversation to reply, or turn it into a work order." />
            </div>
          ) : (
            <>
              <div className="px-3 md:px-6 py-3 border-b border-slate-200/80 bg-white space-y-2.5">
                <div className="flex items-center gap-3 min-w-0">
                  <IconButton label="Back to conversations" onClick={() => setSel(null)} className="md:hidden -ml-1" icon={<M n="arrow_back" c="text-base" />} />
                  <Avatar name={nameOf(sel)} size={36} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 min-w-0"><h2 className="text-sm font-semibold text-ink-primary truncate">{nameOf(sel)}</h2>{sel.is_spam ? <Pill hue={SPAM_HUE}>Spam</Pill> : <StatusBadge status={sel.status} />}</div>
                    <p className="text-xs text-ink-tertiary truncate tabular-nums">{chName(sel)}{sel.contact_phone ? ` · ${sel.contact_phone}` : ""}</p>
                  </div>
                </div>
                {/* Conversation actions on the left, main one (Create) first. */}
                <div className="flex flex-wrap items-center gap-1.5">
                  <div className="relative">
                    {sel.order_id ? (
                      <span className="h-9 text-xs font-semibold px-3 rounded-xl flex items-center gap-1.5" style={{ background: tint(STATUS.replied.hue), color: hueText(STATUS.replied.hue) }}><M n="check_circle" c="text-sm" />Linked to a work order</span>
                    ) : (
                      <>
                        <Button variant="primary" onClick={() => { setConvMenu((v) => !v); setMore(false); }} aria-expanded={convMenu} icon={<M n="add" c="text-sm" />}>Create<M n="expand_more" c="text-sm" /></Button>
                        {convMenu && (<>
                          <div className="fixed inset-0 z-10" aria-hidden="true" onClick={() => setConvMenu(false)} />
                          <div className="absolute top-full left-0 mt-1.5 bg-white border border-slate-200 rounded-xl shadow-menu p-1 w-48 z-20">
                            <button onClick={() => convert("order")} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-xs font-medium text-ink-primary"><M n="handyman" c="text-sm text-ink-tertiary" />Work order</button>
                            <button onClick={() => convert("estimate")} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-xs font-medium text-ink-primary"><M n="request_quote" c="text-sm text-ink-tertiary" />Estimate</button>
                            <button onClick={() => convert("inquiry")} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-xs font-medium text-ink-primary"><M n="contact_support" c="text-sm text-ink-tertiary" />Inquiry</button>
                          </div>
                        </>)}
                      </>
                    )}
                  </div>
                  {canManage && sel.is_spam && (
                    <Button onClick={() => update({ is_spam: false }, "Moved back to the inbox")} disabled={busy} icon={<M n="undo" c="text-sm" />}>Not spam</Button>
                  )}
                  {canManage && !sel.is_spam && sel.status !== "completed" && (
                    <Button onClick={() => update({ status: "completed" }, "Marked as completed")} disabled={busy} icon={<M n="check" c="text-sm" />}>Complete</Button>
                  )}
                  {canManage && !sel.is_spam && (
                    <div className="relative">
                      <IconButton variant="secondary" label="More actions" aria-expanded={more} onClick={() => { setMore((v) => !v); setConvMenu(false); }} icon={<M n="more_vert" c="text-base" />} />
                      {more && (<>
                        <div className="fixed inset-0 z-10" aria-hidden="true" onClick={() => setMore(false)} />
                        <div className="absolute top-full left-0 mt-1.5 bg-white border border-slate-200 rounded-xl shadow-menu p-1 w-56 z-20">
                          <label className="block px-2.5 pt-2 pb-1 text-xs font-medium text-ink-tertiary" htmlFor="chat-status">Status</label>
                          <div className="px-2.5 pb-2">
                            <Select id="chat-status" aria-label="Chat status" value={sel.status || "new"} disabled={busy}
                              onChange={(e) => update({ status: e.target.value as ChatStatus }, `Marked as ${STATUS[e.target.value as ChatStatus].label.toLowerCase()}`)}>
                              {(Object.keys(STATUS) as ChatStatus[]).map((k) => <option key={k} value={k}>{STATUS[k].label}</option>)}
                            </Select>
                          </div>
                          <div className="border-t border-slate-100 my-1" />
                          <button onClick={() => update({ is_spam: true }, "Moved to spam")} disabled={busy} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-rose-50 text-xs font-medium text-rose-600"><M n="report" c="text-sm" />Mark as spam</button>
                        </div>
                      </>)}
                    </div>
                  )}
                </div>
              </div>
              <div className="flex-1 overflow-y-auto p-3 md:p-6 space-y-3 bg-canvas">
                {msgs.length === 0 && <EmptyState size="inline" className="text-center pt-6" title="No messages yet. Send the first one below." />}
                {msgs.map((m) => {
                  const out = m.direction === "out" || m.direction === "outbound";
                  return (
                    <div key={m.id} className={`flex flex-col max-w-[85%] md:max-w-[70%] ${out ? "ml-auto items-end" : "items-start"}`}>
                      <div className={`px-3 py-2 rounded-2xl text-sm leading-relaxed ${out ? "bg-primary text-white rounded-br-md" : "bg-white border border-slate-200 text-ink-primary rounded-bl-md shadow-2xs"}`}>{m.body}</div>
                      <span className="text-2xs text-ink-tertiary mt-1 tabular-nums">{fmt(m.created_at)}{m.status ? ` · ${humanize(m.status)}` : ""}</span>
                    </div>
                  );
                })}
              </div>
              {/* quick replies */}
              <div className="px-3 md:px-4 pt-2.5 flex items-center gap-1.5 overflow-x-auto no-scrollbar md:flex-wrap border-t border-slate-200/80 bg-white">
                <span className="text-xs font-medium text-ink-tertiary mr-0.5 flex-shrink-0">Quick replies</span>
                {QUICK.map((q) => <button key={q} title={q} onClick={() => insertQuick(q)} className="flex-shrink-0 h-8 px-3 rounded-full ring-1 ring-inset ring-slate-200 bg-white hover:bg-slate-50 text-ink-secondary hover:text-ink-primary text-xs font-semibold">{q.length > 34 ? q.slice(0, 34) + "…" : q}</button>)}
              </div>
              <div className="p-3 md:p-4 pt-2 bg-white flex items-center gap-2">
                {/* No channel can send images yet, so there is no attach button (it could only ever fail). */}
                <Input ref={composerRef} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} aria-label="Message" className="flex-1 min-w-0" placeholder="Message…" />
                <Button variant="primary" onClick={() => send()} disabled={!text.trim()}><span>Send</span><M n="send" c="text-sm" /></Button>
              </div>
            </>
          )}
        </div>
      </PageLayout>
      )}
      {toast && <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-menu z-[70]">{toast}</div>}
    </Shell>
  );
}
