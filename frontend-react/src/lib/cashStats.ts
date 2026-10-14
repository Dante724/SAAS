// Cash drawer glance stats. Pure + tested — the only new logic in the /cash redesign (144bg).
export type CashTxn = { txn_type?: string; amount?: number; actual_date?: string; status?: string };

function sameDay(iso: string | undefined, ref: Date): boolean {
  if (!iso) return false;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return false;
  return d.getFullYear() === ref.getFullYear() && d.getMonth() === ref.getMonth() && d.getDate() === ref.getDate();
}

// In / out / net for the given day (default: now). Deleted rows are excluded; transfers and other
// non income/expense types don't count toward in/out. Amounts are summed by absolute value so a
// signed-negative expense and a positive-typed expense both read as money out.
export function todayTotals(txns: CashTxn[], now: Date = new Date()): { in: number; out: number; net: number } {
  let inc = 0, exp = 0;
  for (const t of txns || []) {
    if (t.status === "deleted") continue;
    if (!sameDay(t.actual_date, now)) continue;
    if (t.txn_type === "income") inc += Math.abs(t.amount || 0);
    else if (t.txn_type === "expense") exp += Math.abs(t.amount || 0);
  }
  return { in: inc, out: exp, net: inc - exp };
}
