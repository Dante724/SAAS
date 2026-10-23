// Today — role-aware.
//   Technician: my jobs from GET /api/tech/dashboard (overdue, today in the shop's timezone, upcoming).
//   Front desk: what customers ask about — ready for pickup first, then new and waiting jobs (board).
//   Owner: the same board with counts per stage at the top.
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertTriangle, CalendarCheck, Clock3, Pause, RefreshCw, Timer } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Card, Empty, Page, Section, SkeletonRows, StatusBadge } from "../components/ui";
import { useOnChange, useStatuses, type StatusMeta } from "../lib/useGo";
import { inferRole, savedRole, type GoRole } from "../lib/role";
import { hm, utc } from "../lib/time";
import { PRIORITY_LABEL, mergeBuckets, sortQueue } from "../lib/queue";
import { useTick, type ActiveEntry } from "../lib/useJobTimer";
import { haptic } from "../lib/scanner";
import { announceChange, useToast } from "../lib/useGo";

export type JobCard = {
  id: string; order_number: string; status: string; client?: string | null; device?: string | null;
  complaint?: string; scheduled_for?: string | null; due_at?: string | null; overdue?: boolean; priority?: number;
};

type Dash = { today: JobCard[]; upcoming: JobCard[]; overdue: JobCard[]; completed: JobCard[];
  clock: { clocked_in: boolean; since?: string | null }; shop_today?: string };
type Board = { columns: { group: string; label: string; cards: (JobCard & { client_name?: string | null; assigned_to_name?: string | null })[] }[] };

const time = (iso?: string | null) => iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
const day = (iso?: string | null) => iso ? new Date(iso).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }) : "";

export function JobRow({ job, meta, active, overdue }: { job: JobCard; meta?: StatusMeta; active?: boolean; overdue?: boolean }) {
  const when = job.scheduled_for ? `${day(job.scheduled_for)} · ${time(job.scheduled_for)}`
    : job.due_at ? `Due ${day(job.due_at)} · ${time(job.due_at)}` : "";
  const pri = job.priority ? PRIORITY_LABEL[job.priority] : "";
  return (
    <Link to={`/job/${encodeURIComponent(job.id)}`} aria-current={active ? "page" : undefined}
      className={`block rounded-2xl border p-4 shadow-clay-card transition-colors active:bg-slate-50 ${active ? "border-primary/60 bg-blue-50" : "border-slate-100 bg-white"}`}>
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[0.9375rem] font-semibold text-ink-primary">{job.order_number}</span>
          {pri && <span className={`rounded-md px-1.5 py-0.5 text-[0.75rem] font-semibold ${job.priority! >= 2 ? "bg-violet-100 text-violet-800" : "bg-rose-100 text-rose-800"}`}>{pri}</span>}
        </span>
        <StatusBadge meta={meta} statusKey={job.status} />
      </div>
      <p className="mt-2 text-[0.9375rem] font-medium text-ink-primary truncate">
        {[job.client, job.device].filter(Boolean).join(" · ") || "Walk-in"}
      </p>
      {(job.complaint || when || overdue) && (
        <p className="mt-0.5 flex items-center gap-1.5 text-[0.875rem] text-ink-secondary">
          {overdue && <span className="inline-flex items-center gap-1 font-semibold text-rose-700"><AlertTriangle size={14} />Overdue</span>}
          {when && <span className="whitespace-nowrap">{when}</span>}
          {job.complaint && <span className="truncate">{(when || overdue) ? "· " : ""}{job.complaint}</span>}
        </p>
      )}
    </Link>
  );
}

function List({ jobs, byKey, activeId, overdueIds }: { jobs: JobCard[]; byKey: Map<string, StatusMeta>; activeId?: string; overdueIds?: Set<string> }) {
  return <div className="space-y-2.5">{jobs.map((j) => <JobRow key={j.id} job={j} meta={byKey.get(j.status)} active={j.id === activeId} overdue={overdueIds?.has(j.id) || j.overdue} />)}</div>;
}

export default function Today() {
  const { me } = useMe();
  const { id: activeId } = useParams();
  const role: GoRole = savedRole() ?? inferRole(me);
  const { byKey } = useStatuses();
  const [dash, setDash] = useState<Dash | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // technician: today's job time and the running timer
  const [work, setWork] = useState<{ today_seconds: number; active: ActiveEntry; at: number } | null>(null);
  const [pausing, setPausing] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    setBusy(true); setErr(null);
    try {
      if (role === "tech") {
        api.get<{ today_seconds: number; active: ActiveEntry }>("/timeclock/my-work")
          .then((w) => setWork({ ...w, at: Date.now() })).catch(() => setWork(null));
        setDash(await api.get<Dash>("/tech/dashboard"));
      }
      else setBoard(await api.get<Board>("/orders/board"));
    } catch (e: any) {
      setErr(navigator.onLine ? (e?.message || "Couldn't load today's jobs") : "You're offline. Showing nothing saved yet.");
    } finally { setBusy(false); }
  }, [role]);
  useEffect(() => { load(); }, [load]);
  useOnChange("jobs", load);
  useEffect(() => {
    const on = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, [load]);

  useTick(!!work?.active);
  const since = work?.active ? Math.max(0, (Date.now() - work.at) / 1000) : 0;
  async function pause() {
    if (!navigator.onLine) { toast({ title: "You're offline", body: "The timer needs a connection.", tone: "warn" }); return; }
    setPausing(true);
    try { await api.post("/timeclock/stop", {}); haptic(); toast({ title: "Timer paused" }); announceChange("jobs"); }
    catch (e: any) { toast({ title: "Couldn't pause the timer", body: e?.message, tone: "error" }); }
    finally { setPausing(false); }
  }

  const first = me?.name?.split(" ")[0];
  const greeting = (() => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"; })();
  const refresh = (
    <button onClick={load} aria-label="Refresh" className="h-11 w-11 -mr-2 inline-flex items-center justify-center rounded-full text-ink-secondary active:bg-slate-100">
      <RefreshCw size={20} className={busy ? "animate-spin" : ""} />
    </button>
  );

  let body;
  if (err && !dash && !board) {
    body = <Empty icon={<AlertTriangle size={22} />} title="Couldn't load" body={err} action={<button onClick={load} className="font-semibold text-primary min-h-[2.75rem] px-4">Try again</button>} />;
  } else if (role === "tech") {
    if (!dash) body = <SkeletonRows />;
    else {
      const overdueIds = new Set(dash.overdue.map((j) => j.id));
      // My queue: overdue and today's jobs in the order to pick them up (overdue, priority, due, booking)
      const queue = sortQueue(mergeBuckets(dash.overdue, dash.today), overdueIds);
      const running = work?.active;
      body = (
        <>
          <div className="mb-5 grid grid-cols-2 gap-2.5">
            <Card className="p-4">
              <p className="text-[1.5rem] font-bold leading-none tabular-nums text-ink-primary">{work ? hm((work.today_seconds || 0) + since) : "–"}</p>
              <p className="mt-1.5 text-[0.875rem] text-ink-secondary">On jobs today</p>
            </Card>
            <Link to="/clock" className="rounded-2xl border border-slate-100 bg-white p-4 shadow-clay-card active:bg-slate-50">
              <p className="flex items-center gap-1.5 font-semibold text-ink-primary"><Clock3 size={17} className={dash.clock.clocked_in ? "text-emerald-700" : "text-ink-tertiary"} />{dash.clock.clocked_in ? "On shift" : "Clocked out"}</p>
              <p className="mt-1 text-[0.875rem] text-ink-secondary">{dash.clock.clocked_in ? `Since ${utc(dash.clock.since)?.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) ?? ""}` : "Tap to clock in"}</p>
            </Link>
          </div>
          {running && (
            <div className="mb-5 flex items-center gap-3 rounded-2xl border border-blue-200 bg-blue-50 p-3.5" role="status">
              <Timer size={20} className="text-blue-700 shrink-0" />
              <Link to={running.order_id ? `/job/${encodeURIComponent(running.order_id)}` : "/clock"} className="min-w-0 flex-1">
                <span className="block font-semibold text-blue-900 truncate">Working on {running.order_number || running.task_title || "a job"}</span>
                <span className="block text-[0.875rem] tabular-nums text-blue-900/80">{hm((running.duration_sec || 0) + since)}</span>
              </Link>
              <button onClick={async () => { await pause(); load(); }} disabled={pausing} className="min-h-[2.75rem] rounded-xl bg-white px-3.5 font-semibold text-blue-800 inline-flex items-center gap-1.5 border border-blue-200"><Pause size={16} />Pause</button>
            </div>
          )}
          <Section title="My queue" count={queue.length}>
            {queue.length ? <List jobs={queue} byKey={byKey} activeId={activeId} overdueIds={overdueIds} />
              : <Empty icon={<CalendarCheck size={22} />} title="Nothing waiting" body="Jobs assigned to you show up here, most urgent first." />}
          </Section>
          {dash.upcoming.length > 0 && <Section title="Coming up" count={dash.upcoming.length}><List jobs={dash.upcoming.slice(0, 10)} byKey={byKey} activeId={activeId} /></Section>}
        </>
      );
    }
  } else {
    if (!board) body = <SkeletonRows />;
    else {
      const col = (g: string) => board.columns.find((c) => c.group === g)?.cards ?? [];
      const norm = (cards: Board["columns"][number]["cards"]) => cards.map((c) => ({ ...c, client: c.client_name ?? c.client }));
      const order = role === "desk" ? ["ready", "new", "pending", "in_progress"] : ["ready", "in_progress", "pending", "new"];
      const title: Record<string, string> = { ready: "Ready for pickup", new: "New", pending: "Waiting", in_progress: "In progress" };
      const overdueAll = board.columns.filter((c) => !["closed", "dropped"].includes(c.group)).flatMap((c) => c.cards).filter((c) => c.overdue);
      body = (
        <>
          {role === "owner" && (
            <div className="mb-6 grid grid-cols-2 gap-2.5">
              {[["in_progress", "In progress"], ["ready", "Ready"], ["pending", "Waiting"], ["overdue", "Overdue"]].map(([g, label]) => {
                const n = g === "overdue" ? overdueAll.length : col(g).length;
                return (
                  <Card key={g} className="p-4">
                    <p className={`text-[1.75rem] font-bold tabular-nums leading-none ${g === "overdue" && n ? "text-rose-700" : "text-ink-primary"}`}>{n}</p>
                    <p className="mt-1.5 text-[0.875rem] text-ink-secondary">{label}</p>
                  </Card>
                );
              })}
            </div>
          )}
          {order.map((g) => {
            const cards = norm(col(g));
            if (!cards.length && g !== "ready") return null;
            return (
              <Section key={g} title={title[g]} count={cards.length}>
                {cards.length ? <List jobs={cards.slice(0, 25)} byKey={byKey} activeId={activeId} />
                  : <Empty icon={<CalendarCheck size={22} />} title="Nothing ready yet" body="Jobs move here when they're ready to collect." />}
              </Section>
            );
          })}
        </>
      );
    }
  }

  return (
    <Page title="Today" right={refresh}
      subtitle={<>{greeting}{first ? `, ${first}` : ""}{dash?.shop_today ? ` · ${new Date(dash.shop_today + "T12:00").toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })}` : ""}</>}>
      {err && (dash || board) && <p className="mb-3 rounded-xl bg-amber-50 border border-amber-200 px-3 py-2 text-[0.875rem] text-amber-800">{err}</p>}
      {body}
    </Page>
  );
}
