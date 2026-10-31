// 144cq — the customer conversation on a work order: staff client-visible messages merged with the
// customer's replies, in one thread, with a composer. Backend: GET/POST /orders/{id}/thread.
// G3: this box and the Activity tab's message box share the same thread, and both show what the
// server says really happened (sent by email/text, or saved but not sent and why).
import { useEffect, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { deliveryText, deliveryTone, type Delivery, type Flash } from "../../lib/delivery";

export type ThreadMsg = { id: string; from: "staff" | "customer"; who?: string; body: string; at?: string };
export type SendChannel = "auto" | "email" | "sms";
export type ThreadSent = ThreadMsg & { delivery?: Delivery };
const fmt = (iso?: string) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");

export const CHANNELS: [SendChannel, string][] = [["auto", "Auto"], ["email", "Email"], ["sms", "Text"]];

/** Load the shared customer thread of a job (both message boxes read this). */
export function useThread(orderId: string, reloadKey?: number) {
  const [msgs, setMsgs] = useState<ThreadMsg[] | null>(null);
  const load = () => api.get<{ items: ThreadMsg[] }>(`/orders/${orderId}/thread`).then((r) => setMsgs(r.items || [])).catch(() => setMsgs([]));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [orderId, reloadKey]);
  return { msgs, load };
}

/** Small Auto / Email / Text picker for how a message goes out. */
export function ChannelPicker({ value, onChange, id }: { value: SendChannel; onChange: (c: SendChannel) => void; id: string }) {
  return (
    <div role="radiogroup" aria-label="Send by" id={id} className="inline-flex gap-0.5 rounded-lg bg-slate-100 p-0.5 shrink-0">
      {CHANNELS.map(([k, l]) => (
        <button key={k} type="button" role="radio" aria-checked={value === k} onClick={() => onChange(k)}
          className={`px-2 py-1 rounded-md text-[11px] font-semibold ${value === k ? "bg-white text-ink-primary shadow-sm" : "text-ink-tertiary hover:text-ink-primary"}`}>{l}</button>
      ))}
    </div>
  );
}

/** One line under a composer saying whether the last message reached the customer. */
export function DeliveryNote({ note }: { note: { text: string; tone?: "warn" | "error" } | null }) {
  if (!note) return null;
  const cls = note.tone === "error" ? "bg-rose-50 text-rose-700 border-rose-200"
    : note.tone === "warn" ? "bg-amber-50 text-amber-800 border-amber-200"
    : "bg-emerald-50 text-emerald-700 border-emerald-200";
  return (
    <div role="status" className={`mx-2.5 mb-2.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium flex items-start gap-1.5 ${cls}`}>
      <M n={note.tone ? "info" : "check_circle"} c="text-sm" /><span>{note.text}</span>
    </div>
  );
}

export default function ConversationPanel({ orderId, onFlash, reloadKey, onSent }: {
  orderId: string; onFlash?: Flash; reloadKey?: number; onSent?: () => void;
}) {
  const { msgs, load } = useThread(orderId, reloadKey);
  const [text, setText] = useState("");
  const [channel, setChannel] = useState<SendChannel>("auto");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone?: "warn" | "error" } | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView?.({ block: "nearest" }); }, [msgs]);

  async function send() {
    const body = text.trim(); if (!body || busy) return;
    setBusy(true);
    try {
      const r = await api.post<ThreadSent>(`/orders/${orderId}/thread`, { body, channel });
      setText("");
      const msg = deliveryText(r?.delivery, "Saved on the job.");
      const tone = deliveryTone(r?.delivery);
      setNote({ text: msg, tone }); onFlash?.(msg, tone);
      await load(); onSent?.();
    } catch (e: any) {
      // keep the text so the user can retry
      const msg = e?.message || "Couldn't send that message";
      setNote({ text: msg, tone: "error" }); onFlash?.(msg, "warn");
    } finally { setBusy(false); }
  }

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
      <div className="px-4 py-2.5 border-b border-slate-100 flex items-center gap-2">
        <M n="forum" c="text-base text-primary" />
        <span className="text-xs font-bold text-ink-primary">Customer conversation</span>
        <span className="text-[11px] text-ink-tertiary font-semibold ml-auto">Messages here show on the customer's status page</span>
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
                <div className={`text-[11px] mt-1 ${staff ? "text-white/70" : "text-ink-tertiary"}`}>{m.who || (staff ? "Staff" : "Customer")} · {fmt(m.at)}</div>
              </div>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
      <div className="p-2.5 border-t border-slate-100 flex items-end gap-2">
        <ChannelPicker id={`conv-ch-${orderId}`} value={channel} onChange={setChannel} />
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={1} placeholder="Message the customer…" aria-label="Message the customer"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          className="glass-input flex-1 py-2 px-3 text-xs rounded-xl resize-none max-h-24" />
        <button onClick={send} disabled={busy || !text.trim()} aria-label="Send message"
          className="shrink-0 w-9 h-9 rounded-xl bg-primary text-white flex items-center justify-center shadow-clay-sm disabled:opacity-40"><M n="send" c="text-base" /></button>
      </div>
      <DeliveryNote note={note} />
    </div>
  );
}
