// Clock: the shop attendance clock (shift in/out) and the job timer, each shown only when the
// company has it turned on. Clock actions need a connection — the server stamps the time, so a
// queued clock-in replayed later would record the wrong time.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Clock3, Timer } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Card, Empty, Page, PrimaryButton, SkeletonRows } from "../components/ui";
import { useToast } from "../lib/useGo";
import { haptic } from "../lib/scanner";
import { hm, utc } from "../lib/time";

type Shift = { started_at: string; elapsed_sec: number } | null;
type Entry = { order_id?: string | null; order_number?: string | null; task_title?: string | null; category: string; started_at: string; duration_sec: number } | null;

function useTick(active: boolean) {
  const [, set] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => set((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

export default function Clock() {
  const { me } = useMe();
  const toast = useToast();
  const attendance = me?.clock_attendance_enabled !== false;
  const jobTimer = me?.clock_wo_enabled !== false;
  const [shift, setShift] = useState<Shift | undefined>(undefined);
  const [entry, setEntry] = useState<Entry | undefined>(undefined);
  const [loadedAt, setLoadedAt] = useState(Date.now());
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [s, e] = await Promise.all([
      attendance ? api.get<{ shift: Shift }>("/timeclock/shift/active").then((r) => r.shift).catch(() => null) : Promise.resolve(null),
      jobTimer ? api.get<{ active: Entry }>("/timeclock/active").then((r) => r.active).catch(() => null) : Promise.resolve(null),
    ]);
    setShift(s); setEntry(e); setLoadedAt(Date.now());
  }, [attendance, jobTimer]);
  useEffect(() => { load(); }, [load]);
  useTick(!!shift || !!entry);
  const since = (Date.now() - loadedAt) / 1000;

  async function act(key: string, path: string, body: unknown, done: string) {
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Clocking needs a connection so the time is right.", tone: "warn" }); return; }
    setBusy(key);
    try { await api.post(path, body); haptic(); toast({ title: done }); await load(); }
    catch (e: any) { toast({ title: "Couldn't do that", body: e?.message, tone: "error" }); }
    finally { setBusy(null); }
  }

  const clock = (iso?: string) => utc(iso)?.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) ?? "";
  const loading = shift === undefined || entry === undefined;

  return (
    <Page title="Clock" subtitle={me?.name}>
      {!attendance && !jobTimer && <Empty icon={<Clock3 size={22} />} title="Clocks are off" body="Your company doesn't use the time clock." />}
      {loading && (attendance || jobTimer) && <SkeletonRows n={2} />}

      {!loading && attendance && (
        <Card className="p-5 mb-4">
          <div className="flex items-center gap-3">
            <span className={`flex h-11 w-11 items-center justify-center rounded-xl ${shift ? "bg-emerald-50 text-emerald-700" : "bg-slate-50 text-ink-tertiary"}`}><Clock3 size={22} /></span>
            <div>
              <p className="font-semibold text-ink-primary">Shift</p>
              <p className="text-go-sub text-ink-secondary">{shift ? `Clocked in at ${clock(shift.started_at)}` : "You're clocked out"}</p>
            </div>
          </div>
          {shift && <p className="mt-4 text-go-display font-bold leading-none tabular-nums tracking-tight text-ink-primary">{hm(shift.elapsed_sec + since)}</p>}
          <PrimaryButton className={`mt-5 ${shift ? "!bg-ink-primary" : ""}`} disabled={!!busy}
            onClick={() => shift ? act("shift", "/timeclock/shift/clock-out", undefined, "Clocked out") : act("shift", "/timeclock/shift/clock-in", undefined, "Clocked in")}>
            {busy === "shift" ? "…" : shift ? "Clock out" : "Clock in"}
          </PrimaryButton>
        </Card>
      )}

      {!loading && jobTimer && (
        <Card className="p-5">
          <div className="flex items-center gap-3">
            <span className={`flex h-11 w-11 items-center justify-center rounded-xl ${entry ? "bg-blue-50 text-blue-700" : "bg-slate-50 text-ink-tertiary"}`}><Timer size={22} /></span>
            <div className="min-w-0">
              <p className="font-semibold text-ink-primary">Job timer</p>
              <p className="text-go-sub text-ink-secondary truncate">
                {entry ? <>Since {clock(entry.started_at)}{entry.order_id ? <> on <Link className="font-semibold text-primary" to={`/job/${encodeURIComponent(entry.order_id)}`}>{entry.order_number}</Link></> : entry.task_title ? ` on ${entry.task_title}` : ""}</> : "Not running"}
              </p>
            </div>
          </div>
          {entry && <p className="mt-4 text-go-display font-bold leading-none tabular-nums tracking-tight text-ink-primary">{hm(entry.duration_sec + since)}</p>}
          {entry
            ? <PrimaryButton className="mt-5 !bg-ink-primary" disabled={!!busy} onClick={() => act("timer", "/timeclock/stop", {}, "Timer stopped")}>{busy === "timer" ? "…" : "Stop timer"}</PrimaryButton>
            : <p className="mt-4 text-go-sub text-ink-secondary">Start the timer from a job so the time lands on it.</p>}
        </Card>
      )}
    </Page>
  );
}
