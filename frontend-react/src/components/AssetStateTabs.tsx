import { useState } from "react";

/** 19 — Active / Written off / All on asset lists, with a count on each and the choice
 * remembered on this device. Counts come from the list response (`counts`). */
export type AssetState = "active" | "written_off" | "all";
export type AssetCounts = { active: number; written_off: number; all: number };

const KEY = "tekly.assets.state";
const OPTIONS: { id: AssetState; label: string }[] = [
  { id: "active", label: "Active" }, { id: "written_off", label: "Written off" }, { id: "all", label: "All" },
];

export function readAssetState(): AssetState {
  try {
    const v = localStorage.getItem(KEY);
    return v === "written_off" || v === "all" ? v : "active";
  } catch { return "active"; }
}

export function useAssetState(): [AssetState, (s: AssetState) => void] {
  const [state, setState] = useState<AssetState>(readAssetState);
  const set = (s: AssetState) => { setState(s); try { localStorage.setItem(KEY, s); } catch { /* private mode */ } };
  return [state, set];
}

export default function AssetStateTabs({ value, onChange, counts }: {
  value: AssetState; onChange: (s: AssetState) => void; counts?: AssetCounts | null;
}) {
  return (
    <div role="tablist" aria-label="Show assets" className="inline-flex p-0.5 rounded-lg bg-slate-100 border border-slate-200">
      {OPTIONS.map((o) => {
        const on = value === o.id;
        return (
          <button key={o.id} role="tab" aria-selected={on} type="button" onClick={() => onChange(o.id)}
            className={`px-2.5 py-1 rounded-md text-xs font-semibold flex items-center gap-1.5 transition-colors ${on ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>
            {o.label}
            {counts && <span className={`tabular-nums text-[11px] ${on ? "text-primary" : "text-ink-tertiary"}`}>{counts[o.id]}</span>}
          </button>
        );
      })}
    </div>
  );
}
