// One sign convention for client balances, used everywhere a balance is shown.
//   balance > 0  → the client OWES the shop
//   balance < 0  → the client is IN CREDIT (the shop owes them)
//   balance = 0  → settled
export type BalanceTone = "owes" | "credit" | "settled";
export type BalanceView = { label: "Owes" | "In credit" | "Settled"; amount: number; tone: BalanceTone; className: string };

const CLASS: Record<BalanceTone, string> = { owes: "text-rose-600", credit: "text-emerald-600", settled: "text-ink-tertiary" };

export function balanceView(balance: number | null | undefined): BalanceView {
  const b = Number(balance) || 0;
  // Treat sub-cent noise as settled so a float rounding error never reads as a debt.
  if (Math.abs(b) < 0.005) return { label: "Settled", amount: 0, tone: "settled", className: CLASS.settled };
  if (b > 0) return { label: "Owes", amount: b, tone: "owes", className: CLASS.owes };
  return { label: "In credit", amount: -b, tone: "credit", className: CLASS.credit };
}
