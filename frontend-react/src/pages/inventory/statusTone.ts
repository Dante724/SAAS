// Grounded status tones for Inventory tables.
// Principle (from the inventory design research): quiet the common case, flag only
// the exception. Normal asset states stay neutral grey; only states that need
// someone to ACT get amber (attention) or rose (critical). Fixes the legacy bug
// where every asset_status rendered as a loud amber pill.

export type Tone = { cls: string; dot: string; label: string; quiet: boolean };

const AMBER = "bg-amber-50 text-amber-700 border-amber-200/60";
const ROSE = "bg-rose-50 text-rose-700 border-rose-200/60";
const GREEN = "bg-emerald-50 text-emerald-700 border-emerald-200/60";
const NEUTRAL = "bg-slate-100 text-ink-secondary border-slate-200/70";

function titleCase(k: string): string {
  return k.replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Map a raw asset_status string to a display tone. */
export function assetStatusTone(status?: string | null): Tone {
  const k = (status || "in use").toLowerCase().replace(/_/g, " ").trim();
  const label = titleCase(k);
  // critical — the asset is gone / unusable: needs a decision
  if (/scrap|lost|retir|out of service|dead|written off|damaged/.test(k))
    return { cls: ROSE, dot: "bg-rose-500", label, quiet: false };
  // attention — blocked or waiting: needs a nudge
  if (/await|hold|on hold|parts|overdue|pending|backorder/.test(k))
    return { cls: AMBER, dot: "bg-amber-500", label, quiet: false };
  // positive action — ready to hand back
  if (/ready|collect|complete|done|finished/.test(k))
    return { cls: GREEN, dot: "bg-emerald-500", label, quiet: false };
  // normal in-progress / resting states — stay quiet
  return { cls: NEUTRAL, dot: "bg-slate-400", label, quiet: true };
}
