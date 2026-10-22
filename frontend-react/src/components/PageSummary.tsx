import type { ReactNode } from "react";

/* The one way a page shows its headline numbers: a single card, numbers side by side,
   plain sentence-case labels, the normal font with even-width digits. No icon tiles,
   no gradients, no uppercase. A number can be a button (filter or go somewhere); the
   selected one gets a quiet underline. `tone: "bad"` turns a non-zero number red (late,
   overdue), so colour only shows up when something needs attention. */

export type SummaryStat = {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "bad" | "good";
  /** Treat the value as zero for colouring (e.g. "0", "$0"). */
  zero?: boolean;
  onClick?: () => void;
  active?: boolean;
};

export default function PageSummary({ title, subtitle, actions, stats, className = "" }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; stats: SummaryStat[]; className?: string;
}) {
  return (
    <section className={`bg-white rounded-2xl border border-slate-200/90 ${className}`}>
      {(title || subtitle || actions) && (
        <div className="flex flex-wrap items-start gap-3 px-5 pt-4">
          <div className="min-w-0">
            {title && <h2 className="text-base font-semibold text-ink-primary">{title}</h2>}
            {subtitle && <p className="text-xs text-ink-tertiary">{subtitle}</p>}
          </div>
          {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
        </div>
      )}
      {/* Seven-plus tiles in one row squeeze a big money value until it clips, so long rows stay
          a grid until there's room (1400px). */}
      <div className={`grid grid-cols-2 sm:grid-cols-3 ${stats.length > 5 ? "lg:grid-cols-4 min-[1400px]:flex min-[1400px]:divide-x" : "lg:flex lg:divide-x"} lg:divide-slate-100`}>
        {stats.map((s) => {
          const tone = s.zero ? "text-ink-primary" : s.tone === "bad" ? "text-rose-600" : s.tone === "good" ? "text-emerald-700" : "text-ink-primary";
          const body = (
            <>
              <span className="block text-xs font-medium text-ink-tertiary">{s.label}</span>
              <span className={`block mt-1 text-2xl font-semibold tracking-tight tabular-nums whitespace-nowrap ${tone}`}>{s.value}</span>
              {s.hint && <span className="block mt-0.5 text-xs text-ink-tertiary line-clamp-2">{s.hint}</span>}
            </>
          );
          const cls = `${stats.length > 5 ? "min-[1400px]:flex-1" : "lg:flex-1"} min-w-0 text-left px-5 py-4 ${s.active ? "shadow-[inset_0_-2px_0] shadow-primary" : ""}`;
          return s.onClick ? (
            <button key={s.label} type="button" onClick={s.onClick} aria-pressed={s.active}
              className={`${cls} hover:bg-slate-50/80 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 first:rounded-bl-2xl last:rounded-br-2xl`}>{body}</button>
          ) : <div key={s.label} className={cls}>{body}</div>;
        })}
      </div>
    </section>
  );
}
