import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { useEffect, useRef, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Avatar } from "../orders/look";
import { hueText, tint } from "../../lib/tint";
import { clickableRow } from "../../lib/clickable";

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
const fmt = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
const fieldCls = "bg-white border border-slate-200 rounded-lg text-ink-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/10";

function Pill({ hue, children }: { hue: string; children: React.ReactNode }) {
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap" style={{ background: tint(hue), color: hueText(hue) }}>{children}</span>;
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
  const [msgs, setMsgs] = useState<Msg[]>([]);
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
  const fileRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLInputElement>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  // Reload the current tab (and its counts); keep the open conversation in sync with the server copy.
  function load(t: Tab = tab, keep: string | null = sel?.id ?? null) {
    setLoadErr(null);
    return api.get<{ items: Thread[]; counts?: Counts }>(threadsUrl(t)).then((r) => {
      const items = r.items || [];
      setThreads(items); setCounts(r.counts || {});
      const still = keep ? items.find((x) => x.id === keep) : null;
      if (still) setSel(still);
      else if (keep === null && items[0] && window.matchMedia?.("(min-width: 768px)")?.matches !== false) open(items[0]);
    }).catch((e: any) => { setThreads([]); setLoadErr(e?.message || "Couldn't load conversations"); });
  }
  useEffect(() => { load(); }, []);  // eslint-disable-line
  function pickTab(t: Tab) { setTab(t); setThreads(null); setSel(null); setMsgs([]); load(t, null); }
  async function update(patch: { status?: ChatStatus; is_spam?: boolean }, done: string) {
    if (!sel || busy) return;
    setBusy(true); setMore(false);
    try {
      const t = await api.patch<Thread>(`/chats/threads/${sel.id}`, patch);
      flash(done);
      // Spam / restore moves the chat out of this list; a status change may too (filtered tab).
      const leaves = patch.is_spam !== undefined || (tab !== "all" && tab !== "spam" && t.status !== tab);
      if (leaves) { setSel(null); setMsgs([]); await load(tab, ""); } else { setSel({ ...sel, ...t }); await load(tab, sel.id); }
    } catch (e: any) { flash(`Failed: ${e.message}`); }
    finally { setBusy(false); }
  }
  function open(t: Thread) { setSel(t); setConvMenu(false); setMore(false); api.get<{ items: Msg[] }>(`/chats/threads/${t.id}/messages`).then((r) => setMsgs(r.items || [])).catch(() => setMsgs([])); }
  async function send(body?: string) {
    const b = (body ?? text).trim(); if (!b || !sel) return;
    try {
      const r = await api.post<{ thread_status?: ChatStatus }>(`/chats/threads/${sel.id}/send`, { body: b });
      setText("");
      // The reply may move the chat out of a filtered tab; keep its header status true anyway.
      const now = { ...sel, status: r?.thread_status || sel.status };
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
  const shown = (threads || []).filter((t) => !channel || t.channel === channel);
  const chName = (t: Thread) => CH[t.channel || "web"] || t.channel;

  const emptyNote = (
    <div className="px-6 py-10 text-center">
      <M n={tab === "spam" ? "report" : "chat"} c="text-2xl text-ink-placeholder" />
      <p className="text-xs text-ink-secondary mt-1.5 max-w-[240px] mx-auto leading-relaxed">{EMPTY[tab]}</p>
    </div>
  );

  return (
    <Shell title="Chats">
      <div className="px-4 md:px-6 pt-3 pb-2 border-b border-slate-200/60 flex items-center gap-2 flex-shrink-0">
        <div className="flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200/60 text-xs">
          <button onClick={() => setLayout("split")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1.5 ${layout === "split" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="chat_bubble" c="text-xs" />Conversation</button>
          <button onClick={() => setLayout("list")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1.5 ${layout === "list" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="table_rows" c="text-xs" />List</button>
        </div>
        <select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)} className={`${fieldCls} ml-auto text-xs font-medium pl-2.5 pr-7 py-1.5`}><option value="">All channels</option>{Object.entries(CH).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      </div>
      <div role="tablist" aria-label="Chat status" className="px-4 md:px-6 py-2 border-b border-slate-200/60 flex items-center gap-1 overflow-x-auto flex-shrink-0">
        {TABS.map(([k, label]) => {
          const n = counts[k] ?? 0;
          return (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => pickTab(k)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap flex items-center gap-1.5 border transition-colors ${tab === k ? "bg-white text-ink-primary border-slate-300 shadow-2xs" : "border-transparent text-ink-secondary hover:text-ink-primary hover:bg-slate-100"} ${k === "spam" ? "md:ml-auto" : ""}`}>
              {k === "spam" && <M n="report" c="text-xs" />}{label}
              {n > 0 && <span className="tabular-nums text-[11px] font-medium text-ink-tertiary">{n}</span>}
            </button>
          );
        })}
      </div>

      {layout === "list" ? (
        <div className="flex-1 overflow-y-auto p-4 md:p-6">
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-x-auto">
            <table className="w-full min-w-[720px] text-left border-collapse text-xs">
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-xs font-semibold">
                <th className="py-3 px-4 font-semibold">Contact</th><th className="py-3 px-4 font-semibold">Status</th><th className="py-3 px-4 font-semibold">Channel</th><th className="py-3 px-4 font-semibold">Last message</th><th className="py-3 px-4 font-semibold">When</th><th className="py-3 px-4 font-semibold">Linked</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 text-ink-secondary">
                {!threads && <SkeletonRows cols={6} />}
                {loadErr && <tr><td colSpan={6} className="py-6 px-4 text-rose-600">{loadErr} <button onClick={() => load()} className="ml-2 font-semibold text-primary hover:underline">Try again</button></td></tr>}
                {threads && !loadErr && shown.length === 0 && <tr><td colSpan={6}>{emptyNote}</td></tr>}
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
        </div>
      ) : (
      <div className="flex-1 overflow-hidden flex">
        {/* thread list */}
        <aside className={`w-full md:w-80 flex-shrink-0 border-r border-slate-200/80 bg-white flex-col ${sel ? "hidden md:flex" : "flex"}`}>
          <div className="px-4 py-2.5 border-b border-slate-100 text-xs font-semibold text-ink-secondary">
            Conversations{threads && !loadErr ? <span className="text-ink-tertiary font-medium tabular-nums"> · {shown.length}</span> : null}
          </div>
          <div className="flex-1 overflow-y-auto">
            {!threads && <div className="p-4 text-xs text-ink-tertiary">Loading…</div>}
            {loadErr && <div className="p-6 text-xs text-rose-600 text-center">{loadErr}<br /><button onClick={() => load()} className="mt-2 font-semibold text-primary hover:underline">Try again</button></div>}
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
                        <span className={`text-[13px] text-ink-primary truncate ${waiting ? "font-semibold" : "font-medium"}`}>{nameOf(t)}</span>
                        <span className="text-[11px] text-ink-tertiary tabular-nums flex-shrink-0">{fmt(t.last_message_at)}</span>
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
        <div className={`flex-1 min-w-0 flex-col ${sel ? "flex" : "hidden md:flex"}`}>
          {!sel ? (
            <div className="flex-1 grid place-items-center text-center bg-canvas px-6">
              <div>
                <M n="chat" c="text-3xl text-ink-placeholder" />
                <p className="text-sm text-ink-secondary mt-2 max-w-[260px] leading-relaxed">Pick a conversation to reply, or turn it into a work order.</p>
              </div>
            </div>
          ) : (
            <>
              <div className="px-3 md:px-6 py-3 border-b border-slate-200/80 bg-white flex flex-wrap items-center gap-x-3 gap-y-2">
                <button onClick={() => setSel(null)} aria-label="Back to conversations" className="md:hidden w-10 h-10 -ml-1 rounded-lg flex items-center justify-center text-ink-secondary hover:bg-slate-100 shrink-0"><M n="arrow_back" c="text-base" /></button>
                <Avatar name={nameOf(sel)} size={36} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 min-w-0"><h3 className="text-sm font-semibold text-ink-primary truncate">{nameOf(sel)}</h3>{sel.is_spam ? <Pill hue={SPAM_HUE}>Spam</Pill> : <StatusBadge status={sel.status} />}</div>
                  <p className="text-xs text-ink-tertiary truncate tabular-nums">{chName(sel)}{sel.contact_phone ? ` · ${sel.contact_phone}` : ""}</p>
                </div>
                <div className="w-full sm:w-auto flex items-center justify-end gap-1.5 flex-shrink-0">
                  {canManage && sel.is_spam && (
                    <button onClick={() => update({ is_spam: false }, "Moved back to the inbox")} disabled={busy} className="h-9 px-3 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-ink-primary hover:bg-slate-50 hover:border-slate-300 flex items-center gap-1.5"><M n="undo" c="text-sm" />Not spam</button>
                  )}
                  {canManage && !sel.is_spam && sel.status !== "completed" && (
                    <button onClick={() => update({ status: "completed" }, "Marked as completed")} disabled={busy} className="h-9 px-3 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-ink-primary hover:bg-slate-50 hover:border-slate-300 flex items-center gap-1.5 disabled:opacity-50"><M n="check" c="text-sm" />Complete</button>
                  )}
                  {/* Create split button */}
                  <div className="relative">
                    {sel.order_id ? (
                      <span className="h-9 text-xs font-semibold px-3 rounded-lg flex items-center gap-1.5" style={{ background: tint(STATUS.replied.hue), color: hueText(STATUS.replied.hue) }}><M n="check_circle" c="text-sm" />Linked to a work order</span>
                    ) : (
                      <>
                        <button onClick={() => { setConvMenu((v) => !v); setMore(false); }} aria-expanded={convMenu} className="h-9 px-3.5 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container flex items-center gap-1.5"><M n="add" c="text-sm" />Create<M n="expand_more" c="text-sm" /></button>
                        {convMenu && (<>
                          <div className="fixed inset-0 z-10" aria-hidden="true" onClick={() => setConvMenu(false)} />
                          <div className="absolute top-full right-0 mt-1.5 bg-white border border-slate-200 rounded-xl shadow-menu p-1 w-48 z-20">
                            <button onClick={() => convert("order")} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-xs font-medium text-ink-primary"><M n="handyman" c="text-sm text-ink-tertiary" />Work order</button>
                            <button onClick={() => convert("estimate")} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-xs font-medium text-ink-primary"><M n="request_quote" c="text-sm text-ink-tertiary" />Estimate</button>
                            <button onClick={() => convert("inquiry")} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-xs font-medium text-ink-primary"><M n="contact_support" c="text-sm text-ink-tertiary" />Inquiry</button>
                          </div>
                        </>)}
                      </>
                    )}
                  </div>
                  {canManage && !sel.is_spam && (
                    <div className="relative">
                      <button onClick={() => { setMore((v) => !v); setConvMenu(false); }} aria-label="More actions" aria-expanded={more} className="w-9 h-9 rounded-lg grid place-items-center text-ink-secondary border border-slate-200 bg-white hover:bg-slate-50 hover:text-ink-primary"><M n="more_vert" c="text-base" /></button>
                      {more && (<>
                        <div className="fixed inset-0 z-10" aria-hidden="true" onClick={() => setMore(false)} />
                        <div className="absolute top-full right-0 mt-1.5 bg-white border border-slate-200 rounded-xl shadow-menu p-1 w-56 z-20">
                          <label className="block px-2.5 pt-2 pb-1 text-xs font-medium text-ink-tertiary" htmlFor="chat-status">Status</label>
                          <div className="px-2.5 pb-2">
                            <select id="chat-status" aria-label="Chat status" value={sel.status || "new"} disabled={busy}
                              onChange={(e) => update({ status: e.target.value as ChatStatus }, `Marked as ${STATUS[e.target.value as ChatStatus].label.toLowerCase()}`)}
                              className={`${fieldCls} w-full text-xs font-medium px-2.5 py-1.5`}>
                              {(Object.keys(STATUS) as ChatStatus[]).map((k) => <option key={k} value={k}>{STATUS[k].label}</option>)}
                            </select>
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
                {msgs.length === 0 && <p className="text-xs text-ink-tertiary text-center pt-6">No messages yet. Send the first one below.</p>}
                {msgs.map((m) => {
                  const out = m.direction === "out" || m.direction === "outbound";
                  return (
                    <div key={m.id} className={`flex flex-col max-w-[85%] md:max-w-[70%] ${out ? "ml-auto items-end" : "items-start"}`}>
                      <div className={`px-3 py-2 rounded-2xl text-[13px] leading-relaxed ${out ? "bg-primary text-white rounded-br-md" : "bg-white border border-slate-200 text-ink-primary rounded-bl-md shadow-2xs"}`}>{m.body}</div>
                      <span className="text-[11px] text-ink-tertiary mt-1 tabular-nums">{fmt(m.created_at)}{m.status ? ` · ${humanize(m.status)}` : ""}</span>
                    </div>
                  );
                })}
              </div>
              {/* quick replies */}
              <div className="px-3 md:px-4 pt-2.5 flex items-center gap-1.5 overflow-x-auto md:flex-wrap border-t border-slate-200/80 bg-white">
                <span className="text-xs font-medium text-ink-tertiary mr-0.5 flex-shrink-0">Quick replies</span>
                {QUICK.map((q) => <button key={q} title={q} onClick={() => insertQuick(q)} className="flex-shrink-0 px-2.5 py-1 rounded-full border border-slate-200 bg-white hover:bg-slate-50 hover:border-slate-300 text-ink-secondary hover:text-ink-primary text-xs">{q.length > 34 ? q.slice(0, 34) + "…" : q}</button>)}
              </div>
              <div className="p-3 md:p-4 pt-2 bg-white flex items-center gap-2">
                <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => { if (e.target.files?.[0]) flash("Image attach needs a connected channel provider"); e.currentTarget.value = ""; }} />
                <button aria-label="Attach image" onClick={() => fileRef.current?.click()} title="Attach image" className="w-10 h-10 flex-shrink-0 rounded-xl border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 flex items-center justify-center"><M n="image" c="text-base" /></button>
                <input ref={composerRef} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} className={`${fieldCls} flex-1 min-w-0 h-10 px-3 text-sm rounded-xl`} placeholder="Message…" />
                <button onClick={() => send()} disabled={!text.trim()} className="h-10 px-4 rounded-xl bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-50 flex items-center gap-1.5"><span>Send</span><M n="send" c="text-sm" /></button>
              </div>
            </>
          )}
        </div>
      </div>
      )}
      {toast && <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-menu z-[70]">{toast}</div>}
    </Shell>
  );
}
