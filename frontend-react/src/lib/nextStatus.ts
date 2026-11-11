// The "next step" a job is offered (drawer status menu, Tekly Go's main button): the next status
// on the shop's main path that this role may move to. A "pending" status such as Waiting parts is
// a detour some jobs take, so it's only offered when nothing else comes next. Finishing statuses
// (closed, dropped) are never the next step: finishing is its own button.
// A status of an earlier stage (an in-progress status the shop added after Ready) is a step
// back, never the next step. Waiting parts is the same stage as in progress.
export type StepStatus = { key: string; status_group?: string; sort_order?: number };

const STAGE: Record<string, number> = { new: 0, in_progress: 1, pending: 1, ready: 2, closed: 3, dropped: 3 };
const stage = (s?: StepStatus) => STAGE[s?.status_group || ""] ?? 1;

export function nextStep<T extends StepStatus>(statuses: T[], currentKey: string | undefined, allowed?: string[] | null): T | undefined {
  const ordered = [...statuses].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
  const at = ordered.findIndex((s) => s.key === currentKey);
  const ok = allowed ? new Set(allowed) : null;
  const here = at >= 0 ? stage(ordered[at]) : 0;
  const later = ordered.slice(at + 1).filter((s) => !["closed", "dropped"].includes(s.status_group || "")
    && stage(s) >= here && (!ok || ok.has(s.key)));
  return later.find((s) => s.status_group !== "pending") ?? later[0];
}
