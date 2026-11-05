// The app's one loading placeholder for a page or a section that is still downloading: a toolbar
// and a few rows in the shared `.sk` shimmer, inside the page frame, so the sidebar and header stay
// put and the page fades in with its shape. Screen readers hear "Loading…" once.
export default function PageSkeleton({ rows = 6, toolbar = true }: { rows?: number; toolbar?: boolean }) {
  return (
    // A whole page gets the page gutter; a section inside a page (toolbar={false}) only its own space.
    <div className={`space-y-4 ${toolbar ? "flex-1 min-h-0 p-4 sm:p-6" : "py-6"}`} aria-busy="true">
      <span className="sr-only" role="status">Loading…</span>
      {toolbar && (
        <div className="flex items-center gap-2" aria-hidden="true">
          <span className="sk block h-9 w-24 rounded-xl" />
          <span className="sk block h-9 w-20 rounded-xl" />
          <span className="sk block h-9 w-44 rounded-xl ml-auto" />
        </div>
      )}
      <div className="rounded-2xl border border-slate-200/90 bg-white p-4 space-y-3" aria-hidden="true">
        {Array.from({ length: rows }).map((_, i) => (
          <span key={i} className="sk block h-3 rounded" style={{ width: `${[62, 48, 55, 40, 58, 45][i % 6]}%` }} />
        ))}
      </div>
    </div>
  );
}
