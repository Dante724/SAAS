/* AccountPicker — the one "which cashbox does this money go into?" control for every pay dialog.
   Used by the job payment / prepayment dialog (WODrawer), Record payment on an invoice
   (RecordPaymentDrawer) and the mobile PaySheet. Exported on purpose so the POS checkout can adopt
   it too: pass every account you know about, the payment option(s) that fit the chosen method and
   the sale's location, and it keeps `value` on a sensible account.

   - Preselects automatically (see pickDefaultAccount): an account at this location, else a
     company-wide one, else the first. It never sits on an empty value while an account exists.
   - With no account for the method it shows a small "Set up a cashbox" form that creates one
     (POST /cash/accounts) and selects it. Without the permission it says who can set one up. */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useMe } from "../context/Me";

export type PayAccount = { id: string; name?: string; payment_option?: string; location_id?: string | null; is_company?: boolean };
export type PaymentOption = "cash" | "card" | "cashless";

/** Accounts that take this payment option (or any of several, e.g. card + cashless on mobile). */
export function accountsFor(accounts: PayAccount[], option: PaymentOption | PaymentOption[]): PayAccount[] {
  const opts = Array.isArray(option) ? option : [option];
  return (accounts || []).filter((a) => opts.includes((a.payment_option || "") as PaymentOption));
}

/** The account to preselect: one at this location, else a company-wide one (no location), else the
 *  first. Returns "" only when there are no accounts at all. */
export function pickDefaultAccount(accounts: PayAccount[], locationId?: string | null): string {
  if (!accounts?.length) return "";
  const here = locationId ? accounts.find((a) => a.location_id === locationId) : undefined;
  const companyWide = accounts.find((a) => !a.location_id);
  return (here || companyWide || accounts[0]).id;
}

const DEFAULT_NAME: Record<PaymentOption, string> = { cash: "Cash drawer", card: "Card", cashless: "Bank transfer" };
const KIND: Record<PaymentOption, string> = { cash: "cash drawer", card: "card account", cashless: "bank account" };
const ICON: Record<PaymentOption, string> = { cash: "payments", card: "credit_card", cashless: "account_balance" };

export type AccountPickerProps = {
  /** Every account the dialog knows about; the picker shows only those matching `paymentOption`. */
  accounts: PayAccount[];
  /** Which account kind(s) fit the chosen method. The first one is used when creating a cashbox. */
  paymentOption: PaymentOption | PaymentOption[];
  /** The job's / invoice's / sale's location, used to preselect and to place a new cashbox. */
  locationId?: string | null;
  value: string;
  onChange: (accountId: string) => void;
  /** Called with a cashbox created from the inline form, so the caller can add it to its list. */
  onCreated?: (account: PayAccount) => void;
  /** id for the <select>, so a caller's <label htmlFor> points at it. */
  id?: string;
  /** "select" (desktop dialogs) or "pills" (touch screens). */
  variant?: "select" | "pills";
  selectClassName?: string;
};

export default function AccountPicker({ accounts, paymentOption, locationId, value, onChange, onCreated, id, variant = "select", selectClassName }: AccountPickerProps) {
  const fitting = useMemo(() => accountsFor(accounts, paymentOption), [accounts, paymentOption]);
  const fittingKey = fitting.map((a) => a.id).join(",");

  // Keep the value on an account that fits: preselect when empty or when the method changed.
  useEffect(() => {
    if (fitting.some((a) => a.id === value)) return;
    const next = pickDefaultAccount(fitting, locationId);
    if (next !== value) onChange(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fittingKey, value, locationId]);

  const option = (Array.isArray(paymentOption) ? paymentOption[0] : paymentOption) || "cash";
  if (!fitting.length) return <SetUpCashbox option={option} locationId={locationId} onCreated={(a) => { onCreated?.(a); onChange(a.id); }} />;

  if (variant === "pills") {
    return (
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Account">
        {fitting.map((a) => (
          <button key={a.id} type="button" role="radio" aria-checked={value === a.id} onClick={() => onChange(a.id)}
            className={`min-h-[2.75rem] px-4 rounded-full border font-medium ${value === a.id ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`}>{a.name || "Account"}</button>
        ))}
      </div>
    );
  }
  return (
    <select id={id} aria-label={id ? undefined : "Account"} value={value} onChange={(e) => onChange(e.target.value)}
      className={selectClassName || "glass-input w-full py-2 px-3 text-xs font-semibold rounded-xl"}>
      {fitting.map((a) => <option key={a.id} value={a.id}>{a.name || "Account"}</option>)}
    </select>
  );
}

function SetUpCashbox({ option, locationId, onCreated }: { option: PaymentOption; locationId?: string | null; onCreated: (a: PayAccount) => void }) {
  const { can } = useMe();
  const [name, setName] = useState(DEFAULT_NAME[option]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  useEffect(() => { setName(DEFAULT_NAME[option]); setErr(null); }, [option]);
  const kind = KIND[option];

  if (denied || !can("can_manage_accounts")) {
    return (
      <div role="note" className="rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2.5 text-xs text-amber-900">
        There's no {kind} set up yet, so this payment has nowhere to go. Ask the owner or a manager (anyone allowed to manage accounts) to add one in Cash, then try again.
      </div>
    );
  }

  async function create() {
    const n = name.trim();
    if (!n) { setErr("Give it a name first."); return; }
    setBusy(true); setErr(null);
    try {
      const a = await api.post<PayAccount>("/cash/accounts", {
        name: n, payment_option: option, icon: ICON[option],
        location_id: locationId || null, is_company: !locationId,
      });
      onCreated({ ...a, payment_option: a.payment_option || option });
    } catch (e: any) {
      if (e?.status === 403) setDenied(true);
      else setErr(e?.message || "Couldn't set it up. Please try again.");
    } finally { setBusy(false); }
  }

  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50/70 p-3 space-y-2">
      <p className="text-xs text-ink-secondary">You don't have a {kind} yet. Set one up now so this money is counted in the right place.</p>
      <div className="flex items-center gap-2">
        <input aria-label="New cashbox name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); create(); } }}
          className="glass-input flex-1 min-w-0 py-2 px-3 text-xs font-semibold rounded-xl" />
        <button type="button" onClick={create} disabled={busy}
          className="shrink-0 px-3 py-2 rounded-xl bg-primary text-white text-xs font-bold disabled:opacity-60">{busy ? "Setting up…" : "Set up a cashbox"}</button>
      </div>
      {err && <p className="text-xs text-rose-600">{err}</p>}
    </div>
  );
}
