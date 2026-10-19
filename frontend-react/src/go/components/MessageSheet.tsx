// Talk to the customer about this job.
//  Chat: the job's conversation (the customer's tracking-page messages and the shop's replies).
//        A reply here shows on their tracking page.
//  Text / WhatsApp: pick a saved template (filled in by the server for this job), edit it, and
//        open it in the phone's own Messages or WhatsApp app; one tap on Send there. Back in the
//        app we ask "Sent?" and only a "yes" goes into the job history and the portal thread.
import { useCallback, useEffect, useRef, useState } from "react";
import { MessageCircle, MessageSquareText, Send } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { PrimaryButton, Sheet } from "./ui";
import { goQueue } from "../lib/offlineQueue";
import { smsLink, smsSegments, waLink } from "../lib/messaging";
import { utc } from "../lib/time";
import { useToast } from "../lib/useGo";

type Msg = { id: string; from: "staff" | "customer"; who?: string; body: string; at: string };
type Tpl = { id: string; name: string; channel: string; body: string };
type Compose = {
  client: { id: string; name: string; phone?: string | null } | null;
  to_e164: string | null;
  consent: { sms: boolean; whatsapp: boolean | null };
  templates: Tpl[];
};

export default function MessageSheet({ open, onClose, orderId, onRead }: {
  open: boolean; onClose: () => void; orderId: string; onRead: () => void;
}) {
  const { can } = useMe();
  const toast = useToast();
  const mayText = can("can_send_sms");
  const [tab, setTab] = useState<"chat" | "text">("chat");
  const [thread, setThread] = useState<Msg[] | null>(null);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [ctx, setCtx] = useState<Compose | null>(null);
  const [tplId, setTplId] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [asked, setAsked] = useState<null | { id: Promise<string | null>; channel: string }>(null);
  const [confirmSent, setConfirmSent] = useState(false);
  const left = useRef(false);
  const end = useRef<HTMLDivElement>(null);
  const onReadRef = useRef(onRead);    // the parent passes a fresh function each render
  onReadRef.current = onRead;

  const loadThread = useCallback(async () => {
    try {
      const r = await api.get<{ items: Msg[] }>(`/orders/${encodeURIComponent(orderId)}/thread`);
      setThread(r.items || []);
      onReadRef.current();
    } catch { setThread([]); }
  }, [orderId]);

  useEffect(() => {
    if (!open) return;
    setThread(null); setReply(""); setConfirmSent(false); setAsked(null);
    loadThread();
    if (mayText) {
      api.get<Compose>(`/mobile/orders/${encodeURIComponent(orderId)}/compose`).then((c) => {
        setCtx(c);
        const first = c.templates[0];
        setTplId(first?.id ?? null);
        setBody(first?.body ?? "");
      }).catch(() => setCtx(null));
    }
  }, [open, orderId, mayText, loadThread]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [thread]);

  // Back from the Messages / WhatsApp app: ask whether it was sent.
  useEffect(() => {
    if (!asked) return;
    const on = () => {
      if (document.visibilityState === "hidden") left.current = true;
      else if (left.current) { left.current = false; setConfirmSent(true); }
    };
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, [asked]);

  async function sendReply() {
    const text = reply.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const m = await api.post<Msg>(`/orders/${encodeURIComponent(orderId)}/thread`, { body: text });
      setThread((t) => [...(t || []), m]);
      setReply("");
    } catch (e: any) {
      toast({ title: "Reply not sent", body: navigator.onLine ? e?.message : "You're offline.", tone: "error" });
    } finally { setSending(false); }
  }

  /** Open the phone's app straight from the tap (browsers block opening it after an await). */
  function openIn(channel: "sms" | "whatsapp") {
    if (!ctx?.client?.phone || !body.trim()) return;
    const link = channel === "sms" ? smsLink(ctx.to_e164 || ctx.client.phone, body) : waLink(ctx.to_e164 || "", body);
    const id = api.post<{ id: string }>("/mobile/messages", { order_id: orderId, channel, body, template_id: tplId })
      .then((r) => r.id).catch((e) => { toast({ title: "Couldn't record the message", body: e?.message, tone: "warn" }); return null; });
    setAsked({ id, channel });
    left.current = false;
    window.location.href = link;
  }

  async function markSent(sent: boolean) {
    const a = asked;
    setConfirmSent(false); setAsked(null);
    const id = await a?.id;
    if (!id) return;
    // setting a value: safe to queue if the phone is offline right now
    await goQueue().mutate("PATCH", `/api/mobile/messages/${encodeURIComponent(id)}`, { state: sent ? "sent" : "cancelled" });
    if (sent) { toast({ title: "Added to the job history" }); loadThread(); }
  }

  async function setWhatsApp(granted: boolean) {
    if (!ctx?.client) return;
    try {
      const r = await api.post<{ consent: Compose["consent"] }>(`/mobile/clients/${encodeURIComponent(ctx.client.id)}/whatsapp-consent`, { granted });
      setCtx({ ...ctx, consent: r.consent });
    } catch (e: any) { toast({ title: "Couldn't save that", body: e?.message, tone: "error" }); }
  }

  const tabBtn = (t: typeof tab, label: string, Icon: typeof Send) => (
    <button onClick={() => setTab(t)} aria-pressed={tab === t}
      className={`min-h-[2.75rem] rounded-xl text-[0.9375rem] font-semibold inline-flex items-center justify-center gap-1.5 ${tab === t ? "bg-white text-ink-primary shadow-clay-sm" : "text-ink-secondary"}`}>
      <Icon size={16} />{label}
    </button>
  );
  const phone = ctx?.client?.phone;
  const consent = ctx?.consent;
  const segs = smsSegments(body);

  return (
    <Sheet open={open} onClose={onClose} title="Message">
      <div className="px-2">
        {mayText && <div className="mb-3 grid grid-cols-2 rounded-2xl bg-slate-100 p-1">{tabBtn("chat", "Chat", MessageCircle)}{tabBtn("text", "Text / WhatsApp", MessageSquareText)}</div>}

        {confirmSent ? (
          <div className="py-6 text-center">
            <p className="text-[1.0625rem] font-semibold text-ink-primary">Did the {asked?.channel === "whatsapp" ? "WhatsApp" : "text"} go?</p>
            <p className="mt-1 text-[0.875rem] text-ink-secondary">If yes, it's added to the job and the customer's tracking page.</p>
            <div className="mt-4 flex gap-2">
              <button onClick={() => markSent(false)} className="min-h-[3.25rem] flex-1 rounded-2xl border border-slate-200 bg-white font-semibold text-ink-primary">Not sent</button>
              <PrimaryButton onClick={() => markSent(true)} className="flex-1">Sent</PrimaryButton>
            </div>
          </div>
        ) : tab === "chat" ? (
          <>
            <div className="max-h-[45vh] min-h-[8rem] overflow-y-auto space-y-2 py-1">
              {thread === null && <p className="py-6 text-center text-ink-tertiary">Loading…</p>}
              {thread?.length === 0 && <p className="py-6 text-center text-ink-secondary">No messages yet. Customers can write from their tracking page.</p>}
              {thread?.map((m) => (
                <div key={m.id} className={`flex ${m.from === "staff" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[80%] rounded-2xl px-3.5 py-2 ${m.from === "staff" ? "bg-primary text-white rounded-br-md" : "bg-slate-100 text-ink-primary rounded-bl-md"}`}>
                    <p className="whitespace-pre-wrap text-[0.9375rem]">{m.body}</p>
                    <p className={`mt-0.5 text-[0.6875rem] ${m.from === "staff" ? "text-white/75" : "text-ink-tertiary"}`}>
                      {m.who ? `${m.who} · ` : ""}{utc(m.at)?.toLocaleString([], { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}
                    </p>
                  </div>
                </div>
              ))}
              <div ref={end} />
            </div>
            <div className="mt-2 flex items-end gap-2">
              <textarea value={reply} onChange={(e) => setReply(e.target.value)} rows={1} placeholder="Reply on their tracking page"
                className="flex-1 min-h-[3rem] max-h-32 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-[1rem] text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary resize-none" />
              <button onClick={sendReply} disabled={!reply.trim() || sending} aria-label="Send reply"
                className="h-12 w-12 shrink-0 rounded-full bg-primary text-white inline-flex items-center justify-center disabled:opacity-40"><Send size={20} /></button>
            </div>
          </>
        ) : (
          <>
            {!ctx ? <p className="py-6 text-center text-ink-tertiary">Loading…</p> : !phone ? (
              <p className="py-6 text-center text-ink-secondary">This customer has no phone number.</p>
            ) : (
              <>
                {ctx.templates.length > 0 && (
                  <div className="mb-2 flex gap-2 overflow-x-auto pb-1">
                    {ctx.templates.map((t) => (
                      <button key={t.id} onClick={() => { setTplId(t.id); setBody(t.body); }}
                        className={`min-h-[2.5rem] shrink-0 px-3.5 rounded-full border text-[0.875rem] font-medium ${tplId === t.id ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`}>{t.name}</button>
                    ))}
                  </div>
                )}
                <textarea value={body} onChange={(e) => { setBody(e.target.value); }} rows={5} placeholder="Write a message"
                  className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-[1rem] text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary resize-none" />
                <p className="mt-1 px-1 text-[0.8125rem] text-ink-tertiary">To {ctx.client?.name} · {ctx.to_e164 || phone}{segs > 1 ? ` · ${segs} texts` : ""}</p>
                <div className="mt-3 space-y-2">
                  {consent?.sms
                    ? <PrimaryButton onClick={() => openIn("sms")} disabled={!body.trim()}>Open in Messages</PrimaryButton>
                    : <p className="rounded-xl bg-amber-50 border border-amber-200 px-3 py-2 text-[0.875rem] text-amber-800">This customer asked not to be texted.</p>}
                  {consent?.whatsapp === true && ctx.to_e164 && (
                    <button onClick={() => openIn("whatsapp")} disabled={!body.trim()}
                      className="w-full min-h-[3.25rem] rounded-2xl bg-[#1fa855] font-semibold text-white disabled:opacity-50">Open in WhatsApp</button>
                  )}
                  {consent?.whatsapp === true && !ctx.to_e164 && <p className="text-[0.8125rem] text-ink-secondary">WhatsApp needs the number with its country code. Edit the customer's phone in Tekly.</p>}
                  {consent?.whatsapp == null && (
                    <div className="rounded-2xl border border-slate-200 bg-white p-3">
                      <p className="text-[0.9375rem] text-ink-primary">Is WhatsApp OK with {ctx.client?.name?.split(" ")[0] || "the customer"}? Ask them, then tap.</p>
                      <div className="mt-2 flex gap-2">
                        <button onClick={() => setWhatsApp(false)} className="min-h-[2.75rem] flex-1 rounded-xl border border-slate-200 font-semibold text-ink-primary">No</button>
                        <button onClick={() => setWhatsApp(true)} className="min-h-[2.75rem] flex-1 rounded-xl bg-[#1fa855] font-semibold text-white">Yes, WhatsApp is OK</button>
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </Sheet>
  );
}
