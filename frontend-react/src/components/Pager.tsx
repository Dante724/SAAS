import { M } from "./Shell";
import { pageNumbers } from "../lib/pager";

// Shared client-side numbered pager (default 50/page). Shows "Showing X–Y of N {noun}"
// and ‹ 1 2 … N › with ellipsis truncation. Page numbers hide when there's only one page.
// Page-number sequence is the pure, tested pageNumbers() in lib/pager.ts.
export default function Pager({ total, page, pageSize, onPage, noun }:
  { total: number; page: number; pageSize: number; onPage: (p: number) => void; noun: string }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  const nums = pageNumbers(page, pages);
  const cell = "min-w-[34px] h-[34px] px-2 rounded-lg border text-[11px] font-semibold flex items-center justify-center transition-colors";
  return (
    <div className="px-5 py-3 bg-slate-50/60 border-t border-slate-100 flex items-center justify-between gap-3 flex-wrap text-xs text-ink-tertiary">
      <div>Showing <span className="font-semibold text-ink-primary tabular-nums">{from}–{to}</span> of <span className="font-semibold text-ink-primary tabular-nums">{total}</span> {noun}</div>
      {pages > 1 && (
        <div className="flex items-center gap-1">
          <button disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page" className={`${cell} border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 disabled:opacity-40 disabled:hover:bg-white disabled:cursor-default`}><M n="chevron_left" c="text-sm" /></button>
          {nums.map((n, i) => n === "…"
            ? <span key={`e${i}`} className="px-1 select-none text-ink-tertiary">…</span>
            : <button key={n} onClick={() => onPage(n)} aria-current={n === page ? "page" : undefined} className={`${cell} tabular-nums ${n === page ? "border-primary bg-primary text-white shadow-clay-sm" : "border-slate-200 bg-white text-ink-secondary hover:bg-slate-50"}`}>{n}</button>)}
          <button disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page" className={`${cell} border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 disabled:opacity-40 disabled:hover:bg-white disabled:cursor-default`}><M n="chevron_right" c="text-sm" /></button>
        </div>
      )}
    </div>
  );
}
