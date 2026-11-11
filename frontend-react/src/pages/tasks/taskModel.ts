// Tasks — one reading of a task that works against both the current API and the new one
// (canonical statuses, integer priority, soft delete, server-side q/assignee/status/limit/offset).
// The client-side filters are idempotent: when the server already filtered, running them again
// changes nothing; when it didn't (older API ignores the params), they do the filtering here.

export type Task = {
  id: string; title?: string; description?: string | null; status?: string | null;
  priority?: number | string | null;
  assigned_to_id?: string | null; assignee_name?: string | null; assignee_ids?: string[]; assignee_names?: string[];
  created_by_id?: string | null; created_by?: string | null; source?: string | null;
  due_at?: string | null; completed_at?: string | null; overdue?: boolean;
  order_id?: string | null; order_number?: string | null; client_id?: string | null; client_name?: string | null;
  parent_task_id?: string | null; subtasks?: Task[];
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
