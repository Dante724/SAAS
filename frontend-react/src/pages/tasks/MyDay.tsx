import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type RefObject } from "react";
import { Link } from "react-router-dom";
import { createPortal } from "react-dom";
import { M } from "../../components/Shell";
import Button from "../../components/Button";
import Tabs from "../../components/Tabs";
import TeklySelect from "../../components/TeklySelect";
import DateField from "../../components/DateField";
import JobPicker, { type PickedJob } from "../../components/JobPicker";
import EmptyState, { ErrorState } from "../../components/EmptyState";
import { Badge } from "../../components/Card";
import { Avatar } from "../orders/look";
import { api, ApiError } from "../../api/client";
import { useShopClock, toShopWall, fromShopWall, shopToday, fmtShop, shopLocale } from "../../lib/shopTime";
import {
  type Task, type MyDay as Day, type DayBucket, type QuickWhen, type TaskGroup,
  normDay, moveTasks, insertTask, replaceTask, groupByTime, groupByDay, dueClock, fmtClock, lateLabel,
  quickDue, bucketForDue, dayLabel, normPriority, normStatus, PRIORITY_LABEL, isAuto, dueOf, withShopTimes, splitPool, filterDay, isPool, isCapped, assigneesOf, groupByPerson, POOL_ID, FORMER_ID, type PersonGroup, type DayView, AUTO_LABEL, AUTO_HINT,
} from "./taskModel";
import type { Emp } from "./TaskPanel";
import TaskResults from "./TaskResults";

// Tasks, on one page: quick add on top; then the day as Today / Upcoming / Later / Done, for Me,
// Everyone, Up for grabs or one person; and one search that reaches every task in the shop
// (done and cancelled too). GET /tasks/my-day does the sorting (it knows the shop's today); this
// screen only shows it, moves rows optimistically and puts them back if the server says no.

type View = "today" | "upcoming" | "later" | "done";
const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const DONE_DELAY = 420;   // long enough to see the tick land before the row moves to Done
const newKey = () => { try { return crypto.randomUUID(); } catch { return `${Date.now()}-${Math.random().toString(36).slice(2)}`; } };
const PRIO_TINT = ["text-ink-secondary", "text-amber-700", "text-rose-700"] as const;
const MISSING_NOTE = "My day isn't ready yet. Here's the full list for now.";
// The list API can't ask for unassigned tasks, so without My day there is no Up for grabs list.
const MISSING_POOL_NOTE = "Up for grabs needs My day, which isn't ready yet. Here's the full list for now.";

/** Who quick add gives a task to by default: whoever the page shows (Me and Everyone: me). */
export type AddTarget = { id: string | null; label: string; name: string | null };

export default function MyDay({ meId, emps, isManager, person, onPerson, onOpen, flash, missing, onUnavailable, reloadKey, onChanged, onListPaged }: {
  meId?: string;
  emps: Emp[];
  isManager: boolean;
  person: string;                       // "me", "all", "pool" or an employee id (the page clamps it for staff)
  onPerson: (p: string) => void;
  onOpen: (t: Task) => void;
  flash: (msg: string, action?: () => void, label?: string) => void;
  missing: boolean;                     // the server has no /tasks/my-day yet: show the flat list
  onUnavailable: () => void;
  reloadKey: number;                    // bumped by the page when the panel changed something
  onChanged?: () => void;               // something changed here that the page should know about
  onListPaged?: (paged: boolean) => void; // the flat list's server pages (and so soft-deletes)
}) {
  const [raw, setDay] = useState<Day | null>(null);
  // Due times arrive as UTC instants: put them on the shop's clock before grouping (and wait for
  // the zone, so nothing is grouped by the browser's clock first).
  const { tz, failed: tzFailed, retry: retryTz } = useShopClock();
  // Staff also see the shop's unassigned tasks ("Up for grabs") under their own day and can take one.
  const poolMode = !isManager && person === "me";
  const pooled = person === "pool";
  // Up for grabs: a newer server answers assignee=pool itself (exact counts, any number of tasks).
  // An older one doesn't know "pool" (400/422, or 403/404 as an unknown person): then it is
  // everyone's day (managers) or my day with the pool (staff), cut down to the pool here.
  const [poolApi, setPoolApi] = useState(true);
  const serverPool = pooled && poolApi;
  const src = pooled ? (serverPool ? "pool" : isManager ? "all" : "me") : person;
  const dayUrl = `/tasks/my-day?assignee=${encodeURIComponent(src)}${poolMode || (pooled && !serverPool && !isManager) ? "&include_pool=true" : ""}`;
  const split = useMemo(() => {
    if (!raw || !tz) return null;
    const wall = withShopTimes(raw, (iso) => toShopWall(iso, tz));
    if (serverPool) return { day: wall, pool: [] as Task[] };
    return pooled ? { day: filterDay(wall, isPool), pool: [] as Task[] } : splitPool(wall, poolMode ? meId : null);
  }, [raw, tz, pooled, serverPool, poolMode, meId]);
  const day = split?.day ?? null;
  const pool = split?.pool ?? [];
  const [failed, setFailed] = useState(false);
  const [view, setView] = useState<View>("today");
  const [q, setQ] = useState("");
  const [qDeb, setQDeb] = useState("");
  const searchBox = useRef<HTMLInputElement>(null);
  const [showPool, setShowPool] = useState(false);
  const [ticking, setTicking] = useState<string[]>([]);
  const [justDone, setJustDone] = useState<Record<string, { from: DayBucket; status?: string | null }>>({});   // ticked here: offer Undo in the row
  const [busyMove, setBusyMove] = useState(false);
  const [focusAdd, setFocusAdd] = useState(0);
  const req = useRef(0);
  const dayRef = useRef<Day | null>(null);
  dayRef.current = day;
  // The page hands a new onPerson whenever the URL changes; reading it through a ref keeps
  // opening a task (which changes the URL) from reloading the whole day.
  const onPersonRef = useRef(onPerson);
  onPersonRef.current = onPerson;
  const flashRef = useRef(flash);
  flashRef.current = flash;
  useEffect(() => { const h = setTimeout(() => setQDeb(q.trim()), 250); return () => clearTimeout(h); }, [q]);
  const searching = !!qDeb;

  const load = useCallback((quiet = false) => {
    if (missing) return;
    const id = ++req.current;
    if (!quiet) { setDay(null); setFailed(false); }
    api.get<Partial<Day>>(dayUrl).then((r) => {
      if (id !== req.current) return;
      const d = normDay(r, "");
      setDay(d); setFailed(false);
      if (!quiet && person === "me" && !d.catch_up.length && !d.today.length) setFocusAdd((n) => n + 1);   // an empty day: ready to type
    }).catch((e) => {
      if (id !== req.current) return;
      // A server without assignee=pool: fall back to cutting the pool out of a whole day.
      if (src === "pool" && e instanceof ApiError && [400, 403, 404, 422].includes(e.status)) { setPoolApi(false); return; }
      if (e instanceof ApiError && (e.status === 403 || e.status === 404) && src !== "me") {
        // Not allowed to see that day, or that person isn't in the shop any more: back to mine.
        onPersonRef.current("me"); if (e.status === 404) flashRef.current("That person's day isn't available"); return;
      }
      // Only my own day can be a missing route (the server is older than My day).
      if (e instanceof ApiError && e.status === 404) { onUnavailable(); return; }
      if (!quiet || !dayRef.current) setFailed(true);
    });
  }, [dayUrl, src, person, missing, onUnavailable]);
  useEffect(() => { load(); }, [load]);
  const first = useRef(true);
  useEffect(() => { if (first.current) { first.current = false; return; } load(true); }, [reloadKey, load]);
  // "Today" moves on at the shop's midnight, and a tab left in the background may be stale: check
  // every minute, and reload when the page is shown again.
  const shownDate = raw?.date;
  useEffect(() => {
    const tick = () => { if (tz && shownDate && shopToday(tz) !== shownDate) load(true); };
    const vis = () => { if (document.visibilityState === "visible") load(true); };
    const h = setInterval(tick, 60_000);
    document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(h); document.removeEventListener("visibilitychange", vis); };
  }, [tz, shownDate, load]);

  const today = day?.date || (tz ? shopToday(tz) : "");
  const showWho = person !== "me";

  // ── done / undo ──
  async function setDone(t: Task, from: DayBucket, done: boolean, backTo?: DayBucket, prevStatus?: string | null) {
    const back = backTo || (from === "done_today" ? (t.overdue ? "catch_up" : bucketForDue(dueOf(t) || null, today)) : from);
    // Undo puts back the status it had (In progress stays In progress); the due date never changed.
    const reopenAs = normStatus(prevStatus ?? t.status) === "in_progress" ? "in_progress" : "todo";
    if (done) {
      setTicking((s) => [...s, t.id]);
      setTimeout(() => {
        setTicking((s) => s.filter((x) => x !== t.id));
        setDay((d) => d && moveTasks(d, [t.id], "done_today", { status: "done", completed_at: new Date().toISOString() }));
      }, reducedMotion() ? 0 : DONE_DELAY);
    } else {
      setDay((d) => d && moveTasks(d, [t.id], back, { status: reopenAs, completed_at: null }));
      setJustDone((m) => { const n = { ...m }; delete n[t.id]; return n; });
    }
    try {
      await api.patch(`/tasks/${t.id}`, { status: done ? "done" : reopenAs });
      if (done) {
        setJustDone((m) => ({ ...m, [t.id]: { from, status: t.status } }));
        flash(`Done: ${t.title}`, () => { setDone(t, "done_today", false, from, t.status); });
      } else if (backTo) flash(`Reopened: ${t.title}`);
      load(true);
    } catch {
      setTimeout(() => setDay((d) => d && moveTasks(d, [t.id], done ? from : "done_today", { status: t.status, completed_at: t.completed_at })), done && !reducedMotion() ? DONE_DELAY + 20 : 0);
      flash(done ? "Couldn't mark it done. Try again." : "Couldn't reopen the task. Try again.");
    }
  }

  // ── catch up ──
  // The server lists at most 100 catch-up tasks but counts all of them. "Move all" means all: send
  // the ones on screen (pages of up to 500), fetch the day again, and repeat until none are left or
  // a round moves nothing (whatever is left couldn't be moved).
  async function moveAll() {
    if (!day || !day.catch_up.length) return;
    const prev = raw;
    setBusyMove(true);
    setDay((d) => d && moveTasks(d, day.catch_up.map((t) => t.id), "today", { overdue: false, due_at: today }));
    let ids = day.catch_up.map((t) => t.id);
    let moved = 0, skipped = new Set<string>();
    // Up for grabs only moves the pool's tasks, never everyone's.
    const scoped = (d: Day) => (pooled && !serverPool ? filterDay(d, isPool) : d);
    try {
      for (let round = 0; round < 50 && ids.length; round++) {
        let roundMoved = 0;
        for (let i = 0; i < ids.length; i += 500) {
          const page = ids.slice(i, i + 500);
          // {"moved": n, "skipped": [ids]}: skipped ones were finished, removed or changed meanwhile.
          const r = await api.post<{ moved?: number; skipped?: string[] }>("/tasks/move-to-today", { ids: page });
          const sk = Array.isArray(r?.skipped) ? r.skipped : [];
          sk.forEach((x) => skipped.add(x));
          roundMoved += typeof r?.moved === "number" ? r.moved : page.length - sk.length;
        }
        moved += roundMoved;
        if (!roundMoved) break;
        const next = normDay(await api.get<Partial<Day>>(dayUrl), "");
        setDay(next);
        const left = scoped(next);
        ids = left.counts.catch_up > 0 ? left.catch_up.map((t) => t.id).filter((x) => !skipped.has(x)) : [];
      }
      const said = moved === 1 ? "Moved 1 task to today" : `Moved ${moved} tasks to today`;
      flash(!skipped.size ? said : `${moved ? `${said}. ` : ""}${skipped.size === 1 ? "1 couldn't be moved" : `${skipped.size} couldn't be moved`}`);
      setView("today");
      load(true);   // the server's answer wins
    } catch {
      if (!moved) setDay(prev);
      flash(moved ? `Moved ${moved}, then lost the connection. Some are still in Catch up.` : "Couldn't move them to today.", () => { moveAll(); }, "Try again");
      if (moved) load(true);
    } finally { setBusyMove(false); }
  }

  // ── up for grabs ──
  async function take(t: Task) {
    if (!meId) return;
    const before = raw;
    const mine = { assigned_to_id: meId, assignee_ids: [meId], assignee_names: ["Me"] };
    // It leaves the pool and joins my day (the server's pool list simply loses it).
    setDay((d) => d && replaceTask(d, t.id, serverPool ? null : { ...t, ...mine, due_wall: undefined }));
    try {
      await api.patch(`/tasks/${t.id}`, { assignee_ids: [meId] });
      flash(`It's yours: ${t.title}`);
      load(true);
    } catch {
      setDay(before);
      flash("Couldn't take it. Someone may have taken it already.");
      load(true);
    }
  }

  // ── quick add ──
  // A task goes to whoever the page shows: a person → them, Up for grabs → nobody, Me and Everyone → me.
  const people = emps.filter((e) => e.id !== meId);
  const picked = ["me", "all", "pool", meId].includes(person) ? undefined : emps.find((e) => e.id === person);
  const target: AddTarget = pooled ? { id: null, label: "Up for grabs", name: null }
    : picked ? { id: picked.id, label: picked.name.split(/\s+/)[0], name: picked.name }
    : { id: meId || null, label: "Me", name: null };
  async function add(body: Record<string, any>, preview: Task, forName: string | null, key: string): Promise<boolean> {
    const to = bucketForDue(body.due_at ?? null, today);
    // It shows at once only where it belongs: a task for someone else isn't in my day.
    const assignee = body.assigned_to_id as string | null;
    const belongs = person === "all" || (pooled ? !assignee : person === "me" ? assignee === meId : assignee === person);
    if (belongs) setDay((d) => d && insertTask(d, preview, to));
    try {
      const saved = await api.postOnce<Task>("/tasks", body, key);
      if (belongs) setDay((d) => d && replaceTask(d, preview.id, saved && saved.id ? { ...preview, ...saved } : preview));
      const label = body.due_at ? dayLabel(String(body.due_at).slice(0, 10), today) : "";
      const when = !label ? "later" : label === "Today" || label === "Tomorrow" ? label.toLowerCase() : label;
      flash(!assignee ? `Added to Up for grabs, due ${when}` : forName ? `Task added for ${forName}, due ${when}` : `Task added for ${when}`);
      load(true);
      onChanged?.();
      return true;
    } catch {
      if (belongs) setDay((d) => d && replaceTask(d, preview.id, null));
      flash("Couldn't add the task. Check your connection and try again.");
      return false;
    }
  }

  const counts = day?.counts;
  const off = searching || missing;      // a search (or no My day) shows one flat list instead
  const tabs = [
    { id: "today" as View, label: "Today", count: counts ? counts.today + counts.catch_up : null, disabled: off },
    { id: "upcoming" as View, label: "Upcoming", count: counts?.upcoming ?? null, disabled: off },
    { id: "later" as View, label: "Later", count: counts?.later ?? null, disabled: off },
    { id: "done" as View, label: "Done", count: counts?.done_today ?? null, disabled: off },
  ];
  const nameOfPerson = person === "me" ? "you" : person === "all" ? "anyone" : pooled ? "Up for grabs" : (picked?.name || "this person");
  // Everyone is grouped by person (EveryoneDay); the other views list the day as it comes.
  const everyone = person === "all";
  const staffOrder = useMemo(() => {
    const active = emps.filter((e) => e.active !== false);
    return [...active.filter((e) => e.id === meId), ...active.filter((e) => e.id !== meId)];
  }, [emps, meId]);
  const row = (t: Task, from: DayBucket, who = showWho) => (pooled && !isManager
    ? <TaskRow key={t.id} t={t} from={from} today={today} tz={tz || "UTC"} showWho={false} ticking={false} onOpen={() => onOpen(t)} onToggle={() => undefined} onTake={() => take(t)} />
    : <TaskRow key={t.id} t={t} from={from} today={today} tz={tz || "UTC"} showWho={who} ticking={ticking.includes(t.id)}
      onOpen={() => onOpen(t)} onToggle={(done) => setDone(t, from, done)}
      onUndo={from === "done_today" && justDone[t.id] ? () => setDone(t, "done_today", false, justDone[t.id].from, justDone[t.id].status) : undefined} />
  );
  const focusSearch = () => searchBox.current?.focus();
  // The server sends at most 100 rows a group but counts them all: say so, and point at search.
  // A group cut from a capped one (Up for grabs) has no true total: it says only that there may be more.
  const capped = (b: DayBucket) => day && isCapped(day, b)
    ? <div className="px-4 py-3 border-t border-slate-100 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-ink-tertiary">
        {day.counts[b] > day[b].length
          ? <>Showing {day[b].length} of {day.counts[b]}. <Button variant="link" onClick={focusSearch}>Search</Button> finds any of them.</>
          : <>Showing {day[b].length}, there may be more. <Button variant="link" onClick={focusSearch}>Search</Button> finds the rest.</>}
      </div>
    : null;
  const clearSearch = () => { setQ(""); setQDeb(""); };
  const shopDate = today ? new Date(`${today}T00:00`).toLocaleDateString(shopLocale() ?? [], { weekday: "long", month: "long", day: "numeric" }) : "";

  return (
    <div className="space-y-4 max-w-[1120px]">
      {/* Row 1: the main action, full width. */}
      <QuickAdd today={today} tz={tz} tzFailed={tzFailed} meId={meId} emps={emps} target={target} mayAssign={isManager} onAdd={add} flash={flash} focusKey={focusAdd} />

      {/* Row 2: which days on the left; whose tasks and one search on the right. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Tabs look="segmented" label="Which days" value={view} onChange={setView} tabs={tabs} className="!p-0.5" itemClassName="!h-8" />
        {shopDate && <p className="hidden xl:block text-xs text-ink-tertiary">{person === "all" ? "Everyone · " : person !== "me" ? `${nameOfPerson} · ` : ""}{shopDate}</p>}
        <div className="flex items-center gap-2 w-full sm:w-auto sm:ml-auto">
          {/* Staff see every choice; the ones that need a manager are greyed, never hidden. */}
          <TeklySelect value={person} onChange={(e) => onPerson(e.target.value)} aria-label="Whose tasks"
            className="glass-input h-9 py-2 px-3 text-xs rounded-xl min-w-[136px] sm:min-w-[150px]">
            <option value="me">Me</option>
            <option value="all" disabled={!isManager}>{isManager ? "Everyone" : "Everyone (managers only)"}</option>
            <option value="pool">Up for grabs</option>
            <optgroup label={isManager ? "People" : "People (managers only)"}>
              {people.map((e) => <option key={e.id} value={e.id} disabled={!isManager}>{e.name}</option>)}
            </optgroup>
          </TeklySelect>
          <div className="relative flex-1 sm:w-64 sm:flex-none">
            <M n="search" c="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" />
            <input ref={searchBox} type="search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape" && q) { e.preventDefault(); clearSearch(); } }}
              aria-label="Search all tasks" placeholder="Search all tasks…"
              className="glass-input w-full h-9 rounded-xl pl-8 pr-3 py-2 text-xs text-ink-primary" />
          </div>
        </div>
      </div>

      {(searching || missing) ? (
        <TaskResults q={qDeb} whose={pooled ? "all" : person} meId={meId} tz={tz} onOpen={onOpen} onClear={clearSearch} flash={flash}
          onChanged={() => { load(true); onChanged?.(); }} onPaged={onListPaged} reloadKey={reloadKey} note={missing ? (pooled ? MISSING_POOL_NOTE : MISSING_NOTE) : undefined} />
      ) : (<>
      {tzFailed && !tz && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card">
          <ErrorState title="Couldn't reach your shop's clock" body="Times can't be shown or saved until it loads. Check your connection and try again." onRetry={retryTz} />
        </div>
      )}
      {!tzFailed && failed && !day && <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card"><ErrorState title="Couldn't load your day" onRetry={() => load()} /></div>}
      {!day && !failed && !tzFailed && <DaySkeleton />}

      {day && everyone && (
        <EveryoneDay day={day} view={view} people={staffOrder} meId={meId} busyMove={busyMove} onMoveAll={moveAll}
          row={(t, from) => row(t, from, assigneesOf(t).length > 1)} capped={capped} onSearch={focusSearch} />
      )}

      {day && !everyone && view === "today" && (<>
        {day.catch_up.length > 0 && (
          <section aria-labelledby="catchup-h" className="bg-white rounded-2xl border border-amber-200/80 shadow-clay-card overflow-hidden">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 bg-amber-50/60 border-b border-amber-100">
              <div className="flex items-center gap-3 min-w-0">
                <span className="w-7 h-7 rounded-full bg-amber-100 text-amber-700 grid place-items-center flex-shrink-0"><M n="history" c="text-sm" /></span>
                <div className="min-w-0">
                  <h2 id="catchup-h" className="text-sm font-semibold text-ink-primary">Catch up<span className="sr-only">, {day.counts.catch_up === 1 ? "1 task" : `${day.counts.catch_up} tasks`}</span></h2>
                  <p className="text-2xs text-ink-secondary" aria-hidden="true">{day.counts.catch_up === 1 ? "1 task from earlier" : `${day.counts.catch_up} tasks from earlier`}, oldest first</p>
                </div>
              </div>
              <Button size="sm" onClick={moveAll} busy={busyMove} icon={<M n="event_available" c="text-sm" />}>Move all to today</Button>
            </div>
            <ul className="divide-y divide-slate-100">{day.catch_up.map((t) => row(t, "catch_up"))}</ul>
            {capped("catch_up")}
          </section>
        )}

        <section aria-labelledby="today-h" className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          <h2 id="today-h" className="sr-only">Today</h2>
          {day.today.length === 0 ? (
            day.catch_up.length ? <EmptyState icon={<M n="check" c="text-lg" />} title="Nothing else due today" body="Clear the catch-up list above and you're set." />
              // Nothing today but something soon: a small line that leads there, not a big empty box.
              : day.counts.upcoming > 0 ? (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3">
                  <span className="w-7 h-7 rounded-full bg-emerald-50 text-emerald-700 grid place-items-center flex-shrink-0"><M n="task_alt" c="text-sm" /></span>
                  <p className="text-sm text-ink-secondary"><span className="font-semibold text-ink-primary">{person === "me" ? "Nothing due today" : `Nothing due today for ${nameOfPerson}`}</span> · {day.counts.upcoming} coming up</p>
                  <Button variant="link" onClick={() => setView("upcoming")}>Show upcoming</Button>
                </div>
              )
              : <EmptyState icon={<M n="task_alt" c="text-lg" />} title={person === "me" ? "Nothing due today" : `Nothing due today for ${nameOfPerson}`}
                  body={person === "me" ? "Your day is clear. Add a task above when something comes up, and tasks other people give you land here too." : "Tasks due today show here as they're added."} />
          ) : <Groups groups={groupByTime(day.today)} render={(t) => row(t, "today")} />}
          {capped("today")}
        </section>

        {poolMode && pool.length > 0 && (
          <section aria-labelledby="pool-h" className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-100">
              <span className="w-7 h-7 rounded-full bg-blue-50 text-primary grid place-items-center flex-shrink-0"><M n="group" c="text-sm" /></span>
              <div className="min-w-0">
                <h2 id="pool-h" className="text-sm font-semibold text-ink-primary">Up for grabs<span className="sr-only">, {pool.length === 1 ? "1 task" : `${pool.length} tasks`}</span></h2>
                <p className="text-2xs text-ink-secondary" aria-hidden="true">Shop tasks nobody has taken yet. Take one and it moves to your day.</p>
              </div>
            </div>
            <ul className="divide-y divide-slate-100">
              {pool.slice(0, showPool ? undefined : 5).map((t) => (
                <TaskRow key={t.id} t={t} from={bucketForDue(dueOf(t) || null, today)} today={today} tz={tz || "UTC"} showWho={false} ticking={false}
                  onOpen={() => onOpen(t)} onToggle={() => undefined} onTake={() => take(t)} />
              ))}
            </ul>
            {pool.length > 5 && (
              <div className="px-4 py-2.5 border-t border-slate-100">
                <Button variant="link" onClick={() => setShowPool((v) => !v)}>{showPool ? "Show fewer" : `Show all ${pool.length}`}</Button>
              </div>
            )}
          </section>
        )}
      </>)}

      {day && !everyone && (view === "upcoming" || view === "later") && (
        <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          {day[view].length === 0
            ? (view === "upcoming" ? <EmptyState icon={<M n="calendar_today" c="text-lg" />} title="Nothing coming up this week" body="Tasks due in the next seven days show here, a day at a time." />
              : <EmptyState icon={<M n="calendar_today" c="text-lg" />} title="Nothing planned for later" body="Tasks with a later date, or no date yet, wait here." />)
            : <Groups groups={groupByDay(day[view], today)} render={(t) => row(t, view)} />}
          {capped(view)}
        </section>
      )}

      {day && !everyone && view === "done" && (
        <section aria-labelledby="done-h" className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          <h2 id="done-h" className="sr-only">Done today</h2>
          {day.done_today.length === 0
            ? <EmptyState icon={<M n="check" c="text-lg" />} title="Nothing done yet today" body="Tasks ticked off today show here." />
            : <ul className="divide-y divide-slate-100">{day.done_today.map((t) => row(t, "done_today"))}</ul>}
          {capped("done_today")}
          <p className="px-4 py-3 border-t border-slate-100 bg-slate-50/60 text-xs text-ink-tertiary">
            Older tasks: <Button variant="link" onClick={focusSearch}>use search</Button>
          </p>
        </section>
      )}
      </>)}
    </div>
  );
}

// ── Everyone, by person (managers) ───────────────────────────────────────────
// One list surface: a header line per person (who, and how their day stands), then their tasks.
// Overdue tasks lead each person's list in the late tone, so one person's load is in one place;
// "Move all to today" sits on one quiet line on top. People with nothing here fold to their header.
const EMPTY_SAID: Record<DayView, string> = { today: "All clear", upcoming: "Nothing coming up", later: "Nothing later", done: "Nothing done yet" };
const SEGMENT_SAID: Record<Exclude<DayView, "today">, string> = { upcoming: "coming up", later: "later", done: "done today" };

function EveryoneDay({ day, view, people, meId, busyMove, onMoveAll, row, capped, onSearch }: {
  day: Day; view: DayView; people: Emp[]; meId?: string; busyMove: boolean; onMoveAll: () => void;
  row: (t: Task, from: DayBucket) => JSX.Element; capped: (b: DayBucket) => JSX.Element | null; onSearch: () => void;
}) {
  const groups = groupByPerson(day, view, people);
  const [shut, setShut] = useState<Set<string>>(() => new Set());
  const toggle = (id: string) => setShut((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const late = day.counts.catch_up;
  const buckets: DayBucket[] = view === "today" ? ["catch_up", "today"] : view === "done" ? ["done_today"] : [view];
  return (
    <section aria-label="Everyone's tasks, by person" className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      {view === "today" && late > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5 border-b border-slate-100">
          <Button size="sm" onClick={onMoveAll} busy={busyMove} icon={<M n="event_available" c="text-sm" />}>Move all to today</Button>
          <p className="text-xs text-ink-secondary"><b className="font-semibold text-amber-700 tabular-nums">{late}</b> overdue across the team<span className="hidden sm:inline">, listed first under each person</span></p>
        </div>
      )}
      {groups.map((g, i) => <PersonBlock key={g.id} g={g} first={i === 0} view={view} me={g.id === meId} open={!shut.has(g.id)} onToggle={() => toggle(g.id)} row={row} />)}
      {buckets.map((b) => <div key={b}>{capped(b)}</div>)}
      {view === "done" && (
        <p className="px-4 py-3 border-t border-slate-100 bg-slate-50/60 text-xs text-ink-tertiary">
          Older tasks: <Button variant="link" onClick={onSearch}>use search</Button>
        </p>
      )}
    </section>
  );
}

function PersonBlock({ g, first, view, me, open, onToggle, row }: {
  g: PersonGroup; first: boolean; view: DayView; me: boolean; open: boolean; onToggle: () => void;
  row: (t: Task, from: DayBucket) => JSX.Element;
}) {
  const pool = g.id === POOL_ID;
  const has = g.items.length > 0;
  const shown = has && open;
  const body = `everyone-${g.id}`;
  // A folded list keeps its rows out of the Tab order and away from screen readers.
  const fold = useRef<HTMLDivElement>(null);
  useEffect(() => { if (fold.current) (fold.current as HTMLDivElement & { inert: boolean }).inert = !shown; }, [shown]);
  // A zero stays readable but quiet; a number that matters takes its tone.
  const num = (n: number, tone = "text-ink-primary") => <b className={`font-semibold tabular-nums ${n ? tone : "text-ink-tertiary"}`}>{n}</b>;
  // How this person's day stands: the day's numbers on Today, this segment's count elsewhere.
  const summary = !has
    ? <span className="text-ink-tertiary">{EMPTY_SAID[view]}{view === "today" && g.done > 0 ? <> · {num(g.done, "text-emerald-700")} done</> : null}</span>
    : pool
      ? <>{g.overdue > 0 && <>{num(g.overdue, "text-amber-700")} overdue · </>}{num(view === "today" ? g.waiting : g.items.length, "text-amber-700")} {view === "today" ? "waiting" : SEGMENT_SAID[view as Exclude<DayView, "today">]}</>
      : view === "today"
        ? <>{num(g.overdue, "text-amber-700")} overdue · {num(g.today)} today · {num(g.done, "text-emerald-700")} done</>
        : <>{num(g.items.length)} {SEGMENT_SAID[view as Exclude<DayView, "today">]}</>;
  const head = (<>
    <M n="expand_more" c={`text-base text-ink-tertiary flex-shrink-0 transition-transform duration-200 motion-reduce:transition-none ${has ? "" : "invisible"} ${shown ? "" : "-rotate-90"}`} />
    <span aria-hidden="true" className="flex-shrink-0">{pool
      ? <span className="w-7 h-7 rounded-full bg-white text-amber-700 border border-amber-200/80 grid place-items-center"><M n="group" c="text-sm" /></span>
      : g.id === FORMER_ID
        ? <span className="w-7 h-7 rounded-full bg-slate-100 text-ink-tertiary grid place-items-center"><M n="person_off" c="text-sm" /></span>
        : <Avatar name={g.name} size={28} />}</span>
    <span className="min-w-0 flex-1 sm:flex-none flex items-baseline gap-x-2">
      <span className="text-sm font-semibold text-ink-primary truncate">{g.name}{me && <span className="font-normal text-ink-tertiary"> (you)</span>}</span>
      {pool ? <span className="hidden sm:inline text-xs text-ink-tertiary truncate"><span className="sr-only">, </span>Nobody has these yet</span>
        : g.position ? <span className="text-xs text-ink-tertiary truncate"><span className="sr-only">, </span>{g.position}</span> : null}
    </span>
    <span className="basis-full sm:basis-auto sm:ml-auto pl-[68px] sm:pl-0 text-xs text-ink-secondary tabular-nums"><span className="sr-only">: </span>{summary}</span>
  </>);
  const headCls = `flex flex-wrap items-center gap-x-3 gap-y-0.5 w-full min-h-[52px] px-4 py-2 text-left ${pool ? "bg-amber-50/60" : "bg-slate-50/50"}`;
  return (
    <div className={first ? "" : "border-t border-slate-100"}>
      <h3 className="m-0">
        {has ? (
          <button type="button" onClick={onToggle} aria-expanded={shown} aria-controls={body}
            className={`${headCls} transition-colors ${pool ? "hover:bg-amber-50" : "hover:bg-slate-100/70"} focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/40`}>
            {head}
          </button>
        ) : <div className={headCls}>{head}</div>}
      </h3>
      {has && (
        <div id={body} className={`grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none ${shown ? "grid-rows-[1fr]" : "grid-rows-[0fr]"}`}>
          <div ref={fold} className="overflow-hidden">
            {/* Rows sit under the avatar column (sm and up), so each list reads as that person's. */}
            <ul className="divide-y divide-slate-100 border-t border-slate-100 sm:pl-9">{g.items.map(({ t, from }) => row(t, from))}</ul>
          </div>
        </div>
      )}
    </div>
  );
}

function Groups({ groups, render }: { groups: TaskGroup[]; render: (t: Task) => JSX.Element }) {
  return (<>
    {groups.map((g, i) => (
      <div key={g.key} className={i ? "border-t border-slate-100" : ""}>
        <h3 className="px-4 pt-3 pb-1 text-2xs font-semibold text-ink-tertiary flex items-center gap-1.5">{g.label}<span className="font-medium tabular-nums">{g.items.length}</span></h3>
        <ul className="divide-y divide-slate-100">{g.items.map(render)}</ul>
      </div>
    ))}
  </>);
}

function DaySkeleton() {
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden" role="status" aria-label="Loading your day">
      <span className="sr-only">Loading…</span>
      {[62, 48, 70, 40, 55].map((w, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3.5 border-t first:border-t-0 border-slate-100" aria-hidden="true">
          <span className="sk block w-5 h-5 rounded-full flex-shrink-0" />
          <span className="flex-1"><span className="sk block h-3 rounded" style={{ width: `${w}%` }} /><span className="sk block h-2.5 rounded mt-2" style={{ width: "22%" }} /></span>
          <span className="sk block h-3 w-12 rounded" />
        </div>
      ))}
    </div>
  );
}

// ── one row ──────────────────────────────────────────────────────────────────
function TaskRow({ t, from, today, tz, showWho, ticking, onOpen, onToggle, onUndo, onTake }: {
  t: Task; from: DayBucket; today: string; tz: string; showWho: boolean; ticking: boolean; onOpen: () => void; onToggle: (done: boolean) => void; onUndo?: () => void;
  onTake?: () => void;                  // an "Up for grabs" row: take it instead of ticking it
}) {
  const done = from === "done_today" || !!t.completed_at;
  const checked = done || ticking;
  const pending = t.id.startsWith("tmp-");
  const names = (t.assignee_names && t.assignee_names.length) ? t.assignee_names : t.assignee_name ? [t.assignee_name] : [];
  const prio = normPriority(t.priority);
  const clock = dueClock(dueOf(t));
  const when = onTake
    ? (from === "today" ? (clock ? fmtClock(clock) : "Today") : from === "catch_up" ? lateLabel(dueOf(t), today) : t.due_at ? fmtShop(t.due_at, tz, { weekday: "short", day: "numeric", month: "short" }) : "")
    : from === "catch_up" ? lateLabel(dueOf(t), today)
    : from === "done_today" ? fmtShop(t.completed_at, tz, { hour: "numeric", minute: "2-digit" })
    : clock ? fmtClock(clock) : "";
  return (
    <li className={`group relative flex items-center gap-3 px-4 py-3 transition-colors hover:bg-slate-50/80 ${pending ? "opacity-60" : ""}`}>
      {onTake ? (
        <Button size="sm" className="relative z-10 flex-shrink-0" onClick={onTake} aria-label={`Take it: ${t.title}`}>Take it</Button>
      ) : (
      <button type="button" onClick={() => onToggle(!done)} disabled={pending || ticking} aria-pressed={checked}
        aria-label={done ? `Mark "${t.title}" not done` : `Mark "${t.title}" done`}
        className={`relative z-10 hit44 w-5 h-5 rounded-full border-[1.5px] grid place-items-center flex-shrink-0 transition-colors duration-200 ${checked ? "bg-emerald-500 border-emerald-500 text-white" : "border-slate-300 bg-white hover:border-primary"}`}>
        {checked && <M n="check" c="text-xs tk-tick" />}
      </button>
      )}
      <div className="min-w-0 flex-1">
        <button type="button" onClick={onOpen} disabled={pending}
          className={`block max-w-full line-clamp-2 break-words text-left text-sm font-medium rounded after:absolute after:inset-0 after:content-[''] focus:outline-none focus-visible:after:ring-2 focus-visible:after:ring-inset focus-visible:after:ring-primary/40 transition-colors duration-300 ${checked ? "text-ink-tertiary line-through decoration-slate-300" : "text-ink-primary"}`}>
          {t.title}
        </button>
        {(t.order_id || t.client_name || isAuto(t) || (prio > 0 && !done)) && (
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-2xs text-ink-tertiary">
            {t.order_id ? (
              <Link to={`/orders?open=${encodeURIComponent(t.order_id)}`}
                className="relative z-10 inline-flex items-center gap-1 h-5 px-1.5 rounded-md bg-slate-100 text-ink-secondary font-semibold hover:bg-blue-50 hover:text-primary max-w-[240px]">
                <M n="handyman" c="text-2xs flex-shrink-0" /><span className="sr-only">Open work order </span><span className="tabular-nums whitespace-nowrap">{t.order_number || "Work order"}</span>
                {t.client_name && <span className="font-medium truncate">· {t.client_name}</span>}
              </Link>
            ) : t.client_name ? <span className="truncate">{t.client_name}</span> : null}
            {prio > 0 && !done && <Badge tone={prio === 2 ? "danger" : "warning"} size="sm">{PRIORITY_LABEL[prio]}</Badge>}
            {isAuto(t) && <span title={AUTO_HINT}><Badge tone="neutral" size="sm" className="gap-0.5"><M n="bolt" c="text-2xs" />{AUTO_LABEL}<span className="sr-only">: {AUTO_HINT}</span></Badge></span>}
          </div>
        )}
      </div>
      {onUndo && <Button variant="ghost" size="sm" className="relative z-10 flex-shrink-0" onClick={onUndo} aria-label={`Undo: mark "${t.title}" not done`}>Undo</Button>}
      {showWho && names.length > 0 && (
        <span className="hidden sm:flex gap-1 flex-shrink-0" title={names.join(", ")} aria-label={`Assigned to ${names.join(", ")}`} role="img">
          {names.slice(0, 3).map((n, i) => <Avatar key={i} name={n} size={24} />)}
          {names.length > 3 && <span className="rounded-full w-6 h-6 bg-slate-100 text-2xs font-semibold text-ink-secondary grid place-items-center">+{names.length - 3}</span>}
        </span>
      )}
      {when && <span className={`w-[72px] text-right text-xs tabular-nums flex-shrink-0 ${from === "catch_up" ? "text-amber-700 font-medium" : "text-ink-tertiary"}`}>
        <span className="sr-only">{from === "catch_up" ? "Was due " : from === "done_today" ? "Done at " : "Due "}</span>{when}</span>}
    </li>
  );
}

// ── quick add ────────────────────────────────────────────────────────────────
function QuickAdd({ today, tz, tzFailed, meId, emps, target, mayAssign, onAdd, flash, focusKey }: {
  today: string; tz: string | null; tzFailed: boolean; meId?: string; emps: Emp[]; mayAssign: boolean;
  target: AddTarget;                    // the first "Who" chip: whoever the page shows
  onAdd: (body: Record<string, any>, preview: Task, forName: string | null, key: string) => Promise<boolean>;
  flash: (msg: string) => void;
  focusKey: number;                     // bumped when the day loads empty: put the cursor here
}) {
  const [title, setTitle] = useState("");
  const [when, setWhen] = useState<QuickWhen>("today");
  const [picked, setPicked] = useState("");
  const [pickedTime, setPickedTime] = useState("");
  const [who, setWho] = useState<"me" | "others">("me");   // "me" = the target chip
  const [other, setOther] = useState("");
  // Picking someone else in the person menu points quick add at them.
  useEffect(() => { setWho("me"); setOther(""); setOtherOpen(false); }, [target.id]);
  const [job, setJob] = useState<PickedJob | null>(null);
  const [prio, setPrio] = useState<0 | 1 | 2>(0);   // Normal unless picked
  // "Pick date" and "Others" open a small panel under the chip, over the page (ChipPanel), so
  // choosing never pushes the other chips along. Clicked again, the chip closes it.
  const [pickOpen, setPickOpen] = useState(false);
  const [otherOpen, setOtherOpen] = useState(false);
  const whenBox = useRef<HTMLDivElement>(null);
  const whoBox = useRef<HTMLDivElement>(null);
  const chooseWhen = (w: QuickWhen) => { setPickOpen(w === "pick" ? !(when === "pick" && pickOpen) : false); setWhen(w); };
  const chooseWho = (w: "me" | "others") => { setOtherOpen(w === "others" ? !(who === "others" && otherOpen) : false); setWho(w); };
  const pickLabel = when === "pick" && picked
    ? `${dayLabel(picked, today)}${pickedTime ? `, ${fmtClock(pickedTime)}` : ""}`
    : "Pick date";
  const otherName = who === "others" && other ? emps.find((e) => e.id === other)?.name : undefined;
  const [jobOpen, setJobOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // One key per typed task: a retry after a timeout can't create it twice.
  const key = useRef(newKey());
  useEffect(() => { if (focusKey) input.current?.focus(); }, [focusKey]);

  const [hint, setHint] = useState("");
  // Why Add task can't run right now, said on screen (a greyed button alone says nothing on a phone).
  const blocked = !tz ? (tzFailed ? "Can't save until your shop's clock loads." : "Getting your shop's clock…") : "";
  async function save() {
    const text = title.trim();
    if (busy) return;                              // a second Enter while sending adds nothing
    if (!text) { setHint("Type what needs doing first."); input.current?.focus(); return; }
    if (blocked) { setHint(blocked); return; }
    setHint("");
    if (when === "pick" && !picked) { flash("Pick a date first"); setPickOpen(true); return; }
    if (who === "others" && !other) { flash("Choose who it's for"); setOtherOpen(true); return; }
    const assignee = who === "others" ? other : target.id;
    if (!tz || !today) return;                     // the shop's clock isn't known yet
    // A picked time is on the shop's clock: send it with the shop's offset. A date alone means the
    // end of that shop day (the server decides that).
    const due = fromShopWall(quickDue(when, picked && pickedTime ? `${picked}T${pickedTime}` : picked, today), tz);
    const body = { title: text, due_at: due, assigned_to_id: assignee, assignee_ids: assignee ? [assignee] : [], order_id: job?.id || null, priority: prio };
    const forName = who === "others" ? emps.find((e) => e.id === other)?.name || null : target.name;
    const preview: Task = {
      id: `tmp-${Date.now()}`, title: text, status: "todo", due_at: due, source: "manual", assigned_to_id: assignee, priority: prio,
      assignee_ids: body.assignee_ids, assignee_names: forName ? [forName] : [],
      order_id: job?.id || null, order_number: job?.order_number || null,
    };
    setBusy(true);
    setTitle("");
    const ok = await onAdd(body, preview, forName, key.current);
    setBusy(false);
    if (!ok) setTitle((cur) => cur || text);     // give the words back so nothing is lost
    else { setJob(null); setJobOpen(false); setPrio(0); setPickOpen(false); key.current = newKey(); }
    input.current?.focus();
  }
  const ready = !!title.trim() && !blocked;
  const onKey = (e: ReactKeyboardEvent<HTMLInputElement>) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); save(); } };

  return (
    <div className="max-sm:sticky max-sm:-top-5 max-sm:z-20 max-sm:-mx-4 max-sm:px-4 max-sm:pt-3 max-sm:pb-2 max-sm:bg-canvas">
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-2">
      <div className="flex items-center gap-2">
        <M n="add" c="ml-2 text-base text-ink-tertiary flex-shrink-0" />
        <input ref={input} value={title} onChange={(e) => { setTitle(e.target.value); setHint(""); }} onKeyDown={onKey} aria-label="Add a task" placeholder="Add a task…" enterKeyHint="done"
          aria-describedby={hint || blocked ? "quick-add-why" : undefined}
          className="flex-1 min-w-0 h-10 bg-transparent text-sm text-ink-primary placeholder:text-ink-placeholder outline-none focus-visible:outline-none" />
        {/* Greyed (the app's chip grey, same size and place), not disabled: a tap still says why
            (on screen, below), on a phone too. */}
        <Button variant={ready ? "primary" : "ghost"} onClick={save} busy={busy} aria-disabled={!ready}
          className={`flex-shrink-0 ${ready ? "" : "!bg-slate-100 !text-ink-tertiary cursor-not-allowed"}`}>Add task</Button>
      </div>
      {(hint || (blocked && title.trim())) && <p id="quick-add-why" role="status" className="px-3 pt-1 text-2xs text-ink-secondary">{hint || blocked}</p>}
      {/* On phones the choices fold away until you start typing a task (or have picked something),
          so the sticky box stays small. Only what you typed or chose opens them, never focus: tapping
          a tab elsewhere must not fold them and move the page. */}
      <div className={`flex flex-wrap items-center gap-x-3 gap-y-2 px-2 pt-2 pb-1 border-t border-slate-100 mt-2 ${title || who !== "me" || when !== "today" || prio || job || jobOpen ? "" : "max-sm:hidden"}`}>
        {/* The date and time open in a small panel under "Pick date", over the page: picking a
            day never pushes the other chips along (switching chips moves nothing). */}
        <div ref={whenBox} className="relative">
          <Tabs look="chips" label="When" value={when} onChange={chooseWhen} className="!gap-1.5"
            tabs={[{ id: "today", label: "Today" }, { id: "tomorrow", label: "Tomorrow" }, { id: "pick", label: pickLabel, icon: <M n="calendar_today" c="text-xs" /> }]} />
          <ChipPanel open={when === "pick" && pickOpen} box={whenBox} label="Due date" onClose={() => setPickOpen(false)}>
              <div className="flex items-center gap-2">
                <div className="flex-1 min-w-0"><DateField mode="date" value={picked} onChange={setPicked} label="Due date" /></div>
                <div className="w-[118px]" title="Optional: leave empty for any time that day"><DateField mode="time" value={pickedTime} onChange={setPickedTime} label="Time" placeholder="Any time" /></div>
              </div>
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={() => setPickOpen(false)}>Done</Button>
                <p className="text-2xs text-ink-tertiary">Time is optional.</p>
              </div>
          </ChipPanel>
        </div>
        <span className="hidden sm:block w-px h-5 bg-slate-200" aria-hidden="true" />
        <div ref={whoBox} className="relative">
          <Tabs look="chips" label="Who" value={who} onChange={chooseWho} className="!gap-1.5"
            tabs={[{ id: "me", label: target.label }, { id: "others", label: otherName ? otherName.split(/\s+/)[0] : "Others", disabled: !mayAssign, title: mayAssign ? undefined : "Only managers can give tasks to other people" }]} />
          <ChipPanel open={who === "others" && otherOpen} box={whoBox} label="Choose a person" onClose={() => setOtherOpen(false)}>
            <TeklySelect value={other} onChange={(e) => { setOther(e.target.value); if (e.target.value) setOtherOpen(false); }} aria-label="Who it's for" className="glass-input w-full h-9 py-2 px-3 text-sm rounded-xl">
              <option value="">Choose a person…</option>
              {emps.filter((e) => e.id !== target.id).map((e) => <option key={e.id} value={e.id}>{e.id === meId ? `${e.name} (me)` : e.name}</option>)}
            </TeklySelect>
          </ChipPanel>
        </div>
        {!mayAssign && <span className="text-2xs text-ink-tertiary">Only managers can give tasks to others.</span>}
        <span className="hidden sm:block w-px h-5 bg-slate-200" aria-hidden="true" />
        {/* Priority: one small chip (Normal by default), tinted like the row's badge once raised. */}
        <div className="relative">
          <M n="flag" c={`absolute left-2.5 top-1/2 -translate-y-1/2 text-xs pointer-events-none ${PRIO_TINT[prio]}`} />
          <TeklySelect value={prio} onChange={(e) => setPrio(Number(e.target.value) as 0 | 1 | 2)} aria-label="Task priority"
            className={`glass-input h-8 py-1.5 pl-7 pr-8 text-xs font-semibold rounded-full min-w-[112px] ${PRIO_TINT[prio]}`}>
            <option value={0}>Normal</option><option value={1}>High</option><option value={2}>Urgent</option>
          </TeklySelect>
        </div>
        <span className="hidden sm:block w-px h-5 bg-slate-200" aria-hidden="true" />
        {jobOpen || job ? (
          <div className="w-56"><JobPicker value={job} onChange={(j) => { setJob(j); if (!j) setJobOpen(false); }} /></div>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setJobOpen(true)} icon={<M n="link" c="text-xs" />}>Link a work order</Button>
        )}
      </div>
    </div>
    </div>
  );
}

/** A small panel under a quick-add chip, drawn over the page (absolute), so opening it never moves
 *  the chips around it. Closes on Escape, on a click outside the chip group, or from inside.
 *  Popovers the fields draw on <body> (the calendar) count as inside. */
function ChipPanel({ open, box, label, onClose, children }: {
  open: boolean; box: RefObject<HTMLDivElement>; label: string; onClose: () => void; children: ReactNode;
}) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (!t || box.current?.contains(t) || t.closest?.('[role="dialog"]')) return;
      closeRef.current();
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open, box]);
  const phone = usePhone();
  if (!open) return null;
  const panel = (
    <div role="dialog" aria-label={label} onKeyDown={(e) => { if (e.key === "Escape" && e.currentTarget.contains(e.target as Node)) { e.stopPropagation(); onClose(); } }}
      className={phone
        // A phone: a sheet along the bottom of the screen, so the chips above stay free to tap.
        ? "fixed inset-x-0 bottom-0 z-[60] bg-raised rounded-t-2xl border-t border-slate-200/90 shadow-clay-card px-4 pt-4 pb-[max(16px,env(safe-area-inset-bottom))] space-y-3"
        : "absolute left-0 top-full mt-2 z-30 w-[320px] bg-raised rounded-2xl border border-slate-200/90 shadow-clay-card p-3 space-y-2.5"}>
      {phone && <p className="text-xs font-semibold text-ink-secondary">{label}</p>}
      {children}
    </div>
  );
  return phone ? createPortal(panel, document.body) : panel;
}

/** Phone width (the sm breakpoint), following resizes. */
function usePhone(): boolean {
  const q = "(max-width: 639px)";
  const [on, setOn] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(q).matches);
  useEffect(() => {
    const m = window.matchMedia?.(q);
    if (!m) return;
    const f = () => setOn(m.matches);
    m.addEventListener?.("change", f);
    return () => m.removeEventListener?.("change", f);
  }, []);
  return on;
}
