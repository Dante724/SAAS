// A technician's queue: what to pick up next. Pure (unit-tested).
//   1. overdue first (the promise is already broken)
//   2. then higher priority (2 = VIP, 1 = urgent, 0 = normal)
//   3. then the earliest promised-by date, then the earliest booking; jobs with neither go last
//   4. ties keep the server's order

export type QueueJob = {
  id: string; priority?: number | null; due_at?: string | null; scheduled_for?: string | null; overdue?: boolean;
};

const ts = (iso?: string | null) => {
  if (!iso) return Infinity;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? Infinity : t;
};

export function sortQueue<T extends QueueJob>(jobs: T[], overdueIds: Set<string> = new Set()): T[] {
  return jobs
    .map((j, i) => ({ j, i }))
    .sort((a, b) => {
      const oa = a.j.overdue || overdueIds.has(a.j.id) ? 1 : 0;
      const ob = b.j.overdue || overdueIds.has(b.j.id) ? 1 : 0;
      if (oa !== ob) return ob - oa;
      const pa = a.j.priority ?? 0, pb = b.j.priority ?? 0;
      if (pa !== pb) return pb - pa;
      const da = ts(a.j.due_at), db = ts(b.j.due_at);
      if (da !== db) return da - db;
      const sa = ts(a.j.scheduled_for), sb = ts(b.j.scheduled_for);
      if (sa !== sb) return sa - sb;
      return a.i - b.i;
    })
    .map((x) => x.j);
}

/** Merge the dashboard's buckets into one list without duplicates (a job can be in two). */
export function mergeBuckets<T extends { id: string }>(...lists: T[][]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const l of lists) for (const j of l) if (!seen.has(j.id)) { seen.add(j.id); out.push(j); }
  return out;
}

export const PRIORITY_LABEL: Record<number, string> = { 0: "Normal", 1: "Urgent", 2: "VIP" };
