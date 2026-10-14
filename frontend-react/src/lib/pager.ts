// Page-number sequence for the inventory Pager: always first + last, a window of ±1
// around the current page, and "…" standing in for each gap. Pure + tested (pager.test.ts).
export function pageNumbers(current: number, pages: number): (number | "…")[] {
  const n = Math.max(1, pages);
  const out: (number | "…")[] = [];
  for (let p = 1; p <= n; p++) {
    if (p === 1 || p === n || (p >= current - 1 && p <= current + 1)) out.push(p);
    else if (out[out.length - 1] !== "…") out.push("…");
  }
  return out;
}
