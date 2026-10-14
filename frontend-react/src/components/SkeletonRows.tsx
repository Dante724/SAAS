// Shimmer placeholder rows for a loading table body. Replaces the plain "Loading…" cell
// so list tables fade in with their shape intact. Uses the shared `.sk` shimmer (index.css).
export default function SkeletonRows({ cols, rows = 6 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} aria-hidden={r > 0}>
          {Array.from({ length: cols }).map((_, c) => (
            <td key={c} className="py-3 px-4">
              {/* keep a screen-reader announcement that the table is loading */}
              {r === 0 && c === 0 && <span className="sr-only" role="status">Loading…</span>}
              <span className="sk block h-3 rounded" style={{ width: c === 0 ? "55%" : "35%" }} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
