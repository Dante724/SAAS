// 144cq — the customer conversation on a work order: staff client-visible messages merged with the
// customer's replies, in one thread, with a composer. Backend: GET/POST /orders/{id}/thread.
import { useEffect, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";

type Msg = { id: string; from: "staff" | "customer"; who?: string; body: string; at?: string };
const fmt = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");

export default function ConversationPanel({ orderId }: { orderId: string }) {
  const [msgs, setMsgs] = useState<Msg[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const load = () => api.get<{ items: Msg[] }>(`/orders/${orderId}/thread`).then((r) => setMsgs(r.items || [])).catch(() => setMsgs([]));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [orderId]);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [msgs]);

  async function send() {
    const body = text.trim(); if (!body) return;
    setBusy(true);
    try { await api.post(`/orders/${orderId}/thread`, { body }); setText(""); await load(); }
    catch { /* keep text so the user can retry */ }
    finally { setBusy(false); }
  }

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
      <div className="px-4 py-2.5 border-b border-slate-100 flex items-center gap-2">
        <M n="forum" c="text-base text-primary" />
        <span className="text-xs font-bold text-ink-primary">Customer conversation</span>
        <span className="text-[10px] text-ink-tertiary font-semibold ml-auto">Messages here show on the customer's status page</span>
      </div>
      <div className="max-h-64 overflow-y-auto p-3 space-y-2 bg-slate-50/40">
        {!msgs && <div className="text-xs text-ink-tertiary py-6 text-center">Loading…</div>}
        {msgs?.length === 0 && <div className="text-xs text-ink-tertiary py-6 text-center">No messages yet. Say hello — the customer sees it on their status link.</div>}
        {(msgs || []).map((m) => {
          const staff = m.from === "staff";
          return (
            <div key={m.id} className={`flex ${staff ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[78%] rounded-2xl px-3 py-2 ${staff ? "bg-primary text-white rounded-br-sm" : "bg-white border border-slate-200 text-ink-primary rounded-bl-sm"}`}>
                <div className="text-[13px] leading-snug whitespace-pre-wrap break-words">{m.body}</div>
                <div className={`text-[9px] mt-1 ${staff ? "text-white/70" : "text-ink-tertiary"}`}>{m.who || (staff ? "Staff" : "Customer")} · {fmt(m.at)}</div>
              </div>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
      <div className="p-2.5 border-t border-slate-100 flex items-end gap-2">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={1} placeholder="Message the customer…"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          className="glass-input flex-1 py-2 px-3 text-xs rounded-xl resize-none max-h-24" />
        <button onClick={send} disabled={busy || !text.trim()} aria-label="Send message"
          className="shrink-0 w-9 h-9 rounded-xl bg-primary text-white flex items-center justify-center shadow-clay-sm disabled:opacity-40"><M n="send" c="text-base" /></button>
      </div>
    </div>
  );
}
