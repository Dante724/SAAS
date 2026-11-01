// Today — role-aware.
//   Technician: my jobs from GET /api/tech/dashboard (overdue, today in the shop's timezone, upcoming).
//   Front desk: what customers ask about — ready for pickup first, then new and waiting jobs (board).
//   Owner: the same board with counts per stage at the top.
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertTriangle, ChevronRight, Clock3, Pause, PhoneIncoming, RefreshCw, Timer } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Empty, Group, Page, QuietRow, Section, SkeletonRows, StatusDot } from "../components/ui";
import { useOnChange, useStatuses, type StatusMeta } from "../lib/useGo";
import { inferRole, savedRole, type GoRole } from "../lib/role";
import { hm, utc } from "../lib/time";
import { PRIORITY_LABEL, mergeBuckets, sortQueue } from "../lib/queue";
import { useTick, type ActiveEntry } from "../lib/useJobTimer";
import { haptic } from "../lib/scanner";
import { announceChange, useToast } from "../lib/useGo";
import { mixesStatuses, rowDetail, rowTitle, rowWhen } from "../lib/rows";

export type JobCard = {
  id: string; order_number: string; status: string; client?: string | null; device?: string | null;
  complaint?: string; scheduled_for?: string | null; due_at?: string | null; overdue?: boolean; priority?: number;
};

type Dash = { today: JobCard[]; upcoming: JobCard[]; overdue: JobCard[]; completed: JobCard[];
  clock: { clocked_in: boolean; since?: string | null }; shop_today?: string };
type Board = { columns: { group: string; label: string; cards: (JobCard & { client_name?: string | null; assigned_to_name?: string | null })[] }[] };

/** One 64px row: customer + promised time (red when late) / Device · Problem + WO number. */
export function JobRow({ job, meta, active, overdue, showStatus }: { job: JobCard; meta?: StatusMeta; active?: boolean; overdue?: boolean; showStatus?: boolean }) {
  const when = rowWhen({ ...job, overdue: overdue || job.overdue });
  const detail = rowDetail(job);
  const pri = job.priority ? PRIORITY_LABEL[job.priority] : "";
  return (
    <Link to={`/job/${encodeURIComponent(job.id)}`} aria-current={active ? "page" : undefined}
      className={`flex min-h-16 flex-col justify-center gap-0.5 px-4 py-2.5 transition-colors active:bg-slate-100 ${active ? "bg-blue-50" : ""}`}>
      <span className="flex items-baseline gap-3">
        <span className="min-w-0 flex-1 flex items-baseline gap-2">
          <span className="truncate text-go-body font-semibold text-ink-primary">{rowTitle(job)}</span>
          {pri && <span className={`shrink-0 text-go-caption font-semibold ${job.priority! >= 2 ? "text-violet-700" : "text-rose-700"}`}>{pri}</span>}
        </span>
        {when && <span className={`shrink-0 text-go-sub tabular-nums ${when.late ? "font-semibold text-rose-700" : "text-ink-secondary"}`}>{when.text}</span>}
      </span>
      <span className="flex items-baseline gap-3">
        <span className="min-w-0 flex-1 flex items-baseline gap-2 text-go-sub text-ink-secondary">
          {showStatus && <StatusDot meta={meta} statusKey={job.status} />}
          <span className="truncate">{detail || (showStatus ? "" : "No device or problem yet")}</span>
        </span>
        <span className="shrink-0 font-mono text-go-caption text-ink-tertiary">{job.order_number}</span>
      </span>
    </Link>
  );
}

function List({ jobs, byKey, activeId, overdueIds }: { jobs: JobCard[]; byKey: Map<string, StatusMeta>; activeId?: string; overdueIds?: Set<string> }) {
  const mixed = mixesStatuses(jobs);
  return <Group>{jobs.map((j) => <JobRow key={j.id} job={j} meta={byKey.get(j.status)} active={j.id === activeId} overdue={overdueIds?.has(j.id) || j.overdue} showStatus={mixed} />)}</Group>;
}

/** Front desk / owner: one row to the open inquiries, with how many are late. Hidden when none. */
function InquiriesRow({ tick }: { tick: number }) {
  const [n, setN] = useState<{ open: number; late: number } | null>(null);
  useEffect(() => {
    let live = true;
    api.get<{ items: { sla_overdue?: boolean }[]; total: number }>("/leads?open_only=true&page=1&page_size=100")
      .then((r) => { if (live) setN({ open: r.total ?? r.items.length, late: (r.items || []).filter((i) => i.sla_overdue).length }); })
      .catch(() => { if (live) setN(null); });
    return () => { live = false; };
  }, [tick]);
  if (!n?.open) return null;
  return (
    <Group className="mb-6">
      <Link to="/inquiries" className="min-h-[2.75rem] flex items-center gap-3 px-4 py-2.5 active:bg-slate-100">
        <PhoneIncoming size={18} className={n.late ? "text-rose-700" : "text-ink-tertiary"} />
        <span className="flex-1 text-go-body text-ink-primary">Inquiries</span>
        <span className="text-go-body tabular-nums text-ink-secondary">
          {n.open} open{n.late ? <span className="font-semibold text-rose-700"> · {n.late} late</span> : null}
        </span>
        <ChevronRight size={18} className="text-ink-tertiary" />
      </Link>
    </Group>
  );
}

const goTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

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
  const [tick, setTick] = useState(0);

  const load = useCallback(async () => {
    setBusy(true); setErr(null); setTick((t) => t + 1);
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
          <Group className="mb-6">
            <Link to="/clock" className="min-h-[2.75rem] flex items-center gap-3 px-4 py-2.5 active:bg-slate-100">
              <Clock3 size={18} className={dash.clock.clocked_in ? "text-emerald-700" : "text-ink-tertiary"} />
              <span className="flex-1 text-go-body text-ink-primary">{dash.clock.clocked_in ? "On shift" : "Clocked out"}</span>
              <span className="text-go-body tabular-nums text-ink-secondary">{dash.clock.clocked_in ? `Since ${utc(dash.clock.since)?.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) ?? ""}` : "Clock in"}</span>
              <ChevronRight size={18} className="text-ink-tertiary" />
            </Link>
            {work && (work.today_seconds || running) ? (
              <div className="min-h-[2.75rem] flex items-center gap-3 px-4 py-2.5">
                <Timer size={18} className="text-ink-tertiary" />
                <span className="flex-1 text-go-body text-ink-primary">On jobs today</span>
                <span className="text-go-body tabular-nums text-ink-secondary">{hm((work.today_seconds || 0) + since)}</span>
              </div>
            ) : null}
          </Group>
          {running && (
            <div className="mb-6 flex items-center gap-3 rounded-xl border-[0.5px] border-blue-200 bg-blue-50 px-4 py-2.5" role="status">
              <Timer size={20} className="text-blue-700 shrink-0" />
              <Link to={running.order_id ? `/job/${encodeURIComponent(running.order_id)}` : "/clock"} className="min-w-0 flex-1">
                <span className="block font-semibold text-blue-900 truncate">Working on {running.order_number || running.task_title || "a job"}</span>
                <span className="block text-go-sub tabular-nums text-blue-900/80">{hm((running.duration_sec || 0) + since)}</span>
              </Link>
              <button onClick={async () => { await pause(); load(); }} disabled={pausing} className="min-h-[2.75rem] rounded-xl bg-white px-3.5 text-go-body font-semibold text-blue-800 inline-flex items-center gap-1.5 border-[0.5px] border-blue-200"><Pause size={16} />Pause</button>
            </div>
          )}
          <Section title="My queue" count={queue.length}>
            {queue.length ? <List jobs={queue} byKey={byKey} activeId={activeId} overdueIds={overdueIds} />
              : <QuietRow>Nothing waiting. Jobs assigned to you show up here, most urgent first.</QuietRow>}
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
      // one strip of counts; a zero is noise, so zero stages (and an all-zero strip) are hidden
      const strip = ([["in_progress", "In progress"], ["ready", "Ready"], ["pending", "Waiting"]] as const)
        .map(([g, label]) => [g, label, col(g).length] as const).filter(([, , n]) => n > 0);
      body = (
        <>
          <InquiriesRow tick={tick} />
          {role === "owner" && strip.length > 0 && (
            <div className="mb-6 flex rounded-xl border-[0.5px] border-slate-200 bg-white divide-x-[0.5px] divide-slate-200" role="group" aria-label="Jobs by stage">
              {strip.map(([g, label, n]) => (
                <button key={g} onClick={() => goTo(`go-sec-${g}`)} className="flex-1 min-w-0 px-4 py-2.5 text-left active:bg-slate-100 first:rounded-l-xl last:rounded-r-xl">
                  <span className="block text-go-title font-semibold tabular-nums text-ink-primary">{n}</span>
                  <span className="block truncate text-go-caption text-ink-secondary">{label}</span>
                </button>
              ))}
            </div>
          )}
          {order.map((g) => {
            const cards = norm(col(g));
            if (!cards.length && g !== "ready") return null;
            return (
              <Section key={g} id={`go-sec-${g}`} title={title[g]} count={cards.length || undefined}>
                {cards.length ? <List jobs={cards.slice(0, 25)} byKey={byKey} activeId={activeId} />
                  : <QuietRow>Nothing ready yet. Jobs move here when they're ready to collect.</QuietRow>}
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
      {err && (dash || board) && <p className="mb-3 rounded-xl bg-amber-50 border-[0.5px] border-amber-200 px-3 py-2 text-go-sub text-amber-800">{err}</p>}
      {body}
    </Page>
  );
}
