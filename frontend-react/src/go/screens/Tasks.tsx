// Tasks: mine = assigned to me (alone or with others) or made by me. Grouped by when they're due.
// Ticking one off is safe to queue offline (it sets a value), so it goes through the offline queue.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Circle, Link2, ListTodo, RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Card, Empty, Page, Section, SkeletonRows } from "../components/ui";
import { goQueue } from "../lib/offlineQueue";
import { haptic } from "../lib/scanner";
import { announceChange, useOnChange, useToast } from "../lib/useGo";

export type Task = {
  id: string; title: string; status: "todo" | "in_progress" | "done"; due_at?: string | null;
  assigned_to_id?: string | null; assignee_name?: string | null; created_by_id?: string | null; author_name?: string | null;
  order_id?: string | null; completed_at?: string | null;
};


/** Which group a task belongs in. Due times are shop-local wall-clock, so they parse as local. Pure, tested. */
export function bucket(t: Task, now = new Date()): "done" | "overdue" | "today" | "later" | "someday" {
  if (t.status === "done") return "done";
  if (!t.due_at) return "someday";
  const due = new Date(t.due_at);
  if (due < now) return "overdue";
  const eod = new Date(now); eod.setHours(23, 59, 59, 999);
  return due <= eod ? "today" : "later";
}

/** Open tasks due today or overdue — the Tasks tab badge. */
export function dueCount(items: Task[], now = new Date()): number {
  return items.filter((t) => ["overdue", "today"].includes(bucket(t, now))).length;
}

export function useTaskBadge() {
  const [n, setN] = useState(0);
  const load = useCallback(() => {
    api.get<{ items: Task[] }>("/tasks?mine=true").then((r) => setN(dueCount(r.items || []))).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    const on = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, [load]);
  useOnChange("tasks", load);
  return n;
}

const when = (iso?: string | null) => {
  if (!iso) return "";
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
};

function TaskRow({ t, meId, onToggle }: { t: Task; meId?: string; onToggle: (t: Task) => void }) {
  const done = t.status === "done";
  const late = bucket(t) === "overdue";
  const from = t.created_by_id && t.created_by_id !== meId && t.author_name ? `From ${t.author_name}` : "";
  const forWho = t.assigned_to_id && t.assigned_to_id !== meId && t.assignee_name ? `For ${t.assignee_name}` : "";
  return (
    <div className="flex items-start gap-2 rounded-2xl border border-slate-100 bg-white p-2 pr-4 shadow-clay-card">
      <button onClick={() => onToggle(t)} aria-label={done ? "Mark not done" : "Mark done"} aria-pressed={done}
        className="h-11 w-11 shrink-0 inline-flex items-center justify-center rounded-full active:bg-slate-100">
        {done ? <CheckCircle2 size={26} className="text-emerald-600" /> : <Circle size={26} className="text-ink-tertiary" />}
      </button>
      <div className="min-w-0 flex-1 py-2.5">
        <p className={`text-[1rem] font-medium ${done ? "text-ink-tertiary line-through" : "text-ink-primary"}`}>{t.title}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[0.8125rem] text-ink-secondary">
          {t.due_at && <span className={late && !done ? "font-semibold text-rose-700" : ""}>{late && !done ? "Overdue · " : ""}{when(t.due_at)}</span>}
          {(from || forWho) && <span>{from || forWho}</span>}
          {t.order_id && <Link to={`/job/${encodeURIComponent(t.order_id)}`} className="inline-flex items-center gap-1 font-semibold text-primary"><Link2 size={13} />Job</Link>}
        </p>
      </div>
    </div>
  );
}

export default function Tasks() {
  const { me } = useMe();
  const toast = useToast();
  const [items, setItems] = useState<Task[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try { setItems((await api.get<{ items: Task[] }>("/tasks?mine=true")).items || []); }
    catch (e: any) { setErr(navigator.onLine ? (e?.message || "Couldn't load tasks") : "You're offline."); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useOnChange("tasks", load);

  async function toggle(t: Task) {
    const next = t.status === "done" ? "todo" : "done";
    setItems((l) => l?.map((x) => (x.id === t.id ? { ...x, status: next } : x)) ?? l);
    if (next === "done") haptic();
    const r = await goQueue().mutate("PATCH", `/api/tasks/${encodeURIComponent(t.id)}`, { status: next });
    if (!r.ok) {
      setItems((l) => l?.map((x) => (x.id === t.id ? { ...x, status: t.status } : x)) ?? l);
      toast({ title: "Couldn't update the task", body: r.data?.detail, tone: "error" });
    } else if (!r.queued) announceChange("tasks");   // refresh the tab badge
  }

  const groups = { overdue: [] as Task[], today: [] as Task[], later: [] as Task[], someday: [] as Task[], done: [] as Task[] };
  (items || []).forEach((t) => groups[bucket(t)].push(t));
  const refresh = (
    <button onClick={load} aria-label="Refresh" className="h-11 w-11 -mr-2 inline-flex items-center justify-center rounded-full text-ink-secondary active:bg-slate-100"><RefreshCw size={20} /></button>
  );
  const list = (ts: Task[]) => <div className="space-y-2">{ts.map((t) => <TaskRow key={t.id} t={t} meId={me?.id} onToggle={toggle} />)}</div>;
  const open = groups.overdue.length + groups.today.length + groups.later.length + groups.someday.length;

  return (
    <Page title="Tasks" right={refresh} subtitle={items ? (open ? `${open} to do` : "All done") : undefined}>
      {err && !items && <Card className="p-4 text-ink-secondary">{err}</Card>}
      {!items && !err && <SkeletonRows n={3} />}
      {items && !open && !groups.done.length && <Empty icon={<ListTodo size={22} />} title="No tasks" body="Tasks assigned to you, and ones you make with +, show up here." />}
      {groups.overdue.length > 0 && <Section title="Overdue" count={groups.overdue.length}>{list(groups.overdue)}</Section>}
      {groups.today.length > 0 && <Section title="Today" count={groups.today.length}>{list(groups.today)}</Section>}
      {groups.later.length > 0 && <Section title="Coming up" count={groups.later.length}>{list(groups.later)}</Section>}
      {groups.someday.length > 0 && <Section title="No date" count={groups.someday.length}>{list(groups.someday)}</Section>}
      {groups.done.length > 0 && (
        <Section title="Done" count={groups.done.length}>
          {showDone ? list(groups.done.slice(0, 20))
            : <button onClick={() => setShowDone(true)} className="min-h-[2.75rem] px-1 font-semibold text-primary">Show done tasks</button>}
        </Section>
      )}
    </Page>
  );
}

