// The job timer from a job's point of view: is my timer on this job, on another job, or off; how
// long has this job been worked on in total. "Pause" stops the running entry (the server has no
// pause); starting again opens a new entry, and the job's total adds them all up. Starting while
// another job's timer runs switches it (one server call, so no gap or overlap).
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { haptic } from "./scanner";
import { announceChange, useToast } from "./useGo";

export type ActiveEntry = { order_id?: string | null; order_number?: string | null; task_title?: string | null; started_at: string; duration_sec: number } | null;

export function useTick(active: boolean) {
  const [, set] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => set((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

export function useJobTimer(orderId: string, enabled = true) {
  const toast = useToast();
  const [active, setActive] = useState<ActiveEntry | undefined>(undefined);
  const [tracked, setTracked] = useState(0);         // finished seconds on this job (all people)
  const [loadedAt, setLoadedAt] = useState(Date.now());
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!enabled || !orderId) return;
    const [a, t] = await Promise.all([
      api.get<{ active: ActiveEntry }>("/timeclock/active").then((r) => r.active).catch(() => null),
      api.get<{ tracked_sec: number }>(`/timeclock/order/${encodeURIComponent(orderId)}`).then((r) => r.tracked_sec || 0).catch(() => 0),
    ]);
    setActive(a); setTracked(t); setLoadedAt(Date.now());
  }, [orderId, enabled]);
  useEffect(() => { load(); }, [load]);

  const onThis = !!active && active.order_id === orderId;
  useTick(onThis);
  const since = onThis ? Math.max(0, (Date.now() - loadedAt) / 1000) : 0;
  // tracked_sec already includes the running entry's time up to when it was loaded
  const totalSec = tracked + since;
  const runningSec = onThis && active ? active.duration_sec + since : 0;

  async function act(path: string, body: unknown, title: string) {
    if (!navigator.onLine) { toast({ title: "You're offline", body: "The timer needs a connection so the time is right.", tone: "warn" }); return; }
    setBusy(true);
    try { await api.post(path, body); haptic(); toast({ title }); announceChange("jobs"); await load(); }
    catch (e: any) { toast({ title: "Couldn't change the timer", body: e?.message, tone: "error" }); }
    finally { setBusy(false); }
  }

  return {
    active, onThis, otherJob: !!active && !onThis ? active : null, totalSec, runningSec, busy, reload: load,
    start: (label: string) => act(active ? "/timeclock/switch" : "/timeclock/start", active ? { order_id: orderId } : { order_id: orderId, category: "job" }, active ? `Switched to ${label}` : `Timer started on ${label}`),
    pause: () => act("/timeclock/stop", {}, "Timer paused"),
  };
}
