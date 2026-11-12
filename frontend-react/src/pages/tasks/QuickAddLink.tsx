import { useEffect, useMemo, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { api, ApiError } from "../../api/client";
import type { PickedJob } from "../../components/JobPicker";
import { recentJob, forgetJob, RECENT_JOB_TTL, type RecentJob } from "../../lib/recentJob";
import { findJobClue, answersClue, statusText, type JobClue } from "./jobClue";

// Quick add's two ways to link a work order without opening the picker:
//  · a "Link to?" strip under the box when what you type names a job (number, phone or customer),
//  · a "you were just looking at" chip for the job opened in the last ten minutes.
// Both only offer: nothing is linked until the person picks it.

export type JobHit = { id: string; order_number: string | null; client_name: string | null; device: string | null; status: string | null; open: boolean };
const MAX = 3;
const DEBOUNCE = 250;

type OrderRow = { id: string; order_number?: string | null; status?: string | null; closed_at?: string | null;
  client?: { name?: string | null } | null; device?: { brand?: string | null; model?: string | null } | null };
const toHit = (o: OrderRow): JobHit => ({
  id: o.id, order_number: o.order_number || null, client_name: o.client?.name || null,
  device: [o.device?.brand, o.device?.model].filter(Boolean).join(" ") || null,
  status: o.closed_at ? "Closed" : statusText(o.status) || null, open: !o.closed_at,
});

/** Suggestions for what is being typed: the work-order search (GET /orders?q=, which already keeps
 *  to what this person may open), debounced, stale answers dropped, open jobs first, three at most. */
export function useJobSuggest(title: string, linked: boolean) {
  const clue = useMemo(() => findJobClue(title), [title]);
  const [hits, setHits] = useState<JobHit[]>([]);
  const [forTerm, setForTerm] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const term = clue && !linked && clue.term !== dismissed ? clue.term : null;
  const clueRef = useRef<JobClue | null>(clue);
  clueRef.current = clue;
  useEffect(() => {
    setActive(0);
    if (!term) { setHits([]); setForTerm(null); return; }
    const ctl = new AbortController();
    const h = setTimeout(() => {
      api.get<{ items?: OrderRow[] }>(`/orders?q=${encodeURIComponent(term)}&limit=5`, { signal: ctl.signal })
        .then((r) => {
          if (ctl.signal.aborted) return;
          const c = clueRef.current;
          const found = (r.items || []).map(toHit).filter((j) => c && answersClue(c, j));
          found.sort((a, b) => Number(b.open) - Number(a.open));   // stable: newest first within each
          setHits(found.slice(0, MAX)); setForTerm(term);
        })
        .catch(() => { if (!ctl.signal.aborted) { setHits([]); setForTerm(term); } });
    }, DEBOUNCE);
    return () => { clearTimeout(h); ctl.abort(); };
  }, [term]);
  const items = term && forTerm === term ? hits : [];
  return {
    clue, items, open: items.length > 0, active: Math.min(active, Math.max(0, items.length - 1)), setActive,
    /** Esc: no more suggestions for this clue (a new clue brings them back). */
    dismiss: () => { if (clue) setDismissed(clue.term); },
  };
}

/** The job opened in the last ten minutes, checked with the server before it is offered (a job
 *  the person can no longer open is dropped quietly), and gone when it expires. */
export function useRecentJob(userId: string | undefined) {
  const [job, setJob] = useState<RecentJob | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const again = () => setTick((n) => n + 1);
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => { window.removeEventListener("focus", again); document.removeEventListener("visibilitychange", again); };
  }, []);
  useEffect(() => {
    const r = recentJob(userId);
    if (!r) { setJob(null); return; }
    let live = true;
    // Expire on time even if nothing else re-renders.
    const h = setTimeout(() => { if (live) setJob(null); }, Math.max(0, r.at + RECENT_JOB_TTL - Date.now()));
    api.get(`/orders/${encodeURIComponent(r.id)}`)
      .then(() => { if (live) setJob(r); })
      .catch((e) => {
        if (!live) return;
        if (e instanceof ApiError && (e.status === 403 || e.status === 404)) forgetJob(userId, r.id);
        setJob(null);
      });
    return () => { live = false; clearTimeout(h); };
  }, [userId, tick]);
  /** One tap: link it at once, and check again behind the scenes; a job that has become off-limits
   *  is unlinked and forgotten without a fuss. */
  const link = (set: (j: PickedJob | null) => void) => {
    if (!job) return;
    const j = job;
    set({ id: j.id, order_number: j.number });
    api.get(`/orders/${encodeURIComponent(j.id)}`).catch((e) => {
      if (e instanceof ApiError && (e.status === 403 || e.status === 404)) {
        forgetJob(userId, j.id); setJob(null);
        set(null);
      }
    });
  };
  return { job, link };
}

/** The "Link to?" strip: a listbox the task box controls (aria-activedescendant). Drawn over the
 *  page under the box, so the chips and the rows below never move. */
export function SuggestStrip({ id, items, active, onHover, onPick }: {
  id: string; items: JobHit[]; active: number; onHover: (i: number) => void; onPick: (j: JobHit) => void;
}) {
  return (
    <div className="absolute left-0 right-0 top-full mt-1.5 z-30 bg-raised rounded-xl border border-slate-200/90 shadow-clay-card overflow-hidden">
      <p className="flex items-center gap-2 px-3 pt-2 pb-1 text-2xs text-ink-tertiary">
        <span className="font-semibold text-ink-secondary">Link to a work order?</span>
        <span className="hidden sm:inline ml-auto">Tab to link · Esc to dismiss</span>
      </p>
      <ul id={id} role="listbox" aria-label="Work orders that match" className="pb-1">
        {items.map((j, i) => (
          <li key={j.id} id={`${id}-${i}`} role="option" aria-selected={i === active}
            onMouseEnter={() => onHover(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => onPick(j)}
            className={`flex items-center gap-2.5 h-9 px-3 cursor-pointer text-xs transition-colors ${i === active ? "bg-primary/[0.06]" : "hover:bg-slate-50"}`}>
            <M n="handyman" c={`text-xs flex-shrink-0 ${i === active ? "text-primary" : "text-ink-tertiary"}`} />
            <span className="font-semibold tabular-nums text-ink-primary whitespace-nowrap">{j.order_number || "Work order"}</span>
            <span className="min-w-0 truncate text-ink-secondary">{[j.client_name, j.device].filter(Boolean).join(" · ")}</span>
            {j.status && <span className={`ml-auto flex-shrink-0 h-5 px-1.5 rounded-md text-2xs font-medium inline-flex items-center ${j.open ? "bg-slate-100 text-ink-secondary" : "bg-slate-50 text-ink-tertiary"}`}>{j.status}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
