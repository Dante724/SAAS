import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { M } from "./Shell";
import { api } from "../api/client";
import { money } from "../pages/orders/types";
import { inputCls } from "../pages/invoices/billing";

// F2/F3 — "Charge on reader". Sends the amount to a registered card reader (Stripe Terminal,
// Square Terminal, or the simulated reader in local testing), then follows the charge by polling
// until it is approved, declined or canceled. The server records the payment exactly once.

export type Reader = { id: string; provider: string; label: string; device_type?: string | null; test_mode: boolean };
export type Charge = {
  id: string; terminal_id: string; provider: string; amount: number; status: string; status_label: string;
  error?: string | null; order_id?: string | null; sale_id?: string | null; test_mode?: boolean | null;
};

const LAST_READER = "pos_last_reader";
export const POLL_MS = 2000;
const PROVIDER: Record<string, string> = { stripe: "Stripe", square: "Square", simulated: "Simulated" };

const errText = (e: any): string => e?.message || "Something went wrong";   // ApiError is already readable

export default function ReaderCharge({ orderId, saleId, amount, accountId, title, onApproved, onClose }: {
  orderId?: string; saleId?: string; amount: number; accountId?: string; title?: string;
  onApproved: (c: Charge) => void; onClose: (c: Charge | null) => void;
}) {
  const [readers, setReaders] = useState<Reader[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [readerId, setReaderId] = useState("");
  const [charge, setCharge] = useState<Charge | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyReader, setBusyReader] = useState(false);   // the reader still holds another charge
  const [pollErr, setPollErr] = useState<string | null>(null);   // status checks are failing
  const busyRef = useRef(false);
  const done = useRef(false);

  useEffect(() => {
    api.get<{ items: Reader[] }>("/payments/terminal/readers").then((r) => {
      const items = r.items || [];
      setReaders(items);
      let last = "";
      try { last = localStorage.getItem(LAST_READER) || ""; } catch { /* ignore */ }
      setReaderId((items.find((x) => x.id === last) || items[0])?.id || "");
    }).catch((e) => setLoadErr(errText(e)));
  }, []);

  // Live status: poll while the reader waits for the card.
  useEffect(() => {
    if (!charge || charge.status !== "pending") return;
    const t = setTimeout(() => {
      api.get<Charge>(`/payments/terminal/charges/${charge.id}`)
        .then((c) => { setPollErr(null); setCharge(c); })
        .catch((e) => { setPollErr(errText(e)); setCharge({ ...charge }); });   // keep trying
    }, POLL_MS);
    return () => clearTimeout(t);
  }, [charge]);

  useEffect(() => {
    if (charge?.status === "succeeded" && !done.current) { done.current = true; onApproved(charge); }
  }, [charge, onApproved]);

  // While the reader waits the dialog stays open (closing would hide a payment in progress), unless
  // something is wrong (status checks or Cancel failing): then the cashier can always get out.
  const locked = charge?.status === "pending" && !err && !pollErr;
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape" && !locked) onClose(charge); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [charge, onClose, locked]);

  const reader = readers?.find((r) => r.id === readerId) || null;

  async function send() {
    if (busyRef.current || !readerId) return;
    busyRef.current = true; setBusy(true); setErr(null); setBusyReader(false);
    try {
      const body: Record<string, unknown> = { terminal_id: readerId, account_id: accountId || null };
      if (orderId) { body.order_id = orderId; body.amount = amount; } else body.sale_id = saleId;
      const c = await api.post<Charge>("/payments/terminal/charge", body);
      try { localStorage.setItem(LAST_READER, readerId); } catch { /* ignore */ }
      setCharge(c);
    } catch (e: any) {
      setErr(errText(e));
      setBusyReader(e?.status === 409 && /waiting/i.test(errText(e)));
    } finally { busyRef.current = false; setBusy(false); }
  }
  async function act(path: string, body?: unknown) {
    if (!charge || busyRef.current) return;
    busyRef.current = true; setBusy(true); setErr(null);
    try { setCharge(await api.post<Charge>(`/payments/terminal/charges/${charge.id}/${path}`, body ?? {})); }
    catch (e: any) { setErr(errText(e)); } finally { busyRef.current = false; setBusy(false); }
  }
  async function clearReader() {
    setBusy(true);
    try { await api.post(`/payments/terminal/readers/${readerId}/clear`, {}); setErr(null); setBusyReader(false); }
    catch (e: any) { setErr(errText(e)); } finally { setBusy(false); }
  }

  const st = charge?.status;
  const waiting = st === "pending";
  const failed = st === "declined" || st === "canceled" || st === "failed";
  const testMode = charge ? charge.test_mode !== false : reader?.test_mode !== false;
  const canSimulate = waiting && testMode && charge?.provider !== "square";

  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Charge on reader">
      <div className="absolute inset-0 bg-ink-primary/40" onClick={() => { if (!locked) onClose(charge); }} />
      <div className="relative w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-5 shadow-clay-drawer space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-ink-primary flex items-center gap-2">{title || "Charge on reader"}
              {testMode && <span className="px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200/70 text-[11px] font-semibold">Test mode</span>}
            </div>
            <div className="text-2xl font-semibold text-ink-primary tabular-nums mt-1">{money(amount)}</div>
          </div>
          <button aria-label="Close" disabled={locked} onClick={() => onClose(charge)} className="w-9 h-9 -mr-2 -mt-2 rounded-lg grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100 disabled:opacity-30 disabled:hover:bg-transparent"><M n="close" c="text-lg" /></button>
        </div>

        {!readers && !loadErr && <div className="text-xs text-ink-tertiary py-4 text-center">Loading card readers…</div>}
        {loadErr && <div className="text-xs text-rose-600 py-2">{loadErr}</div>}
        {readers && readers.length === 0 && (
          <div className="rounded-xl border border-dashed border-slate-300 p-4 text-center text-xs text-ink-secondary">
            <M n="contactless" c="text-xl text-ink-tertiary block mx-auto mb-1.5" />No card reader yet. Add one in Settings › Integrations › Card readers.
          </div>
        )}

        {readers && readers.length > 0 && !charge && (
          <>
            <div>
              <label htmlFor="reader-pick" className="block text-xs font-semibold text-ink-tertiary mb-1">Card reader</label>
              <select id="reader-pick" value={readerId} onChange={(e) => setReaderId(e.target.value)} className={inputCls}>
                {readers.map((r) => <option key={r.id} value={r.id}>{r.label} · {PROVIDER[r.provider] || r.provider}</option>)}
              </select>
            </div>
            <button onClick={send} disabled={busy || !readerId || amount <= 0} className="w-full h-11 rounded-xl bg-primary text-white text-sm font-semibold hover:bg-primary-container disabled:opacity-50 flex items-center justify-center gap-2 tabular-nums">
              <M n="contactless" c="text-base" />{busy ? "Sending…" : `Send ${money(amount)} to reader`}
            </button>
          </>
        )}

        {charge && (
          <div role="status" aria-live="polite" className={`rounded-xl border px-4 py-3.5 flex items-start gap-3 ${waiting ? "border-slate-200 bg-slate-50" : st === "succeeded" ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50"}`}>
            {waiting
              ? <span data-testid="reader-spinner" aria-hidden className="mt-0.5 w-4 h-4 flex-shrink-0 rounded-full border-2 border-slate-300 border-t-primary animate-spin motion-reduce:animate-none" />
              : <M n={st === "succeeded" ? "check_circle" : st === "canceled" ? "block" : "error"} c={`text-base flex-shrink-0 mt-0.5 ${st === "succeeded" ? "text-emerald-600" : "text-rose-600"}`} />}
            <div className="min-w-0 text-left">
            <div className="text-sm font-semibold text-ink-primary">{waiting ? "Waiting for card…" : charge.status_label}</div>
            <div className="text-xs text-ink-secondary mt-0.5">
              {waiting ? `Ask the customer to tap, insert or swipe on ${reader?.label || "the reader"}.` : charge.error || (st === "succeeded" ? "Payment recorded." : "")}
            </div>
            </div>
          </div>
        )}

        {pollErr && waiting && !err && (
          <div className="text-xs text-amber-800 flex items-start gap-1.5"><M n="error" c="text-sm" /><span className="flex-1">Can't check the reader right now ({pollErr}). Still trying. If you close this, check the payment before charging again.</span></div>
        )}
        {err && (
          <div className="text-xs text-rose-600 flex items-start gap-1.5"><M n="error" c="text-sm" /><span className="flex-1">{err}</span>
            {busyReader && <button onClick={clearReader} disabled={busy} className="font-semibold text-primary hover:underline whitespace-nowrap">Cancel the waiting charge</button>}
          </div>
        )}

        {canSimulate && (
          <div className="rounded-xl border border-amber-200/70 bg-amber-50/60 p-3 space-y-2">
            <div className="text-xs font-semibold text-amber-900">Test card <span className="font-normal text-amber-800">· nothing is charged</span></div>
            <div className="flex gap-2">
              <button onClick={() => act("simulate", { outcome: "approve" })} disabled={busy} className="flex-1 h-9 rounded-lg bg-white border border-slate-200 text-xs font-semibold text-emerald-700 hover:bg-emerald-50 disabled:opacity-50">Approve</button>
              <button onClick={() => act("simulate", { outcome: "decline" })} disabled={busy} className="flex-1 h-9 rounded-lg bg-white border border-slate-200 text-xs font-semibold text-rose-700 hover:bg-rose-50 disabled:opacity-50">Decline</button>
            </div>
          </div>
        )}

        <div className="flex gap-2">
          {waiting && <button onClick={() => act("cancel")} disabled={busy} className="flex-1 h-10 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-rose-600 hover:bg-rose-50 disabled:opacity-50">Cancel charge</button>}
          {failed && <button onClick={() => { setCharge(null); setErr(null); }} className="flex-1 h-10 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container">Try again</button>}
          {!locked && <button onClick={() => onClose(charge)} className={`flex-1 h-10 rounded-lg text-xs font-semibold ${st === "succeeded" ? "bg-primary text-white hover:bg-primary-container" : "border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50"}`}>{st === "succeeded" ? "Done" : "Close"}</button>}
        </div>
      </div>
    </div>, document.body);
}
