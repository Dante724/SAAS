// Tasks: mine = assigned to me (alone or with others) or made by me. Grouped by when they're due.
// Ticking one off is safe to queue offline (it sets a value), so it goes through the offline queue.
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Circle, Link2, ListTodo, RefreshCw } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Empty, GoButton, IconButton, Page, Section, SkeletonRows } from "../components/ui";
import { goQueue } from "../lib/offlineQueue";
import { haptic } from "../lib/scanner";
import { announceChange, useOnChange, useToast } from "../lib/useGo";
import { serverDate } from "../../lib/serverTime";
import { fmtShop, knownShopTz, loadShopTz, shopToday, toShopWall, useShopClock } from "../../lib/shopTime";

export type Task = {
  id: string; title: string; status: "todo" | "in_progress" | "done" | "cancelled"; due_at?: string | null;
  assigned_to_id?: string | null; assignee_name?: string | null; created_by_id?: string | null; author_name?: string | null;
  order_id?: string | null; completed_at?: string | null;
};


/** Which group a task belongs in. Due times come back as UTC instants: "late" compares instants,
 *  "today" is the SHOP's date (not the phone's). A cancelled task is in no list (it isn't to do,
 *  and it never ran late). Pure, tested. */
export function bucket(t: Task, now = new Date(), tz: string = knownShopTz() || "UTC"): "done" | "cancelled" | "overdue" | "today" | "later" | "someday" {
  if (t.status === "cancelled") return "cancelled";
  if (t.status === "done") return "done";
  if (!t.due_at) return "someday";
  const due = serverDate(t.due_at);
  if (!due) return "someday";
  if (due < now) return "overdue";
  return (toShopWall(t.due_at, tz) || "").slice(0, 10) === shopToday(tz, now) ? "today" : "later";
}

/** Open tasks due today or overdue — the Tasks tab badge. */
export function dueCount(items: Task[], now = new Date(), tz?: string): number {
  return items.filter((t) => ["overdue", "today"].includes(bucket(t, now, tz))).length;
}

export function useTaskBadge() {
  const [n, setN] = useState(0);
  const load = useCallback(() => {
    Promise.all([api.get<{ items: Task[] }>("/tasks?mine=true"), loadShopTz()])
      .then(([r, tz]) => { if (tz) setN(dueCount(r.items || [], new Date(), tz)); }).catch(() => { /* silent: badge count only */ });
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

// On the shop's clock: a time for today, else a short date.
const when = (iso: string | null | undefined, tz: string) => {
  if (!iso) return "";
  const sameDay = (toShopWall(iso, tz) || "").slice(0, 10) === shopToday(tz);
  return sameDay ? fmtShop(iso, tz, { hour: "numeric", minute: "2-digit" })
    : fmtShop(iso, tz, { weekday: "short", day: "numeric", month: "short" });
};

function TaskRow({ t, meId, tz, onToggle, focus }: { t: Task; meId?: string; tz: string; onToggle: (t: Task) => void; focus?: boolean }) {
  const done = t.status === "done";
  const late = bucket(t, new Date(), tz) === "overdue";
  const from = t.created_by_id && t.created_by_id !== meId && t.author_name ? `From ${t.author_name}` : "";
  const forWho = t.assigned_to_id && t.assigned_to_id !== meId && t.assignee_name ? `For ${t.assignee_name}` : "";
  // Opened from a task alert: bring it into view and mark it, once.
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (focus) ref.current?.scrollIntoView?.({ block: "center" }); }, [focus]);
  return (
    <div ref={ref} id={`task-${t.id}`} aria-current={focus ? "true" : undefined}
      className={`flex items-start gap-2 rounded-xl border-[0.5px] bg-white p-2 pr-4 ${focus ? "border-primary ring-2 ring-primary/30" : "border-slate-200"}`}>
      <button onClick={() => onToggle(t)} aria-label={done ? "Mark not done" : "Mark done"} aria-pressed={done}
        className="h-11 w-11 shrink-0 inline-flex items-center justify-center rounded-full active:bg-slate-100">
        {done ? <CheckCircle2 size={26} className="text-emerald-600" /> : <Circle size={26} className="text-ink-tertiary" />}
      </button>
      <div className="min-w-0 flex-1 py-2.5">
        <p className={`text-go-body font-medium ${done ? "text-ink-tertiary line-through" : "text-ink-primary"}`}>{t.title}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-go-sub text-ink-secondary">
          {t.due_at && <span className={late && !done ? "font-semibold text-rose-700" : ""}>{late && !done ? "Overdue · " : ""}{when(t.due_at, tz)}</span>}
          {(from || forWho) && <span>{from || forWho}</span>}
          {t.order_id && <Link to={`/job/${encodeURIComponent(t.order_id)}`} className="-my-3 -mx-2 inline-flex min-h-11 min-w-11 items-center gap-1 px-2 font-semibold text-primary"><Link2 size={14} />Open job</Link>}
        </p>
      </div>
    </div>
  );
}

export default function Tasks() {
  const { me } = useMe();
  // Group and show due times on the shop's clock; if it can't be reached, say so (never guess UTC).
  const { tz: shopTz, failed: tzFailed, retry: retryTz } = useShopClock();
  const tz = shopTz || "UTC";
  const toast = useToast();
  const [items, setItems] = useState<Task[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showDone, setShowDone] = useState(false);
  // A task alert opens /go/tasks?task=<id>: show that task (or say it isn't there).
  const [sp, setSp] = useSearchParams();
  const [focusId, setFocusId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try { setItems((await api.get<{ items: Task[] }>("/tasks?mine=true")).items || []); }
    catch { setErr(navigator.onLine ? "Couldn't load your tasks." : "You're offline."); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useOnChange("tasks", load);
  const wanted = sp.get("task");
  useEffect(() => {
    if (!wanted || !items || !shopTz) return;
    const t = items.find((x) => x.id === wanted && x.status !== "cancelled");
    if (t) { setFocusId(t.id); if (t.status === "done") setShowDone(true); }
    else toast({ title: "This task isn't available", tone: "warn" });
    setSp((p) => { p.delete("task"); return p; }, { replace: true });
  }, [wanted, items, shopTz, toast, setSp]);

  async function toggle(t: Task) {
    const next = t.status === "done" ? "todo" : "done";
    setItems((l) => l?.map((x) => (x.id === t.id ? { ...x, status: next } : x)) ?? l);
    if (next === "done") haptic();
    const r = await goQueue().mutate("PATCH", `/api/tasks/${encodeURIComponent(t.id)}`, { status: next });
    if (!r.ok) {
      setItems((l) => l?.map((x) => (x.id === t.id ? { ...x, status: t.status } : x)) ?? l);
      toast({ title: "Couldn't update the task", body: "Try again in a moment.", tone: "error" });
    } else if (!r.queued) announceChange("tasks");   // refresh the tab badge
  }

  const groups = { overdue: [] as Task[], today: [] as Task[], later: [] as Task[], someday: [] as Task[], done: [] as Task[], cancelled: [] as Task[] };
  (shopTz ? items || [] : []).forEach((t) => groups[bucket(t, new Date(), tz)].push(t));
  const refresh = (
    <IconButton label="Refresh" onClick={load} className="-mr-2" icon={<RefreshCw size={20} />} />
  );
  const list = (ts: Task[]) => <div className="space-y-2">{ts.map((t) => <TaskRow key={t.id} t={t} meId={me?.id} tz={tz} onToggle={toggle} focus={t.id === focusId} />)}</div>;
  const open = groups.overdue.length + groups.today.length + groups.later.length + groups.someday.length;

  return (
    <Page title="Tasks" right={refresh} subtitle={items ? (open ? `${open} to do` : "All done") : undefined}>
      {err && !items && <Empty icon={<AlertTriangle size={22} />} title="Couldn't load" body={err} action={<GoButton variant="link" onClick={load}>Try again</GoButton>} />}
      {tzFailed && <Empty icon={<AlertTriangle size={22} />} title="Couldn't reach your shop's clock" body="Due times can't be shown until it loads." action={<GoButton variant="link" onClick={retryTz}>Try again</GoButton>} />}
      {(!items || !shopTz) && !err && !tzFailed && <SkeletonRows n={3} />}
      {items && shopTz && !open && !groups.done.length && <Empty icon={<ListTodo size={22} />} title="No tasks" body="Tasks assigned to you, and ones you make with +, show up here." />}
      {groups.overdue.length > 0 && <Section title="Overdue" count={groups.overdue.length}>{list(groups.overdue)}</Section>}
      {groups.today.length > 0 && <Section title="Today" count={groups.today.length}>{list(groups.today)}</Section>}
      {groups.later.length > 0 && <Section title="Coming up" count={groups.later.length}>{list(groups.later)}</Section>}
      {groups.someday.length > 0 && <Section title="No date" count={groups.someday.length}>{list(groups.someday)}</Section>}
      {groups.done.length > 0 && (
        <Section title="Done" count={groups.done.length}>
          {showDone ? list(groups.done.slice(0, 20))
            : <GoButton variant="link" onClick={() => setShowDone(true)} className="px-1">Show done tasks</GoButton>}
        </Section>
      )}
    </Page>
  );
}

