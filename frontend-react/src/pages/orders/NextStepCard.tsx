import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { M } from "../../components/Shell";
import Button from "../../components/Button";
import { followupsApi, closesText, reasonText, shortDay, type Followup, type FollowupStep } from "../followups/followupsApi";

// Follow-ups Phase 1 — on the job: what Tekly does next (and when), what it already sent, so
// nobody texts the customer twice. Shown only while the job has a follow-up; a server without the
// feature (404) or a job without one shows nothing at all.
type Task = { id: string; assignee_name?: string | null; due_at?: string | null };
type Flash = (m: string, tone?: "warn") => void;

const firstName = (n?: string | null) => (n || "").trim().split(/\s+/)[0] || "";

/** "done" (green), "next" (sapphire), "scheduled" (grey), "off" (skipped or cancelled). */
function dotState(s: FollowupStep, nextIndex: number | null): "done" | "next" | "scheduled" | "off" {
  if (s.status === "done" || s.status === "sent") return "done";
  if (s.status === "skipped" || s.status === "cancelled") return "off";
  return s.index === nextIndex ? "next" : "scheduled";
}
const DOT: Record<string, string> = {
  done: "bg-emerald-500", next: "bg-primary ring-4 ring-primary/15", scheduled: "bg-slate-300", off: "bg-white border border-slate-300",
};

export default function NextStepCard({ orderId, onFlash }: { orderId: string; onFlash: Flash }) {
  const [f, setF] = useState<Followup | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState<"" | "log" | "skip">("");

  const load = () => followupsApi.forOrder(orderId).then(setF)
    // silent: no follow-up API on this server (or no access) means no card — the job works as before.
    .catch(() => setF(null));
  useEffect(() => { setF(null); setTask(null); setAsking(false); load(); /* eslint-disable-next-line */ }, [orderId]);

  // Whose list the call is on: the linked task's assignee.
  const taskId = f?.state === "active" ? f.next_step?.task_id : null;
  useEffect(() => {
    if (!taskId) { setTask(null); return; }
    api.get<{ items: Task[] }>(`/tasks?order_id=${orderId}`)
      .then((r) => setTask((r.items || []).find((t) => t.id === taskId) || null))
      // silent: the card still reads fine without the person's name.
      .catch(() => setTask(null));
  }, [orderId, taskId]);

  // Only a real follow-up shows: "none", an empty or unexpected answer shows nothing.
  if (!f || !["active", "stopped", "completed"].includes(f.state)) return null;
  const steps = Array.isArray(f.steps) ? f.steps : [];

  async function logCall() {
    if (!taskId) return;
    setBusy("log");
    try { await api.patch(`/tasks/${taskId}`, { status: "done" }); onFlash("Call logged"); await load(); }
    catch (e: any) { onFlash(e?.message || "Couldn't log the call", "warn"); }
    finally { setBusy(""); }
  }
  async function skip() {
    setBusy("skip");
    try { setF(await followupsApi.skip(orderId)); setAsking(false); onFlash("Follow-ups stopped for this job"); }
    catch (e: any) { onFlash(e?.message || "Couldn't stop the follow-ups", "warn"); }
    finally { setBusy(""); }
  }

  const active = f.state === "active";
  const next = active ? f.next_step : null;
  const nextIdx = next?.index ?? null;
  const when = shortDay(next?.due_at);
  const who = firstName(task?.assignee_name);
  // "today" / "tomorrow" read as words; other days as "for Nov 20" / "on Nov 20".
  const rel = /^(today|tomorrow|yesterday)$/i.test(when);
  const line = !next ? ""
    : next.task_id
      ? `${who ? `On ${who}’s list` : "A call task is on this job"}${when ? (rel ? ` ${when.toLowerCase()}` : ` for ${when}`) : ""}.`
      : `Tekly sends it ${when ? (rel ? when.toLowerCase() : `on ${when}`) : "soon"}.`;

  return (
    <section aria-label="Follow-up" className={`rounded-2xl border p-4 space-y-3 ${active ? "border-primary/25 bg-blue-50/60" : "border-slate-200/90 bg-white shadow-clay-sm"}`}>
      <div className="space-y-1">
        <div className={`flex items-center gap-1.5 text-2xs font-semibold ${active ? "text-primary" : "text-ink-tertiary"}`}>
          <M n={active ? "autorenew" : f.state === "completed" ? "check_circle" : "block"} c="text-xs" />
          {active ? "Next step · automatic" : f.state === "completed" ? "Follow-up finished" : "Follow-up stopped"}
        </div>
        {next && <h4 className="text-sm font-semibold text-ink-primary">{next.label}</h4>}
        {active
          ? <p className="text-xs text-ink-secondary">{line} {closesText(f.preset_key)}</p>
          : f.state === "stopped" && f.stop_reason && <p className="text-xs text-ink-secondary">{reasonText(f.stop_reason)}</p>}
      </div>

      {steps.length > 0 && (
        <ol className="space-y-1.5" aria-label="Follow-up steps">
          {steps.map((s) => {
            const st = dotState(s, nextIdx);
            const date = st === "done" ? shortDay(s.at || s.due_at) : st === "off" ? (s.status === "skipped" ? "Skipped" : "Not needed") : shortDay(s.due_at);
            return (
              <li key={s.index} data-state={st} className="grid grid-cols-[14px_minmax(0,1fr)_auto] items-center gap-2 text-xs">
                <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${DOT[st]}`} />
                <span className={`min-w-0 truncate ${st === "next" ? "font-semibold text-ink-primary" : st === "off" ? "text-ink-tertiary line-through" : "text-ink-secondary"}`}>{s.label}</span>
                <span className={`tabular-nums ${st === "next" ? "font-semibold text-primary" : "text-ink-tertiary"}`}>{date}</span>
              </li>
            );
          })}
        </ol>
      )}

      {active && !asking && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" icon={<M n="call" c="text-xs" />} onClick={logCall} busy={busy === "log"} disabled={!taskId}
            title={taskId ? undefined : "Nothing to log yet: the next step is a message Tekly sends itself."}>Log call</Button>
          <Button variant="secondary" size="sm" onClick={() => setAsking(true)} disabled={!!busy}>Skip this job</Button>
          {!taskId && <span className="text-2xs text-ink-tertiary">Log call opens when a call is due.</span>}
        </div>
      )}
      {active && asking && (
        <div role="group" aria-label="Stop follow-ups for this job?" className="rounded-xl border border-slate-200 bg-white p-3 space-y-2.5">
          <p className="text-xs text-ink-primary"><b className="font-semibold">Stop follow-ups for this job?</b> No more texts or calls for this job. Texts already sent stay in the history.</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" onClick={skip} busy={busy === "skip"}>{busy === "skip" ? "Stopping…" : "Stop follow-ups"}</Button>
            <Button variant="ghost" size="sm" onClick={() => setAsking(false)} disabled={busy === "skip"}>Keep it</Button>
          </div>
        </div>
      )}
    </section>
  );
}
