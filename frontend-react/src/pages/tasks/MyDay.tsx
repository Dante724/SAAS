import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Link } from "react-router-dom";
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
  quickDue, bucketForDue, dayLabel, normPriority, normStatus, PRIORITY_LABEL, isAuto, dueOf, withShopTimes, splitPool, filterDay, isPool, teamLoad, AUTO_LABEL, AUTO_HINT,
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
const MISSING_NOTE = "My day isn't ready yet. Here's the full list for now.";

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
  // Up for grabs is everyone's day (managers) or my day with the pool (staff), cut down to the pool.
  const src = pooled ? (isManager ? "all" : "me") : person;
  const dayUrl = `/tasks/my-day?assignee=${encodeURIComponent(src)}${poolMode || (pooled && !isManager) ? "&include_pool=true" : ""}`;
  const split = useMemo(() => {
    if (!raw || !tz) return null;
    const wall = withShopTimes(raw, (iso) => toShopWall(iso, tz));
    return pooled ? { day: filterDay(wall, isPool), pool: [] as Task[] } : splitPool(wall, poolMode ? meId : null);
  }, [raw, tz, pooled, poolMode, meId]);
  const day = split?.day ?? null;
  const pool = split?.pool ?? [];
  // The team row (managers, any view but Me) is counted from everyone's day.
  const needTeam = isManager && person !== "me" && !missing;
  const [teamRaw, setTeamRaw] = useState<Day | null>(null);
  const teamDay = !needTeam ? null : src === "all" ? raw : teamRaw;
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
      if (e instanceof ApiError && (e.status === 403 || e.status === 404) && src !== "me") {
        // Not allowed to see that day, or that person isn't in the shop any more: back to mine.
        onPersonRef.current("me"); if (e.status === 404) flashRef.current("That person's day isn't available"); return;
      }
      // Only my own day can be a missing route (the server is older than My day).
      if (e instanceof ApiError && e.status === 404) { onUnavailable(); return; }
      if (!quiet || !dayRef.current) setFailed(true);
    });
    // One person picked: the team row above still counts everyone.
    if (isManager && person !== "me" && src !== "all") {
      api.get<Partial<Day>>("/tasks/my-day?assignee=all").then((r) => { if (id === req.current) setTeamRaw(normDay(r, "")); }).catch(() => undefined);
    }
  }, [dayUrl, src, person, isManager, missing, onUnavailable]);
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
    const scoped = (d: Day) => (pooled ? filterDay(d, isPool) : d);
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
    setDay((d) => d && replaceTask(d, t.id, { ...t, ...mine, due_wall: undefined }));   // it leaves the pool and joins my day
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
  const team = useMemo(() => {
    if (!teamDay) return null;
    const active = emps.filter((e) => e.active !== false);
    const ordered = [...active.filter((e) => e.id === meId), ...active.filter((e) => e.id !== meId)];
    return teamLoad(teamDay, ordered);
  }, [teamDay, emps, meId]);

  const row = (t: Task, from: DayBucket) => (pooled && !isManager
    ? <TaskRow key={t.id} t={t} from={from} today={today} tz={tz || "UTC"} showWho={false} ticking={false} onOpen={() => onOpen(t)} onToggle={() => undefined} onTake={() => take(t)} />
    : <TaskRow key={t.id} t={t} from={from} today={today} tz={tz || "UTC"} showWho={showWho} ticking={ticking.includes(t.id)}
      onOpen={() => onOpen(t)} onToggle={(done) => setDone(t, from, done)}
      onUndo={from === "done_today" && justDone[t.id] ? () => setDone(t, "done_today", false, justDone[t.id].from, justDone[t.id].status) : undefined} />
  );
  const focusSearch = () => searchBox.current?.focus();
  // The server sends at most 100 rows a group but counts them all: say so, and point at search.
  const capped = (b: DayBucket) => day && day.counts[b] > day[b].length
    ? <div className="px-4 py-3 border-t border-slate-100 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-ink-tertiary">
        Showing {day[b].length} of {day.counts[b]}. <Button variant="link" onClick={focusSearch}>Search</Button> finds any of them.
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
            className="glass-input h-9 py-2 px-3 text-xs rounded-xl min-w-[150px] max-sm:flex-1">
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
              aria-label="Search all tasks" placeholder="Search all tasks, incl. done…"
              className="glass-input w-full h-9 rounded-xl pl-8 pr-3 py-2 text-xs text-ink-primary" />
          </div>
        </div>
      </div>

      {team && !searching && <TeamRow team={team} meId={meId} person={person} onPick={(p) => onPerson(p === person ? "all" : p)} />}

      {(searching || missing) ? (
        <TaskResults q={qDeb} whose={person} meId={meId} tz={tz} onOpen={onOpen} onClear={clearSearch} flash={flash}
          onChanged={() => { load(true); onChanged?.(); }} onPaged={onListPaged} reloadKey={reloadKey} note={missing ? MISSING_NOTE : undefined} />
      ) : (<>
      {tzFailed && !tz && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card">
          <ErrorState title="Couldn't reach your shop's clock" body="Times can't be shown or saved until it loads. Check your connection and try again." onRetry={retryTz} />
        </div>
      )}
      {!tzFailed && failed && !day && <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card"><ErrorState title="Couldn't load your day" onRetry={() => load()} /></div>}
      {!day && !failed && !tzFailed && <DaySkeleton />}

      {day && view === "today" && (<>
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

      {day && (view === "upcoming" || view === "later") && (
        <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          {day[view].length === 0
            ? (view === "upcoming" ? <EmptyState icon={<M n="calendar_today" c="text-lg" />} title="Nothing coming up this week" body="Tasks due in the next seven days show here, a day at a time." />
              : <EmptyState icon={<M n="calendar_today" c="text-lg" />} title="Nothing planned for later" body="Tasks with a later date, or no date yet, wait here." />)
            : <Groups groups={groupByDay(day[view], today)} render={(t) => row(t, view)} />}
          {capped(view)}
        </section>
      )}

      {day && view === "done" && (
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

// ── team row (managers, Everyone) ────────────────────────────────────────────
function TeamRow({ team, meId, person, onPick }: { team: ReturnType<typeof teamLoad>; meId?: string; person: string; onPick: (p: string) => void }) {
  const card = "hit44 text-left rounded-2xl border p-3 flex flex-col gap-2 min-w-0 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40";
  return (
    <div role="group" aria-label="Team" className="grid grid-cols-2 sm:grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-2.5">
      <button type="button" onClick={() => onPick("pool")} aria-pressed={person === "pool"}
        aria-label={`Up for grabs: ${team.pool} waiting`}
        className={`${card} bg-amber-50/70 ${person === "pool" ? "border-primary ring-2 ring-primary/15" : "border-amber-200/80 hover:border-amber-300"}`}>
        <span className="flex items-center gap-2.5 min-w-0">
          <span className="w-7 h-7 rounded-full bg-white text-amber-700 grid place-items-center flex-shrink-0 border border-amber-200/80"><M n="group" c="text-sm" /></span>
          <span className="min-w-0"><span className="block text-sm font-semibold text-ink-primary truncate">Up for grabs</span><span className="block text-2xs text-ink-secondary truncate">Nobody has these yet</span></span>
        </span>
        <span className="text-xs text-ink-secondary"><b className="font-semibold text-amber-700 tabular-nums mr-1">{team.pool}</b>waiting</span>
      </button>
      {team.people.map((p) => {
        const total = p.overdue + p.today + p.done;
        return (
          <button key={p.id} type="button" onClick={() => onPick(p.id)} aria-pressed={person === p.id}
            aria-label={`${p.name}${p.id === meId ? " (you)" : ""}: ${p.overdue} overdue, ${p.today} today, ${p.done} done today`}
            className={`${card} bg-white ${person === p.id ? "border-primary ring-2 ring-primary/15" : "border-slate-200/90 hover:border-slate-300"}`}>
            <span className="flex items-center gap-2.5 min-w-0">
              <Avatar name={p.name} size={28} />
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-ink-primary truncate">{p.name}{p.id === meId && <span className="font-normal text-ink-tertiary"> (you)</span>}</span>
                <span className="block text-2xs text-ink-secondary truncate">{p.position || " "}</span>
              </span>
            </span>
            <span className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-secondary tabular-nums" aria-hidden="true">
              <span><b className={`font-semibold mr-1 ${p.overdue ? "text-amber-700" : "text-ink-primary"}`}>{p.overdue}</b>overdue</span>
              <span><b className="font-semibold text-ink-primary mr-1">{p.today}</b>today</span>
              <span><b className="font-semibold text-emerald-700 mr-1">{p.done}</b>done</span>
            </span>
            <span className="flex h-1 rounded-full bg-slate-100 overflow-hidden" aria-hidden="true">
              {total > 0 && <span className="bg-emerald-500" style={{ width: `${(p.done / total) * 100}%` }} />}
              {total > 0 && <span className="bg-amber-500" style={{ width: `${(p.overdue / total) * 100}%` }} />}
            </span>
          </button>
        );
      })}
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
  useEffect(() => { setWho("me"); setOther(""); }, [target.id]);
  const [job, setJob] = useState<PickedJob | null>(null);
  const [jobOpen, setJobOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState(false);
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
    if (when === "pick" && !picked) { flash("Pick a date first"); return; }
    if (who === "others" && !other) { flash("Choose who it's for"); return; }
    const assignee = who === "others" ? other : target.id;
    if (!tz || !today) return;                     // the shop's clock isn't known yet
    // A picked time is on the shop's clock: send it with the shop's offset. A date alone means the
    // end of that shop day (the server decides that).
    const due = fromShopWall(quickDue(when, picked && pickedTime ? `${picked}T${pickedTime}` : picked, today), tz);
    const body = { title: text, due_at: due, assigned_to_id: assignee, assignee_ids: assignee ? [assignee] : [], order_id: job?.id || null };
    const forName = who === "others" ? emps.find((e) => e.id === other)?.name || null : target.name;
    const preview: Task = {
      id: `tmp-${Date.now()}`, title: text, status: "todo", due_at: due, source: "manual", assigned_to_id: assignee,
      assignee_ids: body.assignee_ids, assignee_names: forName ? [forName] : [],
      order_id: job?.id || null, order_number: job?.order_number || null,
    };
    setBusy(true);
    setTitle("");
    const ok = await onAdd(body, preview, forName, key.current);
    setBusy(false);
    if (!ok) setTitle((cur) => cur || text);     // give the words back so nothing is lost
    else { setJob(null); setJobOpen(false); key.current = newKey(); }
    input.current?.focus();
  }
  const ready = !!title.trim() && !blocked;
  const onKey = (e: ReactKeyboardEvent<HTMLInputElement>) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); save(); } };

  return (
    <div className="max-sm:sticky max-sm:-top-5 max-sm:z-20 max-sm:-mx-4 max-sm:px-4 max-sm:pt-3 max-sm:pb-2 max-sm:bg-canvas"
      onFocus={() => setActive(true)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setActive(false); }}>
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
      {/* On phones the choices fold away until you start a task, so the sticky box stays small. */}
      <div className={`flex flex-wrap items-center gap-x-3 gap-y-2 px-2 pt-2 pb-1 border-t border-slate-100 mt-2 ${active || title || who !== "me" || when !== "today" || job || jobOpen ? "" : "max-sm:hidden"}`}>
        <Tabs look="chips" label="When" value={when} onChange={setWhen} className="!gap-1.5"
          tabs={[{ id: "today", label: "Today" }, { id: "tomorrow", label: "Tomorrow" }, { id: "pick", label: "Pick date", icon: <M n="calendar_today" c="text-xs" /> }]} />
        {when === "pick" && (<>
          <div className="w-40"><DateField mode="date" value={picked} onChange={setPicked} /></div>
          <div className="w-28" title="Optional: leave empty for any time that day"><DateField mode="time" value={pickedTime} onChange={setPickedTime} /></div>
        </>)}
        <span className="hidden sm:block w-px h-5 bg-slate-200" aria-hidden="true" />
        <Tabs look="chips" label="Who" value={who} onChange={setWho} className="!gap-1.5"
          tabs={[{ id: "me", label: target.label }, { id: "others", label: "Others", disabled: !mayAssign, title: mayAssign ? undefined : "Only managers can give tasks to other people" }]} />
        {!mayAssign && <span className="text-2xs text-ink-tertiary">Only managers can give tasks to others.</span>}
        {who === "others" && (
          <TeklySelect value={other} onChange={(e) => setOther(e.target.value)} aria-label="Who it's for" className="glass-input h-8 py-1.5 px-3 text-xs rounded-full min-w-[140px]">
            <option value="">Choose a person…</option>
            {emps.filter((e) => e.id !== target.id).map((e) => <option key={e.id} value={e.id}>{e.id === meId ? `${e.name} (me)` : e.name}</option>)}
          </TeklySelect>
        )}
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
