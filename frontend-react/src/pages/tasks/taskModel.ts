// Tasks — one reading of a task that works against both the current API and the new one
// (canonical statuses, integer priority, soft delete, server-side q/assignee/status/limit/offset).
// The client-side filters are idempotent: when the server already filtered, running them again
// changes nothing; when it didn't (older API ignores the params), they do the filtering here.

import { shopLocale } from "../../lib/shopTime";

export type Task = {
  id: string; title?: string; description?: string | null; status?: string | null;
  priority?: number | string | null;
  assigned_to_id?: string | null; assignee_name?: string | null; assignee_ids?: string[]; assignee_names?: string[];
  created_by_id?: string | null; created_by?: string | null; source?: string | null;
  due_at?: string | null; completed_at?: string | null; overdue?: boolean;
  order_id?: string | null; order_number?: string | null; client_id?: string | null; client_name?: string | null;
  parent_task_id?: string | null; subtasks?: Task[];
  /** due_at on the shop's wall clock (screen only, see withShopTimes); never sent back. */
  due_wall?: string | null;
};

export type Status = "todo" | "in_progress" | "done" | "cancelled";
export const STATUS_LABEL: Record<Status, string> = { todo: "To do", in_progress: "In progress", done: "Done", cancelled: "Cancelled" };

/** Canonical status. Legacy "open", blank or anything unknown reads as To do. */
export function normStatus(s?: string | null): Status {
  const v = (s || "").trim().toLowerCase();
  return v === "in_progress" || v === "done" || v === "cancelled" ? v : "todo";
}

/** What the badge says. A completed task is Done whatever its status string. */
export function statusLabel(t: Pick<Task, "status" | "completed_at">): string {
  return t.completed_at ? STATUS_LABEL.done : STATUS_LABEL[normStatus(t.status)];
}

export const PRIORITY_LABEL = ["Normal", "High", "Urgent"] as const;

/** 0 normal / 1 high / 2 urgent, from an int, a numeric string or a legacy word. */
export function normPriority(p?: number | string | null): 0 | 1 | 2 {
  if (p == null || p === "") return 0;
  const n = typeof p === "number" ? p : /^-?\d+$/.test(String(p).trim()) ? Number(p) : NaN;
  if (!Number.isNaN(n)) return n >= 2 ? 2 : n === 1 ? 1 : 0;
  const w = String(p).trim().toLowerCase();
  return w === "urgent" || w === "critical" ? 2 : w === "high" ? 1 : 0;
}
export const priorityLabel = (p?: number | string | null) => PRIORITY_LABEL[normPriority(p)];

/** Made by an automation rather than a person. */
export function isAuto(t: Pick<Task, "source" | "created_by">): boolean {
  if (t.source) return t.source.toLowerCase() !== "manual";
  return (t.created_by || "").toLowerCase() === "automation";
}

/** "Mine": I'm an assignee or I made it. */
export function involves(t: Task, me?: string | null): boolean {
  if (!me) return true;   // before we know who we are, don't hide anything
  return t.assigned_to_id === me || t.created_by_id === me || (t.assignee_ids || []).includes(me);
}

export function matchesQuery(t: Task, q?: string): boolean {
  const s = (q || "").trim().toLowerCase();
  if (!s) return true;
  return [t.title, t.description, t.order_number, t.client_name].some((x) => (x || "").toLowerCase().includes(s));
}

export type StatusFilter = "all" | "todo" | "in_progress" | "done" | "overdue";
export type Filters = { me?: string | null; scope: "mine" | "everyone"; status: StatusFilter; q?: string; assignee?: string; priority?: string };

export function matchesStatus(t: Task, f: StatusFilter): boolean {
  if (f === "all") return true;
  if (f === "overdue") return !!t.overdue && !t.completed_at;
  if (f === "done") return !!t.completed_at || normStatus(t.status) === "done";
  return !t.completed_at && normStatus(t.status) === f;
}

export function applyFilters(items: Task[], f: Filters): Task[] {
  return items.filter((t) =>
    (f.scope === "everyone" || involves(t, f.me))
    && matchesStatus(t, f.status)
    && matchesQuery(t, f.q)
    && (!f.assignee || t.assigned_to_id === f.assignee || (t.assignee_ids || []).includes(f.assignee))
    && (f.priority == null || f.priority === "" || normPriority(t.priority) === Number(f.priority)));
}

// ── My day ────────────────────────────────────────────────────────────────────
// GET /tasks/my-day sorts one person's open tasks into Catch up (late), Today, Upcoming and
// Later, plus what they finished today. `date` is the SHOP's today, so the client never guesses it
// from the browser clock. These helpers only regroup and move what the server sent.

export type DayBucket = "catch_up" | "today" | "upcoming" | "later" | "done_today";
export const DAY_BUCKETS: DayBucket[] = ["catch_up", "today", "upcoming", "later", "done_today"];
export type MyDay = Record<DayBucket, Task[]> & {
  date: string; counts: Record<DayBucket, number>;
  /** Set by filterDay: the group it was cut from held more than the server sent (100 a group), so
   *  this group's count covers only what was on screen. */
  capped?: Partial<Record<DayBucket, boolean>>;
};
/** The server sent fewer rows than it counted for this group (it lists at most 100). */
export const isCapped = (day: MyDay, b: DayBucket): boolean => day.counts[b] > day[b].length || !!day.capped?.[b];

/** Fill in anything an older or partial reply left out. */
export function normDay(r: Partial<MyDay> | null | undefined, fallbackDate: string): MyDay {
  const d = { date: r?.date || fallbackDate } as MyDay;
  for (const b of DAY_BUCKETS) d[b] = Array.isArray(r?.[b]) ? (r![b] as Task[]) : [];
  d.counts = {} as MyDay["counts"];
  for (const b of DAY_BUCKETS) d.counts[b] = typeof r?.counts?.[b] === "number" ? r!.counts![b] : d[b].length;
  return d;
}

/** Move tasks between groups, keeping the counts in step (optimistic updates and their undo). */
export function moveTasks(day: MyDay, ids: string[], to: DayBucket, patch: Partial<Task> = {}): MyDay {
  const want = new Set(ids);
  const next = { ...day, counts: { ...day.counts } } as MyDay;
  const moved: Task[] = [];
  for (const b of DAY_BUCKETS) {
    const keep: Task[] = [];
    for (const t of day[b]) (want.has(t.id) ? moved : keep).push(t);
    if (keep.length !== day[b].length) next.counts[b] = Math.max(0, day.counts[b] - (day[b].length - keep.length));
    next[b] = keep;
  }
  const add = moved.map((t) => ({ ...t, ...patch }));
  next[to] = to === "done_today" ? [...add, ...next[to]] : [...next[to], ...add];
  next.counts[to] = next.counts[to] + add.length;
  return next;
}

/** Add one task (a quick add) to a group. */
export function insertTask(day: MyDay, t: Task, to: DayBucket): MyDay {
  return { ...day, [to]: [...day[to], t], counts: { ...day.counts, [to]: day.counts[to] + 1 } };
}

/** Swap a task for a newer copy (the server's answer to a quick add), or drop it (`with` = null). */
export function replaceTask(day: MyDay, id: string, withTask: Task | null): MyDay {
  const next = { ...day, counts: { ...day.counts } } as MyDay;
  for (const b of DAY_BUCKETS) {
    if (!day[b].some((t) => t.id === id)) continue;
    next[b] = withTask ? day[b].map((t) => (t.id === id ? withTask : t)) : day[b].filter((t) => t.id !== id);
    if (!withTask) next.counts[b] = Math.max(0, day.counts[b] - 1);
  }
  return next;
}

/** What staff see on the chip for a task the system made (source != manual), and why. */
export const AUTO_LABEL = "Made by Tekly";
export const AUTO_HINT = "Created automatically from a work order or a call";

/** In the shop's pool: nobody is on it yet. */
export const isPool = (t: Task): boolean => !t.assigned_to_id && !(t.assignee_ids || []).length;

/** With include_pool=true the server mixes the shop's unassigned tasks into the groups. Take the
 *  ones other people made out into their own list ("Up for grabs"; my own unassigned to-dos stay
 *  in my day), and take them off the counts. A null `me` leaves the day as it is. */
export function splitPool(day: MyDay, me: string | null | undefined): { day: MyDay; pool: Task[] } {
  if (!me) return { day, pool: [] };
  const next = { ...day, counts: { ...day.counts } } as MyDay;
  const pool: Task[] = [];
  for (const b of ["catch_up", "today", "upcoming", "later"] as DayBucket[]) {
    const keep: Task[] = [];
    for (const t of day[b]) (isPool(t) && t.created_by_id !== me ? pool : keep).push(t);
    next[b] = keep;
    next.counts[b] = Math.max(0, day.counts[b] - (day[b].length - keep.length));
  }
  pool.sort((a, b) => (dueOf(a) || "9999").localeCompare(dueOf(b) || "9999"));
  return { day: next, pool };
}

/** The due time as the shop's wall clock: due_wall once withShopTimes ran, else due_at as sent. */
export const dueOf = (t: Pick<Task, "due_at" | "due_wall">): string | null | undefined => (t.due_wall !== undefined ? t.due_wall : t.due_at);

/** Put every task's due time on the shop's clock (the server sends UTC instants). */
export function withShopTimes(day: MyDay, toWall: (iso: string | null | undefined) => string | null): MyDay {
  const next = { ...day } as MyDay;
  for (const b of DAY_BUCKETS) next[b] = day[b].map((t) => ({ ...t, due_wall: toWall(t.due_at) }));
  return next;
}

/** "HH:MM" of a wall-clock due time, or null when the task is due "some time that day"
 *  (a date-only value, or the end-of-day time the server stores for one). */
export function dueClock(due?: string | null): string | null {
  const m = /T(\d\d):(\d\d)/.exec(due || "");
  if (!m) return null;
  if (m[1] === "23" && Number(m[2]) >= 59) return null;
  return `${m[1]}:${m[2]}`;
}

/** "9:30 AM" for a due time, in the viewer's 12/24h style. */
export function fmtClock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date(2000, 0, 1, h, m);
  return d.toLocaleTimeString(shopLocale() ?? [], { hour: "numeric", minute: "2-digit" });
}

export type TaskGroup = { key: string; label: string; items: Task[] };
/** Today's list split into parts of the day, earliest first; tasks with no time go last. */
export function groupByTime(items: Task[]): TaskGroup[] {
  const parts: TaskGroup[] = [
    { key: "morning", label: "Morning", items: [] }, { key: "afternoon", label: "Afternoon", items: [] },
    { key: "evening", label: "Evening", items: [] }, { key: "anytime", label: "Any time today", items: [] },
  ];
  const sorted = [...items].sort((a, b) => (dueClock(dueOf(a)) || "99").localeCompare(dueClock(dueOf(b)) || "99") || normPriority(b.priority) - normPriority(a.priority));
  for (const t of sorted) {
    const c = dueClock(dueOf(t));
    const h = c ? Number(c.slice(0, 2)) : -1;
    parts[h < 0 ? 3 : h < 12 ? 0 : h < 17 ? 1 : 2].items.push(t);
  }
  return parts.filter((p) => p.items.length);
}

const dayOf = (due?: string | null) => (due || "").slice(0, 10);
const asLocal = (ymd: string) => new Date(`${ymd}T00:00`);
export function addDays(ymd: string, n: number): string {
  const d = asLocal(ymd); d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** "Today", "Tomorrow", "Friday, Nov 14" or "No date", for a group heading. */
export function dayLabel(ymd: string, today: string): string {
  if (!ymd) return "No date";
  if (ymd === today) return "Today";
  if (ymd === addDays(today, 1)) return "Tomorrow";
  const sameYear = ymd.slice(0, 4) === today.slice(0, 4);
  return asLocal(ymd).toLocaleDateString(shopLocale() ?? [], { weekday: "long", month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

/** Upcoming and Later: one group per day, soonest first; tasks with no date last. */
export function groupByDay(items: Task[], today: string): TaskGroup[] {
  const map = new Map<string, Task[]>();
  for (const t of [...items].sort((a, b) => (dueOf(a) || "9999").localeCompare(dueOf(b) || "9999"))) {
    const k = dayOf(dueOf(t));
    if (!map.has(k)) map.set(k, []);
    map.get(k)!.push(t);
  }
  return [...map.entries()].map(([k, v]) => ({ key: k || "none", label: dayLabel(k, today), items: v }));
}

/** When a catch-up task was due: "Yesterday", else a clear date in the shop's style ("Fri 7 Nov"). */
export function lateLabel(due: string | null | undefined, today: string): string {
  const k = dayOf(due);
  if (!k) return "";
  if (k === addDays(today, -1)) return "Yesterday";
  const d = asLocal(k);
  // A clear date ("Fri 7 Nov"), never a bare weekday that could be this week or last.
  return d.toLocaleDateString(shopLocale() ?? [], { weekday: "short", day: "numeric", month: "short", ...(k.slice(0, 4) === today.slice(0, 4) ? {} : { year: "numeric" }) });
}

export type QuickWhen = "today" | "tomorrow" | "pick";
/** A quick add's due date: a date-only string, which the server reads as the end of that shop day. */
export function quickDue(when: QuickWhen, picked: string, today: string): string | null {
  if (when === "today") return today;
  if (when === "tomorrow") return addDays(today, 1);
  return picked || null;
}

/** Where a quick-added task shows before the server answers. Upcoming is the next seven days. */
export function bucketForDue(due: string | null, today: string): DayBucket {
  const k = dayOf(due);
  if (!k) return "later";
  if (k <= today) return "today";
  return k <= addDays(today, 7) ? "upcoming" : "later";
}

// ── whose tasks / team row ────────────────────────────────────────────────────

/** Everyone on a task: the primary owner and the extra assignees, once each. */
export const assigneesOf = (t: Pick<Task, "assigned_to_id" | "assignee_ids">): string[] =>
  [...new Set([t.assigned_to_id, ...(t.assignee_ids || [])].filter((x): x is string => !!x))];

/** Keep only some tasks of a day (Up for grabs out of everyone's day). Counts are always what is
 *  left in the lists; a group whose source was capped (the server counted more than it sent) is
 *  marked in `capped`, so the screen can say there may be more. */
export function filterDay(day: MyDay, keep: (t: Task) => boolean): MyDay {
  const next = { ...day, counts: { ...day.counts }, capped: {} } as MyDay;
  for (const b of DAY_BUCKETS) {
    next[b] = day[b].filter(keep);
    next.counts[b] = next[b].length;
    if (isCapped(day, b)) next.capped![b] = true;
  }
  return next;
}

/** A count and whether it is only a lower bound (counted from a capped group): shown as "100+". */
export type Tally = { n: number; more: boolean };
export const fmtTally = (t: Tally): string => `${t.n}${t.more ? "+" : ""}`;
export type PersonLoad = { id: string; name: string; position?: string | null; overdue: Tally; today: Tally; done: Tally };
/** The team row, from everyone's day: how many open tasks wait in the pool, and per person how
 *  many are overdue, due today and done today. A task with two people counts for both. Counted
 *  from the lists, so a number from a capped group is a lower bound (`more`). */
export function teamLoad(day: MyDay, people: { id: string; name: string; position?: string | null }[]): { pool: Tally; people: PersonLoad[] } {
  const open: DayBucket[] = ["catch_up", "today", "upcoming", "later"];
  const pool = { n: open.reduce((n, b) => n + day[b].filter(isPool).length, 0), more: open.some((b) => isCapped(day, b)) };
  const tally = (b: DayBucket, id: string): Tally => ({ n: day[b].filter((t) => assigneesOf(t).includes(id)).length, more: isCapped(day, b) });
  return { pool, people: people.map((p) => ({ id: p.id, name: p.name, position: p.position, overdue: tally("catch_up", p.id), today: tally("today", p.id), done: tally("done_today", p.id) })) };
}

/** One person's card from their own day: the server's exact counts (and the optimistic moves on it). */
export function ownLoad(day: MyDay): Pick<PersonLoad, "overdue" | "today" | "done"> {
  return { overdue: { n: day.counts.catch_up, more: false }, today: { n: day.counts.today, more: false }, done: { n: day.counts.done_today, more: false } };
}
