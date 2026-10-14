import { useState } from "react";
import { SETUP, type SectionId } from "./settingsRegistry";

// Settings → Overview activation checklist. Self-contained (no Shell/api deps) so it
// renders in isolation and is cheap to unit-test (SetupChecklist.test.tsx).
export function SetupChecklist({ goSection }: { goSection: (s: SectionId) => void }) {
  const [done, setDone] = useState<Record<string, boolean>>({ general: true, team: true });
  const n = SETUP.filter((x) => done[x.id]).length;
  const pct = Math.round((n / SETUP.length) * 100);
  return (
    <div className="rounded-2xl border border-slate-200/90 bg-gradient-to-b from-blue-50/60 to-transparent p-5">
      <h3 className="text-[15px] font-extrabold text-ink-primary">Finish setting up your shop</h3>
      <div className="text-xs text-ink-tertiary mb-3">{n} of {SETUP.length} done · a few minutes to go</div>
      <div className="h-2 rounded-full bg-indigo-100 overflow-hidden mb-3.5"><div className="h-full bg-primary transition-all" style={{ width: pct + "%" }} /></div>
      {SETUP.map((x, i) => (
        <div key={x.id} role="button" aria-label={x.t} aria-pressed={!!done[x.id]} onClick={() => setDone((s) => ({ ...s, [x.id]: !s[x.id] }))} className={`flex items-center gap-3 py-2.5 cursor-pointer ${i ? "border-t border-slate-200/70" : ""}`}>
          <div className={`w-5 h-5 rounded-md flex items-center justify-center flex-shrink-0 ${done[x.id] ? "bg-status-ready-ink text-white" : "border-2 border-slate-300"}`} aria-hidden>
            {done[x.id] && <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth={3}><path d="M5 12l4 4L19 6" /></svg>}
          </div>
          <div className="flex-1">
            <div className={`text-[13px] font-semibold ${done[x.id] ? "text-ink-tertiary line-through" : "text-ink-primary"}`}>{x.t}</div>
            <div className="text-[11px] text-ink-tertiary">{x.d}</div>
          </div>
          <button onClick={(e) => { e.stopPropagation(); goSection(x.id); }} className="text-xs font-bold text-primary">Open →</button>
        </div>
      ))}
    </div>
  );
}
