// Changing a job's status from the phone: show the new status at once, then confirm with the server.
// If the server refuses, put the old status back and say why in plain words. Offline (or off the
// shop network) the change is queued and the new status stays on screen, marked as waiting to sync.
import type { MutateResult } from "./offlineQueue";
import { readableError } from "../../api/client";

export type StatusOutcome =
  | { kind: "saved"; data: any }
  | { kind: "queued"; held: boolean }
  | { kind: "refused"; title: string; message: string };

type Mutate = (method: string, url: string, body?: unknown) => Promise<MutateResult>;

/** Plain-words reason for a refused status change. 409 = a gate on the target status. */
export function refusal(status: number, data: any, targetName: string): { title: string; message: string } {
  const detail = typeof data?.detail === "string" ? data.detail
    : data ? readableError(JSON.stringify(data)) : "";
  if (status === 409) {
    return { title: `Can't move to ${targetName} yet`, message: detail || "Something on the job needs finishing first." };
  }
  if (status === 403) return { title: "Not allowed", message: detail || "Your role can't make this status change." };
  if (status === 401) return { title: "Signed out", message: "Sign in again, then try once more." };
  if (status === 404) return { title: "Job not found", message: "It may have been archived or moved to another shop." };
  return { title: "Couldn't change the status", message: detail || "Please try again." };
}

export async function changeStatus(opts: {
  orderId: string;
  from: string;
  to: string;
  toName: string;
  apply: (key: string) => void;
  mutate: Mutate;
}): Promise<StatusOutcome> {
  const { orderId, from, to, toName, apply, mutate } = opts;
  apply(to);
  let r: MutateResult;
  try {
    r = await mutate("PATCH", `/api/orders/${encodeURIComponent(orderId)}/status`, { status: to });
  } catch {
    apply(from);
    return { kind: "refused", ...refusal(0, null, toName) };
  }
  if (r.ok && r.queued) return { kind: "queued", held: !!r.held };
  if (r.ok) return { kind: "saved", data: r.data };
  apply(from);
  return { kind: "refused", ...refusal(r.status, r.data, toName) };
}
