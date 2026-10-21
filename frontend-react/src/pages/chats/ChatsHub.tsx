import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { useEffect, useRef, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";

export type ChatStatus = "new" | "reply_needed" | "replied" | "completed";
type Thread = { id: string; channel?: string; contact_phone?: string; contact_name?: string; client_name?: string; last_body?: string; last_message_at?: string; order_id?: string | null; status?: ChatStatus; is_spam?: boolean };
type Counts = Partial<Record<ChatStatus | "all" | "spam", number>>;
// A tab is a status filter over the inbox, or the Spam folder.
export type Tab = "all" | ChatStatus | "spam";
type Msg = { id: string; direction?: string; body?: string; status?: string; created_at?: string };

const CH: Record<string, string> = { whatsapp: "WhatsApp", sms: "SMS", email: "Email", instagram: "Instagram", facebook: "Facebook", telegram: "Telegram", web: "Web" };
const QUICK = ["On our way to diagnose it now.", "It's ready for pickup!", "Your estimate is approved — starting the repair.", "Can you confirm a good time to call?"];
// "Neha Gupta" → "NG" (first letters of the first two words), not the first two letters.
const initialsOf = (n?: string) => (n || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase() || "?";
export const STATUS: Record<ChatStatus, { label: string; cls: string }> = {
  new: { label: "New", cls: "bg-blue-50 text-blue-700 border-blue-200" },
  reply_needed: { label: "Reply needed", cls: "bg-amber-50 text-amber-700 border-amber-200" },
  replied: { label: "Replied", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  completed: { label: "Completed", cls: "bg-slate-100 text-ink-secondary border-slate-200" },
};
export const TABS: [Tab, string][] = [["all", "All"], ["new", "New"], ["reply_needed", "Reply needed"], ["replied", "Replied"], ["completed", "Completed"], ["spam", "Spam"]];
export const threadsUrl = (tab: Tab) => (tab === "spam" ? "/chats/threads?folder=spam" : `/chats/threads?status=${tab}`);
const EMPTY: Record<Tab, string> = {
  all: "No conversations yet.", new: "No new conversations.", reply_needed: "Nobody is waiting on a reply.",
  replied: "No replied conversations.", completed: "No completed conversations.", spam: "Spam is empty.",
};
const fmt = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");

export function StatusBadge({ status }: { status?: ChatStatus }) {
  const s = STATUS[status || "new"];
  return <span className={`px-1.5 py-0.5 rounded border text-[11px] font-bold whitespace-nowrap ${s.cls}`}>{s.label}</span>;
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
  const [toast, setToast] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
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
    setBusy(true);
    try {
      const t = await api.patch<Thread>(`/chats/threads/${sel.id}`, patch);
      flash(done);
      // Spam / restore moves the chat out of this list; a status change may too (filtered tab).
      const leaves = patch.is_spam !== undefined || (tab !== "all" && tab !== "spam" && t.status !== tab);
      if (leaves) { setSel(null); setMsgs([]); await load(tab, ""); } else { setSel({ ...sel, ...t }); await load(tab, sel.id); }
    } catch (e: any) { flash(`Failed: ${e.message}`); }
    finally { setBusy(false); }
  }
  function open(t: Thread) { setSel(t); setConvMenu(false); api.get<{ items: Msg[] }>(`/chats/threads/${t.id}/messages`).then((r) => setMsgs(r.items || [])).catch(() => setMsgs([])); }
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

  return (
    <Shell title="Chats">
      <div className="px-6 pt-3 pb-2 border-b border-slate-200/60 bg-white/40 flex items-center gap-2 flex-shrink-0">
        <div className="flex items-center bg-slate-100/90 p-0.5 rounded-lg border border-slate-200/60 text-xs">
          <button onClick={() => setLayout("split")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1.5 ${layout === "split" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="chat_bubble" c="text-xs" />Conversation</button>
          <button onClick={() => setLayout("list")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1.5 ${layout === "list" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="table_rows" c="text-xs" />List</button>
        </div>
        <select value={channel} onChange={(e) => setChannel(e.target.value)} className="glass-input text-[11px] px-2 py-1 rounded-lg font-semibold ml-auto"><option value="">All channels</option>{Object.entries(CH).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      </div>
      <div role="tablist" aria-label="Chat status" className="px-4 md:px-6 py-2 border-b border-slate-200/60 bg-white/40 flex items-center gap-1.5 overflow-x-auto flex-shrink-0">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => pickTab(k)}
            className={`px-2.5 py-1 rounded-full text-[11px] font-semibold whitespace-nowrap flex items-center gap-1.5 border transition-colors ${tab === k ? "bg-primary text-white border-primary" : "bg-white text-ink-secondary border-slate-200 hover:border-primary/40 hover:text-primary"} ${k === "spam" ? "md:ml-auto" : ""}`}>
            {k === "spam" && <M n="report" c="text-xs" />}{label}
            <span className={`tabular-nums text-[11px] ${tab === k ? "text-white/80" : "text-ink-tertiary"}`}>{counts[k] ?? 0}</span>
          </button>
        ))}
      </div>

      {layout === "list" ? (
        <div className="flex-1 overflow-y-auto p-6">
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <table className="w-full text-left border-collapse text-xs">
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold ">
                <th className="py-3 px-4">Contact</th><th className="py-3 px-4">Status</th><th className="py-3 px-4">Channel</th><th className="py-3 px-4">Last message</th><th className="py-3 px-4">When</th><th className="py-3 px-4">Linked</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                {!threads && <SkeletonRows cols={6} />}
                {loadErr && <tr><td colSpan={6} className="py-6 px-4 text-rose-600">{loadErr} <button onClick={() => load()} className="ml-2 font-semibold text-primary hover:underline">Try again</button></td></tr>}
                {threads && !loadErr && shown.length === 0 && <tr><td colSpan={6} className="py-6 px-4 text-ink-tertiary">{EMPTY[tab]}</td></tr>}
                {shown.map((t) => (
                  <tr key={t.id} onClick={() => { setLayout("split"); open(t); }} className="hover:bg-blue-50/30 cursor-pointer">
                    <td className="py-3 px-4 font-bold text-ink-primary">{nameOf(t)}</td>
                    <td className="py-3 px-4">{t.is_spam ? <span className="text-[11px] font-bold text-rose-600">Spam</span> : <StatusBadge status={t.status} />}</td>
                    <td className="py-3 px-4"><span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-100 text-ink-secondary">{CH[t.channel || "web"] || t.channel}</span></td>
                    <td className="py-3 px-4 text-ink-tertiary truncate max-w-[320px]">{t.last_body || "—"}</td>
                    <td className="py-3 px-4">{fmt(t.last_message_at)}</td>
                    <td className="py-3 px-4">{t.order_id ? <span className="text-primary font-bold">Work order</span> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
      <div className="flex-1 overflow-hidden flex">
        {/* thread list */}
        <aside className={`w-full md:w-80 flex-shrink-0 border-r border-slate-200/80 bg-white/40 flex-col ${sel ? "hidden md:flex" : "flex"}`}>
          <div className="p-3 border-b border-slate-100 flex items-center gap-2">
            <span className="text-[11px] font-bold text-ink-tertiary">Conversations</span>
            <select value={channel} onChange={(e) => setChannel(e.target.value)} className="glass-input ml-auto text-[11px] px-2 py-1 rounded-lg font-semibold">
              <option value="">All channels</option>{Object.entries(CH).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="flex-1 overflow-y-auto">
            {!threads && <div className="p-4 text-xs text-ink-tertiary">Loading…</div>}
            {loadErr && <div className="p-6 text-xs text-rose-600 text-center">{loadErr}<br /><button onClick={() => load()} className="mt-2 font-semibold text-primary hover:underline">Try again</button></div>}
            {threads && !loadErr && shown.length === 0 && <div className="p-6 text-xs text-ink-tertiary text-center">{EMPTY[tab]}</div>}
            {shown.map((t) => (
              <button key={t.id} onClick={() => open(t)} className={`w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-blue-50/40 transition-colors ${sel?.id === t.id ? "bg-blue-50/60" : ""}`}>
                <div className="flex items-center gap-2.5">
                  <div className="w-9 h-9 rounded-full bg-emerald-500 text-white font-bold flex items-center justify-center text-xs flex-shrink-0">{initialsOf(nameOf(t))}</div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2"><span className="text-xs font-bold text-ink-primary truncate">{nameOf(t)}</span><span className="text-[11px] text-ink-tertiary flex-shrink-0">{fmt(t.last_message_at)}</span></div>
                    {!t.is_spam && t.status && t.status !== "completed" && <div className="my-0.5"><StatusBadge status={t.status} /></div>}
                    <div className="flex items-center gap-1.5"><span className="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-ink-secondary font-bold">{CH[t.channel || "web"] || t.channel}</span><span className="text-[11px] text-ink-tertiary truncate">{t.last_body || t.contact_phone}</span></div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </aside>

        {/* conversation */}
        <div className={`flex-1 min-w-0 flex-col ${sel ? "flex" : "hidden md:flex"}`}>
          {!sel ? (
            <div className="flex-1 grid place-items-center text-center"><div><M n="chat" c="text-4xl text-ink-placeholder" /><p className="text-sm font-semibold text-ink-secondary mt-2">Pick a conversation</p></div></div>
          ) : (
            <>
              <div className="px-3 md:px-6 py-3 md:py-4 border-b border-slate-200/80 bg-white/60 flex flex-wrap items-center gap-x-3 gap-y-2">
                <button onClick={() => setSel(null)} aria-label="Back to conversations" className="md:hidden w-9 h-9 -ml-1 rounded-lg flex items-center justify-center text-ink-secondary hover:bg-slate-100 shrink-0"><M n="arrow_back" c="text-base" /></button>
                <div className="w-9 h-9 shrink-0 rounded-full bg-emerald-500 text-white font-bold flex items-center justify-center text-xs">{initialsOf(nameOf(sel))}</div>
                <div className="min-w-0 flex-1"><h3 className="text-sm font-bold text-ink-primary truncate">{nameOf(sel)}</h3><p className="text-[11px] text-emerald-600 font-semibold flex items-center gap-1 whitespace-nowrap truncate"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />{CH[sel.channel || "web"] || sel.channel} · {sel.contact_phone}</p></div>
                <div className="w-full sm:w-auto flex items-center justify-end gap-1.5 flex-shrink-0">
                  {sel.is_spam ? (
                    <span className="text-[11px] font-bold text-rose-600 hidden sm:inline">Spam</span>
                  ) : canManage ? (
                    <select aria-label="Chat status" value={sel.status || "new"} disabled={busy}
                      onChange={(e) => update({ status: e.target.value as ChatStatus }, `Marked as ${STATUS[e.target.value as ChatStatus].label.toLowerCase()}`)}
                      className="glass-input text-[11px] px-2 py-1.5 rounded-lg font-semibold">
                      {(Object.keys(STATUS) as ChatStatus[]).map((k) => <option key={k} value={k}>{STATUS[k].label}</option>)}
                    </select>
                  ) : <StatusBadge status={sel.status} />}
                  {canManage && (sel.is_spam
                    ? <button onClick={() => update({ is_spam: false }, "Moved back to the inbox")} disabled={busy} className="px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-[11px] font-bold text-ink-secondary hover:text-primary hover:border-primary/40 flex items-center gap-1"><M n="undo" c="text-xs" />Not spam</button>
                    : <button onClick={() => update({ is_spam: true }, "Moved to spam")} disabled={busy} aria-label="Mark as spam" title="Mark as spam" className="px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-[11px] font-bold text-ink-secondary hover:text-rose-600 hover:border-rose-200 flex items-center gap-1"><M n="report" c="text-xs" /><span className="hidden sm:inline">Spam</span></button>)}
                {/* Create split button */}
                <div className="relative">
                  {sel.order_id ? (
                    <span className="text-[11px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2.5 py-1.5 rounded-lg flex items-center gap-1"><M n="check_circle" c="text-xs" />Linked to a work order</span>
                  ) : (
                    <>
                      <button onClick={() => setConvMenu((v) => !v)} className="px-3.5 py-2 rounded-lg bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-xs" />Create<M n="expand_more" c="text-xs" /></button>
                      {convMenu && (
                        <div className="absolute top-full right-0 mt-1.5 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 w-48 z-10">
                          <button onClick={() => convert("order")} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs font-semibold text-ink-primary"><M n="handyman" c="text-sm text-primary" />Work order</button>
                          <button onClick={() => convert("estimate")} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs font-semibold text-ink-primary"><M n="request_quote" c="text-sm text-primary" />Estimate</button>
                          <button onClick={() => convert("inquiry")} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs font-semibold text-ink-primary"><M n="contact_support" c="text-sm text-primary" />Inquiry</button>
                        </div>
                      )}
                    </>
                  )}
                </div>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto p-3 md:p-6 space-y-3 bg-canvas">
                {msgs.length === 0 && <p className="text-xs text-ink-tertiary text-center">No messages yet. Outbound is simulated until channel provider keys are connected.</p>}
                {msgs.map((m) => {
                  const out = m.direction === "out" || m.direction === "outbound";
                  return (
                    <div key={m.id} className={`flex flex-col max-w-[85%] md:max-w-[70%] ${out ? "ml-auto items-end" : "items-start"}`}>
                      <div className={`p-2.5 rounded-2xl text-xs ${out ? "bg-primary text-white rounded-tr-none" : "bg-white border border-slate-200 text-ink-primary rounded-tl-none shadow-2xs"}`}>{m.body}</div>
                      <span className="text-[11px] text-slate-400 mt-0.5 tabular-nums">{fmt(m.created_at)}{m.status ? ` · ${humanize(m.status)}` : ""}</span>
                    </div>
                  );
                })}
              </div>
              {/* quick replies */}
              <div className="px-4 pt-2 flex items-center gap-1.5 flex-wrap border-t border-slate-100 bg-white">
                <span className="text-[11px] font-bold text-ink-tertiary flex items-center gap-1"><M n="bolt" c="text-xs text-amber-500" />Quick</span>
                {QUICK.map((q) => <button key={q} onClick={() => send(q)} className="px-2.5 py-1 rounded-full bg-slate-100 hover:bg-blue-50 text-ink-secondary hover:text-primary text-[11px] font-medium">{q.length > 34 ? q.slice(0, 34) + "…" : q}</button>)}
              </div>
              <div className="p-4 pt-2 bg-white flex items-center gap-2">
                <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => { if (e.target.files?.[0]) flash("Image attach needs a connected channel provider"); e.currentTarget.value = ""; }} />
                <button aria-label="Attach image" onClick={() => fileRef.current?.click()} title="Attach image" className="w-10 h-10 flex-shrink-0 rounded-xl bg-slate-100 text-ink-secondary hover:bg-slate-200 flex items-center justify-center"><M n="image" c="text-base" /></button>
                <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} className="glass-input flex-1 min-w-0 py-2.5 px-3 text-xs rounded-xl" placeholder="Message…" />
                <button onClick={() => send()} className="px-4 py-2.5 rounded-xl bg-primary text-white text-xs font-bold hover:brightness-110 flex items-center gap-1.5"><span>Send</span><M n="send" c="text-xs" /></button>
              </div>
            </>
          )}
        </div>
      </div>
      )}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}
