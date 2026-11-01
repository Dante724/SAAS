// The "next step" a job is offered (drawer status menu, Tekly Go's main button): the next status
// on the shop's main path that this role may move to. A "pending" status such as Waiting parts is
// a detour some jobs take, so it's only offered when nothing else comes next. Finishing statuses
// (closed, dropped) are never the next step: finishing is its own button.
export type StepStatus = { key: string; status_group?: string; sort_order?: number };

export function nextStep<T extends StepStatus>(statuses: T[], currentKey: string | undefined, allowed?: string[] | null): T | undefined {
  const ordered = [...statuses].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
  const at = ordered.findIndex((s) => s.key === currentKey);
  const ok = allowed ? new Set(allowed) : null;
  const later = ordered.slice(at + 1).filter((s) => !["closed", "dropped"].includes(s.status_group || "") && (!ok || ok.has(s.key)));
  return later.find((s) => s.status_group !== "pending") ?? later[0];
}
