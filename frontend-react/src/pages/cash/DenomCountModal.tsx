// 144bd — guided denomination count for opening / closing the drawer. Replaces the single-number
// prompt so an untrained cashier counts bill-by-bill and the total can't be fat-fingered. Sends a
// {denom: count} map to the backend, which stores it and derives the total.
import { useMemo, useState } from "react";
import { M } from "../../components/Shell";
import Button, { buttonClass } from "../../components/Button";
import { money } from "../orders/types";

const DENOMS: Record<string, number[]> = {
  INR: [2000, 500, 200, 100, 50, 20, 10, 5, 2, 1],
  USD: [100, 50, 20, 10, 5, 1, 0.25, 0.1, 0.05, 0.01],
  EUR: [500, 200, 100, 50, 20, 10, 5, 2, 1, 0.5, 0.2, 0.1],
  GBP: [50, 20, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05],
  AED: [1000, 500, 200, 100, 50, 20, 10, 5, 1],
};
const DEFAULT_SET = [100, 50, 20, 10, 5, 1];

function fmtDenom(d: number): string {
  // key the backend expects, e.g. "2000" or "0.25"
  return Number.isInteger(d) ? String(d) : String(d);
}

export default function DenomCountModal({
  title, currencyCode, sym, confirmLabel, busy, onConfirm, onCancel,
}: {
  title: string; currencyCode?: string; sym?: string; confirmLabel?: string; busy?: boolean;
  onConfirm: (denoms: Record<string, number>, total: number) => void; onCancel: () => void;
}) {
  const set = DENOMS[(currencyCode || "").toUpperCase()] || DEFAULT_SET;
  const [counts, setCounts] = useState<Record<string, string>>({});

  const total = useMemo(() => set.reduce((s, d) => s + d * (parseInt(counts[fmtDenom(d)] || "0", 10) || 0), 0), [counts, set]);

  function confirm() {
    const denoms: Record<string, number> = {};
    for (const d of set) {
      const n = parseInt(counts[fmtDenom(d)] || "0", 10) || 0;
      if (n > 0) denoms[fmtDenom(d)] = n;
    }
    onConfirm(denoms, total);
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-ink-primary/40 p-4" role="presentation" onClick={onCancel}>
      <div className="relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-ink-primary flex items-center gap-2"><M n="payments" c="text-base text-primary" />{title}</h3>
          <button aria-label="Close" onClick={onCancel} className={buttonClass("ghost", "sm", "w-8 !px-0 text-ink-tertiary")}><M n="close" c="text-base" /></button>
        </div>
        <p className="text-2xs text-ink-tertiary mb-3">Enter how many of each note / coin are in the drawer.</p>
        <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
          {set.map((d) => {
            const key = fmtDenom(d);
            const n = parseInt(counts[key] || "0", 10) || 0;
            return (
              <div key={key} className="flex items-center gap-2">
                <span className="w-16 text-xs font-semibold tabular-nums text-ink-secondary text-right">{money(d, sym)}</span>
                <span className="text-ink-tertiary text-xs">×</span>
                <input type="number" min={0} inputMode="numeric" value={counts[key] || ""}
                  onChange={(e) => setCounts({ ...counts, [key]: e.target.value })}
                  placeholder="0" className="glass-input w-16 h-9 px-2 text-sm tabular-nums font-semibold rounded-xl text-center" />
                <span className="ml-auto text-xs tabular-nums font-semibold text-ink-primary">{n ? money(d * n, sym) : <span className="text-ink-placeholder">—</span>}</span>
              </div>
            );
          })}
        </div>
        <div className="mt-3 pt-3 border-t border-slate-200 flex items-center justify-between">
          <span className="text-xs font-semibold text-ink-tertiary">Counted total</span>
          <span className="text-lg font-bold tabular-nums text-ink-primary">{money(total, sym)}</span>
        </div>
        <div className="mt-4 flex gap-2">
          <Button variant="primary" onClick={confirm} disabled={busy}>{confirmLabel || "Confirm"}</Button>
          <Button onClick={onCancel}>Cancel</Button>
        </div>
      </div>
    </div>
  );
}
