// "You were just looking at…": the last work order this person opened in this browser, for
// quick add to offer as a one-tap link. A per-viewer convenience only (localStorage, per user,
// ten minutes); storage can be off or full, so every read and write is allowed to fail quietly.

export type RecentJob = { id: string; number: string | null; client: string | null; device: string | null; at: number };

export const RECENT_JOB_TTL = 10 * 60 * 1000;
const key = (userId: string) => `tekly.recentJob.${userId}`;

/** Remember a work order the person just opened (the drawer calls this). */
export function rememberJob(userId: string | null | undefined, job: Omit<RecentJob, "at">, now = Date.now()): void {
  if (!userId || !job.id) return;
  try { localStorage.setItem(key(userId), JSON.stringify({ ...job, at: now })); } catch { /* storage off */ }
}

/** The job opened in the last ten minutes, or null (an older one is cleared). */
export function recentJob(userId: string | null | undefined, now = Date.now()): RecentJob | null {
  if (!userId) return null;
  try {
    const raw = localStorage.getItem(key(userId));
    if (!raw) return null;
    const j = JSON.parse(raw) as RecentJob;
    if (!j || typeof j.id !== "string" || typeof j.at !== "number" || now - j.at > RECENT_JOB_TTL || j.at > now + 60_000) {
      localStorage.removeItem(key(userId));
      return null;
    }
    return j;
  } catch { return null; }
}

/** Forget it (the person can't open that job any more). */
export function forgetJob(userId: string | null | undefined, id?: string): void {
  if (!userId) return;
  try {
    const cur = recentJob(userId);
    if (!id || !cur || cur.id === id) localStorage.removeItem(key(userId));
  } catch { /* storage off */ }
}
