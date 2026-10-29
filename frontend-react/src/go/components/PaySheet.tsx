// Take payment on a job. Online only and never queued: money must fail closed. Every attempt carries
// an Idempotency-Key, so a retried tap can't post twice; the amount can go down (part payment) but
// not above what's owed. Cash shows change due; card records a terminal charge; a pay link shows a
// QR the customer scans, and we watch for the balance to reach zero.
import { useCallback, useEffect, useRef, useState } from "react";
import { Banknote, CheckCircle2, CreditCard, Loader2, QrCode, Share2 } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { PrimaryButton, Sheet } from "./ui";
import { changeDue, clampAmount, money, quickTender } from "../lib/pay";
import { newKey } from "../lib/photos";
import { haptic } from "../lib/scanner";
import { useToast } from "../lib/useGo";
import { useLock } from "./Lock";
import AccountPicker, { type PayAccount, type PaymentOption, accountsFor, pickDefaultAccount } from "../../components/AccountPicker";

type Ctx = { remaining: number; order_total: number; already_paid: number; location_id?: string | null; accounts_by_method: Record<string, PayAccount[]> };
// Card covers card readers and cashless (bank / UPI) accounts on the phone.
const OPTIONS: Record<"cash" | "card", PaymentOption[]> = { cash: ["cash"], card: ["card", "cashless"] };
const allAccounts = (c: Ctx | null): PayAccount[] =>
  c ? Object.entries(c.accounts_by_method || {}).flatMap(([k, list]) => (list || []).map((a) => ({ ...a, payment_option: a.payment_option || k }))) : [];
type Method = "cash" | "card" | "link";

export default function PaySheet({ open, onClose, orderId, orderNumber, customer, onPaid }: {
  open: boolean; onClose: () => void; orderId: string; orderNumber: string; customer?: string | null;
  onPaid: (remaining: number) => void;
}) {
  const { me, can } = useMe();
  const fmt = (n?: number | null) => money(me?.currency, n);
  const toast = useToast();
  const { confirmMoney } = useLock();
  const [ctx, setCtx] = useState<Ctx | null>(null);
  const [method, setMethod] = useState<Method | null>(null);
  const [amountRaw, setAmountRaw] = useState("");
  const [tendered, setTendered] = useState<number | null>(null);
  const [accountId, setAccountId] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<{ url: string; qr_svg: string } | null>(null);
  const key = useRef(newKey());
  const mayLink = can("can_create_payment_links");

  const loadCtx = useCallback(async () => {
    const c = await api.get<Ctx>(`/cash/orders/${encodeURIComponent(orderId)}/payment-context`);
    setCtx(c);
    return c;
  }, [orderId]);

  useEffect(() => {
    if (!open) return;
    setCtx(null); setMethod(null); setConfirming(false); setLink(null); setTendered(null); key.current = newKey();
    loadCtx().then((c) => setAmountRaw(String(c.remaining))).catch((e) => toast({ title: "Couldn't load the balance", body: e?.message, tone: "error" }));
  }, [open, loadCtx, toast]);

  // watch a pay link: the customer pays on their phone, the balance drops to zero
  useEffect(() => {
    if (!open || method !== "link" || !link) return;
    const t = setInterval(async () => {
      try {
        const c = await loadCtx();
        if (c.remaining <= 0.009) { clearInterval(t); haptic(); toast({ title: "Paid by card online" }); onPaid(0); onClose(); }
      } catch { /* keep watching */ }
    }, 4000);
    return () => clearInterval(t);
  }, [open, method, link, loadCtx, onPaid, onClose, toast]);

  const remaining = ctx?.remaining ?? 0;
  const amount = clampAmount(amountRaw, remaining);
  const all = allAccounts(ctx);
  const accounts = method === "cash" || method === "card" ? accountsFor(all, OPTIONS[method]) : [];
  // Preselect the cashbox at the job's location (else a company-wide one) so the button is ready.
  useEffect(() => { setAccountId(pickDefaultAccount(accounts, ctx?.location_id)); setConfirming(false); key.current = newKey(); }, [method]);
  const addAccount = (a: PayAccount) => setCtx((c) => c && ({ ...c, accounts_by_method: { ...c.accounts_by_method, [a.payment_option || "cash"]: [...(c.accounts_by_method[a.payment_option || "cash"] || []), a] } }));
  useEffect(() => { setConfirming(false); key.current = newKey(); }, [amountRaw]);

  async function pay() {
    if (!amount || !method || method === "link" || busy) return;
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Payments need a connection.", tone: "warn" }); return; }
    if (!accountId) { toast({ title: "Pick where the money goes", tone: "warn" }); return; }
    if (!confirming) { setConfirming(true); return; }
    if (!(await confirmMoney(`Take ${fmt(amount)}`))) return;
    setBusy(true);
    try {
      await api.postOnce(`/cash/orders/${encodeURIComponent(orderId)}/pay`, { method: method === "card" ? (ctx?.accounts_by_method.card?.some((a) => a.id === accountId) ? "card" : "cashless") : "cash", account_id: accountId, amount }, key.current);
      haptic();
      const left = Math.max(0, Math.round((remaining - amount) * 100) / 100);
      toast({ title: `${fmt(amount)} taken`, body: left > 0 ? `${fmt(left)} still to pay` : undefined });
      key.current = newKey();
      onPaid(left);
      if (left > 0) { setConfirming(false); await loadCtx().then((c) => setAmountRaw(String(c.remaining))); }
      else onClose();
    } catch (e: any) {
      toast({ title: "Payment not taken", body: e?.message, tone: "error" });
      setConfirming(false);
    } finally { setBusy(false); }
  }

  async function makeLink() {
    if (busy) return;
    if (!(await confirmMoney("Make a pay link"))) { setMethod(null); return; }
    setBusy(true);
    try {
      const r = await api.post<{ url: string; qr_svg: string }>(`/orders/${encodeURIComponent(orderId)}/payment-link`);
      setLink(r);
    } catch (e: any) { toast({ title: "Couldn't make a pay link", body: e?.message, tone: "error" }); }
    finally { setBusy(false); }
  }

  const tile = (on: boolean) => `min-h-[4.5rem] rounded-xl border flex flex-col items-center justify-center gap-1 text-go-body font-semibold ${on ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`;

  return (
    <Sheet open={open} onClose={onClose} title={`Payment · ${orderNumber}`}>
      <div className="px-2">
        {!ctx ? <div className="py-10 flex justify-center text-ink-tertiary"><Loader2 className="animate-spin" /></div> : remaining <= 0.009 ? (
          <div className="py-8 text-center">
            <CheckCircle2 size={40} className="mx-auto text-emerald-600" />
            <p className="mt-2 font-semibold text-ink-primary">Nothing to pay</p>
            <p className="text-ink-secondary">{fmt(ctx.already_paid)} already paid.</p>
          </div>
        ) : (
          <>
            <div className="text-center">
              <p className="text-go-sub font-semibold text-ink-tertiary">{customer || "Customer"} pays</p>
              <input value={amountRaw} onChange={(e) => setAmountRaw(e.target.value)} inputMode="decimal" aria-label="Amount"
                className="mt-1 w-full bg-transparent text-center text-go-display font-bold tabular-nums tracking-tight text-ink-primary focus:outline-none" />
              <p className="text-go-sub text-ink-secondary">{amount && amount < remaining ? `Part payment · ${fmt(remaining)} owed` : `Balance due ${fmt(remaining)}`}</p>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-2">
              <button onClick={() => setMethod("cash")} className={tile(method === "cash")}><Banknote size={22} />Cash</button>
              <button onClick={() => setMethod("card")} className={tile(method === "card")}><CreditCard size={22} />Card</button>
              {mayLink && <button onClick={() => { setMethod("link"); if (!link) makeLink(); }} className={tile(method === "link")}><QrCode size={22} />Pay link</button>}
            </div>

            {method === "cash" && amount && (
              <div className="mt-4">
                <p className="mb-2 text-go-sub font-semibold text-ink-tertiary">Customer handed you</p>
                <div className="flex flex-wrap gap-2">
                  {quickTender(amount).map((v) => (
                    <button key={v} onClick={() => setTendered(v)} className={`min-h-[2.75rem] px-4 rounded-full border font-semibold tabular-nums ${tendered === v ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`}>{fmt(v)}</button>
                  ))}
                </div>
                {tendered != null && tendered > amount && <p className="mt-3 text-center text-go-title font-bold text-emerald-700">Change {fmt(changeDue(tendered, amount))}</p>}
              </div>
            )}

            {(method === "cash" || method === "card") && accounts.length !== 1 && (
              <div className="mt-4">
                {accounts.length > 1 && <p className="mb-2 text-go-sub font-semibold text-ink-tertiary">{method === "cash" ? "Cashbox" : "Card account"}</p>}
                <AccountPicker variant="pills" accounts={all} paymentOption={OPTIONS[method]} locationId={ctx?.location_id}
                  value={accountId} onChange={setAccountId} onCreated={addAccount} />
              </div>
            )}

            {method === "link" && (
              <div className="mt-4 text-center">
                {!link ? <Loader2 className="mx-auto animate-spin text-ink-tertiary" /> : (
                  <>
                    <img src={link.qr_svg} alt="Pay link QR code" className="mx-auto h-56 w-56 rounded-xl bg-white p-3" />
                    <p className="mt-2 text-go-sub text-ink-secondary inline-flex items-center gap-1.5"><Loader2 size={14} className="animate-spin" />Waiting for the customer to pay…</p>
                    {"share" in navigator && (
                      <button onClick={() => navigator.share({ title: `Pay for ${orderNumber}`, url: link.url }).catch(() => {})}
                        className="mt-3 min-h-[2.75rem] px-5 rounded-xl border border-slate-200 bg-white font-semibold text-ink-primary inline-flex items-center gap-2"><Share2 size={18} />Send the link</button>
                    )}
                  </>
                )}
              </div>
            )}

            {(method === "cash" || method === "card") && (
              <div className="mt-5">
                {confirming && (
                  <p className="mb-2 rounded-xl bg-amber-50 border border-amber-200 px-3 py-2 text-go-sub text-amber-800">
                    Record {amount ? fmt(amount) : ""} {method === "cash" ? "cash" : "by card"} from {customer || "the customer"}, taken by {me?.name || "you"}?
                  </p>
                )}
                <PrimaryButton onClick={pay} disabled={!amount || busy || !accountId}>
                  {busy ? "Taking payment…" : confirming ? `Confirm ${amount ? fmt(amount) : ""}` : method === "cash" ? `Take ${amount ? fmt(amount) : ""} cash` : `Charged ${amount ? fmt(amount) : ""} on the terminal`}
                </PrimaryButton>
              </div>
            )}
          </>
        )}
      </div>
    </Sheet>
  );
}
