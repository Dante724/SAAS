// Cash drawer glance stats. Pure + tested — the only new logic in the /cash redesign (144bg).
export type CashTxn = { txn_type?: string; amount?: number; actual_date?: string; status?: string };

function sameDay(iso: string | undefined, ref: Date): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return false;
  return d.getFullYear() === ref.getFullYear() && d.getMonth() === ref.getMonth() && d.getDate() === ref.getDate();
}

// Money direction comes from the sign, not the type: amount > 0 is money IN, amount < 0 is money OUT
// (sales are `invoice_payment` / `income`, refunds can be `expense`). Transfers between the shop's own
// accounts move no money in or out of the business, so they are left out of the totals.
export function txnDirection(t: CashTxn): "in" | "out" {
  return (t.amount || 0) < 0 ? "out" : "in";
}

// In / out / net for the given day (default: now). Deleted rows and transfers are excluded.
export function todayTotals(txns: CashTxn[], now: Date = new Date()): { in: number; out: number; net: number } {
  let inc = 0, exp = 0;
  for (const t of txns || []) {
    if (t.status === "deleted") continue;
    if (t.txn_type === "transfer") continue;
    if (!sameDay(t.actual_date, now)) continue;
    const amt = t.amount || 0;
    if (amt > 0) inc += amt;
    else if (amt < 0) exp += -amt;
  }
  return { in: inc, out: exp, net: inc - exp };
}
