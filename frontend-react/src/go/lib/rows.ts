// What a Today row says. Pure (unit-tested).
//   top line:    customer · promised time on the right (red once it's late)
//   second line: "Device · Problem" · the WO number on the right
// Due and scheduled times are shop-local wall-clock (no "Z"), so `new Date(iso)` reads them right.

export type RowJob = {
  client?: string | null; device?: string | null; complaint?: string | null;
  due_at?: string | null; scheduled_for?: string | null; overdue?: boolean;
};

const DAY = 86_400_000;
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
const clock = (d: Date) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const date = (d: Date) => d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });

export const rowTitle = (j: RowJob) => (j.client || "").trim() || "Walk-in";

export function rowDetail(j: RowJob): string {
  return [j.device, j.complaint].map((s) => (s || "").trim()).filter(Boolean).join(" · ");
}

/** The time on the right of the top line: the promise (due) first, else the booking. */
export function rowWhen(j: RowJob, now: Date = new Date()): { text: string; late: boolean } | null {
  const iso = j.due_at || j.scheduled_for;
  if (!iso) return j.overdue ? { text: "Late", late: true } : null;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return null;
  const late = !!j.due_at && (j.overdue || t.getTime() < now.getTime());
  const days = Math.round((startOfDay(t) - startOfDay(now)) / DAY);
  if (late && days < 0) return { text: days === -1 ? "1 day late" : `${-days} days late`, late };
  const text = days === 0 ? clock(t) : days === 1 ? `Tomorrow ${clock(t)}` : date(t);
  return { text, late };
}

/** A list only shows each row's status when the list mixes statuses. */
export const mixesStatuses = (jobs: { status: string }[]) => new Set(jobs.map((j) => j.status)).size > 1;
