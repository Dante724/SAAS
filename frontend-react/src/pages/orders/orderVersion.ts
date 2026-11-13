// WO-03: header edits on a job (notes, schedule, people, priority, type, fields, deposit, currency)
// send `If-Match: <the job's updated_at as this screen last saw it>`. If someone else saved the
// job since, the server refuses with 409 "stale_order" instead of silently overwriting their
// change; the screen then reloads the job and says so.
//
// The version is kept per job here, so every part of the drawer shares it: the job's GET sets
// it, and every reply that saves the job (header edits, line edits, status changes) carries the
// new `updated_at`, so the drawer's own edits never trip the check (re-review N6).
import { api, ApiError } from "../../api/client";

const versions = new Map<string, string>();

export const STALE_ORDER_MESSAGE =
  "Someone else saved this job while you had it open. It has been reloaded with their changes. Make your change again.";

/** Remember the job's version from a GET or a save reply (ignored when the reply has none). */
export function rememberOrderVersion(orderId: string, version?: string | null): void {
  if (orderId && version) versions.set(orderId, version);
}

export function orderVersion(orderId: string): string | undefined {
  return versions.get(orderId);
}

export function forgetOrderVersion(orderId: string): void {
  versions.delete(orderId);
}

/** Was this error the server refusing a save on a job someone else changed? */
export function isStaleOrder(e: unknown): boolean {
  if (!(e instanceof ApiError) || e.status !== 409) return false;
  if ((e as any).stale) return true;
  try { return JSON.parse(e.body || "null")?.detail?.code === "stale_order"; } catch { return false; }
}

/** PATCH a job header field with If-Match. On success the job's new version is remembered; on a
 *  stale version it throws an ApiError whose message is STALE_ORDER_MESSAGE (callers reload). */
export async function patchOrderHeader<T = any>(orderId: string, path: string, body: unknown): Promise<T> {
  const v = versions.get(orderId);
  try {
    const r = await api.patch<T>(path, body, v ? { "If-Match": v } : undefined);
    rememberOrderVersion(orderId, (r as any)?.updated_at);
    return r;
  } catch (e) {
    if (isStaleOrder(e)) {
      forgetOrderVersion(orderId);          // the reload brings the current one
      const err = new ApiError(409, STALE_ORDER_MESSAGE);
      (err as any).stale = true;
      (err as any).body = (e as ApiError).body;
      throw err;
    }
    throw e;
  }
}
