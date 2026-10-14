import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { useEffect, useRef, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";

type Thread = { id: string; channel?: string; contact_phone?: string; contact_name?: string; client_name?: string; last_body?: string; last_message_at?: string; order_id?: string | null };
type Msg = { id: string; direction?: string; body?: string; status?: string; created_at?: string };

const CH: Record<string, string> = { whatsapp: "WhatsApp", sms: "SMS", email: "Email", instagram: "Instagram", facebook: "Facebook", telegram: "Telegram", web: "Web" };
const QUICK = ["On our way to diagnose it now.", "It's ready for pickup!", "Your estimate is approved — starting the repair.", "Can you confirm a good time to call?"];
const fmt = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "");

export default function ChatsHub() {
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [layout, setLayout] = useState<"split" | "list">("split");
  const [channel, setChannel] = useState("");
  const [sel, setSel] = useState<Thread | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [convMenu, setConvMenu] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2600); };

  function load() { api.get<{ items: Thread[] }>("/chats/threads").then((r) => { setThreads(r.items || []); if (!sel && r.items?.[0]) open(r.items[0]); }).catch(() => setThreads([])); }
  useEffect(() => { load(); }, []);
  function open(t: Thread) { setSel(t); setConvMenu(false); api.get<{ items: Msg[] }>(`/chats/threads/${t.id}/messages`).then((r) => setMsgs(r.items || [])).catch(() => setMsgs([])); }
  async function send(body?: string) {
    const b = (body ?? text).trim(); if (!b || !sel) return;
    try { await api.post(`/chats/threads/${sel.id}/send`, { body: b }); setText(""); open(sel); } catch (e: any) { flash(`Failed: ${e.message}`); }
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

      {layout === "list" ? (
        <div className="flex-1 overflow-y-auto p-6">
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <table className="w-full text-left border-collapse text-xs">
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[10px] font-bold uppercase tracking-wider">
                <th className="py-3 px-4">Contact</th><th className="py-3 px-4">Channel</th><th className="py-3 px-4">Last message</th><th className="py-3 px-4">When</th><th className="py-3 px-4">Linked</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                {!threads && <SkeletonRows cols={5} />}
                {shown.length === 0 && <tr><td colSpan={5} className="py-6 px-4 text-ink-tertiary">No conversations.</td></tr>}
                {shown.map((t) => (
                  <tr key={t.id} onClick={() => { setLayout("split"); open(t); }} className="hover:bg-blue-50/30 cursor-pointer">
                    <td className="py-3 px-4 font-bold text-ink-primary">{nameOf(t)}</td>
                    <td className="py-3 px-4"><span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-ink-secondary">{CH[t.channel || "web"] || t.channel}</span></td>
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
        <aside className="w-80 flex-shrink-0 border-r border-slate-200/80 bg-white/40 flex flex-col">
          <div className="p-3 border-b border-slate-100 flex items-center gap-2">
            <span className="text-[11px] font-bold uppercase tracking-wider text-ink-tertiary">Conversations</span>
            <select value={channel} onChange={(e) => setChannel(e.target.value)} className="glass-input ml-auto text-[11px] px-2 py-1 rounded-lg font-semibold">
              <option value="">All channels</option>{Object.entries(CH).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="flex-1 overflow-y-auto">
            {!threads && <div className="p-4 text-xs text-ink-tertiary">Loading…</div>}
            {shown.length === 0 && <div className="p-6 text-xs text-ink-tertiary text-center">No conversations.</div>}
            {shown.map((t) => (
              <button key={t.id} onClick={() => open(t)} className={`w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-blue-50/40 transition-colors ${sel?.id === t.id ? "bg-blue-50/60" : ""}`}>
                <div className="flex items-center gap-2.5">
                  <div className="w-9 h-9 rounded-full bg-gradient-to-tr from-emerald-500 to-teal-600 text-white font-bold flex items-center justify-center text-xs flex-shrink-0">{nameOf(t).slice(0, 2).toUpperCase()}</div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between"><span className="text-xs font-bold text-ink-primary truncate">{nameOf(t)}</span><span className="text-[10px] text-ink-tertiary flex-shrink-0">{fmt(t.last_message_at)}</span></div>
                    <div className="flex items-center gap-1.5"><span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-100 text-ink-secondary font-bold">{CH[t.channel || "web"] || t.channel}</span><span className="text-[11px] text-ink-tertiary truncate">{t.last_body || t.contact_phone}</span></div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </aside>

        {/* conversation */}
        <div className="flex-1 min-w-0 flex flex-col">
          {!sel ? (
            <div className="flex-1 grid place-items-center text-center"><div><M n="chat" c="text-4xl text-ink-placeholder" /><p className="text-sm font-semibold text-ink-secondary mt-2">Pick a conversation</p></div></div>
          ) : (
            <>
              <div className="px-6 py-4 border-b border-slate-200/80 bg-white/60 flex items-center gap-3">
                <div className="w-9 h-9 rounded-full bg-gradient-to-tr from-emerald-500 to-teal-600 text-white font-bold flex items-center justify-center text-xs">{nameOf(sel).slice(0, 2).toUpperCase()}</div>
                <div><h3 className="text-sm font-bold text-ink-primary">{nameOf(sel)}</h3><p className="text-[11px] text-emerald-600 font-semibold flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />{CH[sel.channel || "web"] || sel.channel} · {sel.contact_phone}</p></div>
                {/* Create split button */}
                <div className="ml-auto relative">
                  {sel.order_id ? (
                    <span className="text-[11px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2.5 py-1.5 rounded-lg flex items-center gap-1"><M n="check_circle" c="text-xs" />Linked to a work order</span>
                  ) : (
                    <>
                      <button onClick={() => setConvMenu((v) => !v)} className="px-3.5 py-2 rounded-lg bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-xs" />Create<M n="expand_more" c="text-xs" /></button>
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
              <div className="flex-1 overflow-y-auto p-6 space-y-3 bg-[#f8faff]">
                {msgs.length === 0 && <p className="text-xs text-ink-tertiary text-center">No messages yet. Outbound is simulated until channel provider keys are connected.</p>}
                {msgs.map((m) => {
                  const out = m.direction === "out" || m.direction === "outbound";
                  return (
                    <div key={m.id} className={`flex flex-col max-w-[70%] ${out ? "ml-auto items-end" : "items-start"}`}>
                      <div className={`p-2.5 rounded-2xl text-xs ${out ? "bg-primary text-white rounded-tr-none" : "bg-white border border-slate-200 text-ink-primary rounded-tl-none shadow-2xs"}`}>{m.body}</div>
                      <span className="text-[9px] text-slate-400 mt-0.5 font-mono">{fmt(m.created_at)}{m.status ? ` · ${humanize(m.status)}` : ""}</span>
                    </div>
                  );
                })}
              </div>
              {/* quick replies */}
              <div className="px-4 pt-2 flex items-center gap-1.5 flex-wrap border-t border-slate-100 bg-white">
                <span className="text-[10px] font-bold uppercase tracking-wider text-ink-tertiary flex items-center gap-1"><M n="bolt" c="text-xs text-amber-500" />Quick</span>
                {QUICK.map((q) => <button key={q} onClick={() => send(q)} className="px-2.5 py-1 rounded-full bg-slate-100 hover:bg-blue-50 text-ink-secondary hover:text-primary text-[11px] font-medium">{q.length > 34 ? q.slice(0, 34) + "…" : q}</button>)}
              </div>
              <div className="p-4 pt-2 bg-white flex items-center gap-2">
                <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => { if (e.target.files?.[0]) flash("Image attach needs a connected channel provider"); e.currentTarget.value = ""; }} />
                <button aria-label="Attach image" onClick={() => fileRef.current?.click()} title="Attach image" className="w-10 h-10 flex-shrink-0 rounded-xl bg-slate-100 text-ink-secondary hover:bg-slate-200 flex items-center justify-center"><M n="image" c="text-base" /></button>
                <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} className="glass-input flex-1 py-2.5 px-3 text-xs rounded-xl" placeholder="Message…" />
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
