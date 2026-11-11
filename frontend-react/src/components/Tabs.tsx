import type { ReactNode } from "react";

// The app's one tab control, in three looks:
//   underline: page sections (Finance › Overview / Profit & loss …)
//   segmented: view switches (Table / Board, Agenda / Scheduler)
//   chips:     filters with counts (All 5 · New 1 · Done 2)
// The selected tab changes colour only, never weight or size, and tabs never shrink below their
// label (a long row scrolls sideways with no visible scrollbar), so switching never moves anything.
export type TabItem<T extends string> = { id: T; label: ReactNode; count?: number | null; icon?: ReactNode; disabled?: boolean; title?: string };

export default function Tabs<T extends string>({ tabs, value, onChange, look = "underline", label, className = "" }: {
  tabs: TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  look?: "underline" | "segmented" | "chips";
  label: string;          // accessible name of the group, e.g. "Finance sections"
  className?: string;
}) {
  const row = look === "underline" ? "flex gap-6 overflow-x-auto no-scrollbar border-b border-slate-200"
    : look === "segmented" ? "inline-flex rounded-xl bg-slate-100 p-1 gap-0.5 max-w-full overflow-x-auto no-scrollbar"
    : "flex flex-wrap items-center gap-2";
  const isTabs = look === "underline";
  return (
    <div role={isTabs ? "tablist" : "group"} aria-label={label} className={`${row} ${className}`}>
      {tabs.map((t) => {
        const on = t.id === value;
        const cls = look === "underline"
          ? `-mb-px py-3 border-b-2 text-sm ${on ? "text-primary border-primary" : "text-ink-secondary border-transparent hover:text-ink-primary"}`
          : look === "segmented"
          ? `h-7 px-3 rounded-lg text-xs ${on ? "bg-white text-ink-primary shadow-2xs" : "text-ink-secondary hover:text-ink-primary"}`
          : `h-8 px-3 rounded-full ring-1 ring-inset text-xs ${on ? "bg-primary/10 text-primary ring-primary/20" : "bg-white text-ink-secondary ring-slate-200 hover:text-ink-primary hover:bg-slate-50"}`;
        return (
          <button key={t.id} type="button" disabled={t.disabled} title={t.title} onClick={() => onChange(t.id)}
            {...(isTabs ? { role: "tab", "aria-selected": on } : { "aria-pressed": on })}
            className={`hit44 shrink-0 inline-flex items-center gap-1.5 whitespace-nowrap font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${cls}`}>
            {t.icon}{t.label}
            {t.count != null && <span className={`tabular-nums font-medium ${on ? "text-primary" : "text-ink-tertiary"}`}>{t.count}</span>}
          </button>
        );
      })}
    </div>
  );
}
