import { useLayoutEffect, type ReactNode } from "react";

// Small pieces the admin pages share, on top of the app's own components.

/** The admin follows the shop app's light/dark choice (the same `ros_theme` setting). */
export function useAdminTheme() {
  useLayoutEffect(() => {
    let dark = false;
    try { dark = localStorage.getItem("ros_theme") === "dark"; } catch { /* private mode */ }
    document.documentElement.classList.toggle("dark", dark);
  }, []);
}

/** A card with a titled header row; the body runs edge to edge (lists and tables). */
export function AdminCard({ title, meta, children, className = "" }: { title?: ReactNode; meta?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-clay-card ${className}`}>
      {title && (
        <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-3">
          <h3 className="text-sm font-semibold text-ink-primary">{title}</h3>
          {meta != null && <span className="text-xs tabular-nums text-ink-tertiary">{meta}</span>}
        </div>
      )}
      {children}
    </section>
  );
}

export const rowCls = "flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-slate-100 px-4 py-3 text-sm last:border-0";
