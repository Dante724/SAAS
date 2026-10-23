// Pure helpers for taking payment on the phone (unit-tested).

const round2 = (n: number) => Math.round(n * 100) / 100;

/** Quick "customer handed me" amounts: exact, then the next round notes above it. */
export function quickTender(due: number): number[] {
  if (!(due > 0)) return [];
  const out = [round2(due)];
  for (const step of [5, 10, 20, 50, 100, 500, 1000, 2000, 5000, 10000, 20000]) {
    const v = Math.ceil(due / step) * step;
    if (v > due + 0.001 && !out.includes(v)) out.push(v);
    if (out.length >= 4) break;
  }
  return out;
}

export function changeDue(tendered: number, amount: number): number {
  return tendered > amount ? round2(tendered - amount) : 0;
}

/** A part payment may go down, never above what's owed (the server refuses that too). */
export function clampAmount(raw: string, remaining: number): number | null {
  const n = Number(String(raw).replace(/[^\d.]/g, ""));
  if (!isFinite(n) || n <= 0) return null;
  return Math.min(round2(n), round2(remaining));
}

export type StatusLite = { key: string; name: string; status_group?: string; is_terminal?: boolean; sort_order?: number };

/** The status "Finish" moves a job to: the first allowed finishing status in the closed group,
 *  else any allowed finishing status. Null when the role can't finish from here. */
export function finishTarget(statuses: StatusLite[], allowed: string[] | null): StatusLite | null {
  const ok = (s: StatusLite) => !allowed || allowed.includes(s.key);
  const sorted = [...statuses].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
  return sorted.find((s) => s.is_terminal && s.status_group === "closed" && ok(s))
    ?? sorted.find((s) => s.is_terminal && s.status_group !== "dropped" && ok(s)) ?? null;
}

/** Money with cents (the app-wide fmt() rounds to whole units, which is wrong at the till). */
export function money(currency: string | undefined, n?: number | null): string {
  return `${currency || "$"}${(n ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
