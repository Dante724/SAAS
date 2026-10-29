import { useState } from "react";
import { M } from "../../components/Shell";
import FloatingMenu from "../../components/FloatingMenu";

// Quantity and discount live on the line, not in an "Add" step: picking from search adds the item
// with quantity 1, and the rare line that needs 2 or a discount is changed here. The line decides
// what opens it (its price, a big target); this is just the small editor, anchored to that price.
// Done saves only what changed.
export const qtyAnchor = (lineId: string) => `[data-qty-anchor="${lineId}"]`;

export default function LineQtyEditor({ lineId, label, qty, discount, canDiscount, onSave, onClose }: {
  lineId: string; label: string; qty: number; discount: number; canDiscount: boolean;
  onSave: (body: { qty?: number; discount?: number; discount_type?: "pct" }) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState(String(qty));
  const [d, setD] = useState(String(discount || ""));
  function done() {
    const nq = Math.max(1, Math.round(Number(q) || 1));
    const nd = Math.min(100, Math.max(0, Number(d) || 0));
    const body: { qty?: number; discount?: number; discount_type?: "pct" } = {};
    if (nq !== qty) body.qty = nq;
    if (canDiscount && nd !== (discount || 0)) { body.discount = nd; body.discount_type = "pct"; }
    if (Object.keys(body).length) onSave(body);
    onClose();
  }
  const step = (n: number) => setQ(String(Math.max(1, (Math.round(Number(q) || 1)) + n)));
  const stepBtn = "grid h-10 w-10 place-items-center rounded-lg text-ink-secondary hover:bg-slate-100 hover:text-ink-primary";
  return (
    <FloatingMenu anchorSel={qtyAnchor(lineId)} onClose={onClose} width={248}>
      <form role="dialog" aria-label={`Quantity for ${label}`} className="space-y-3 p-3" onSubmit={(e) => { e.preventDefault(); done(); }}>
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs font-medium text-ink-secondary">Quantity</span>
          <span className="flex items-center rounded-xl border border-slate-200 bg-white">
            <button type="button" aria-label="Less" onClick={() => step(-1)} className={stepBtn}><M n="remove" c="text-base" /></button>
            <input autoFocus inputMode="numeric" value={q} onChange={(e) => setQ(e.target.value.replace(/\D/g, ""))} onFocus={(e) => e.target.select()} aria-label="Quantity"
              className="w-9 bg-transparent text-center text-sm font-semibold tabular-nums text-ink-primary outline-none" />
            <button type="button" aria-label="More" onClick={() => step(1)} className={stepBtn}><M n="add" c="text-base" /></button>
          </span>
        </div>
        {canDiscount && (
          <label className="flex items-center justify-between gap-3">
            <span className="text-xs font-medium text-ink-secondary">Discount</span>
            <span className="flex h-10 w-[120px] items-center rounded-xl border border-slate-200 bg-white px-3 focus-within:border-primary/50">
              <input inputMode="decimal" value={d} placeholder="0" onChange={(e) => setD(e.target.value.replace(/[^\d.]/g, ""))} aria-label="Discount percent"
                className="w-full min-w-0 bg-transparent text-right text-sm font-medium tabular-nums text-ink-primary outline-none" />
              <span className="ml-1 text-xs text-ink-tertiary">%</span>
            </span>
          </label>
        )}
        <button type="submit" className="h-10 w-full rounded-xl bg-primary text-sm font-semibold text-white hover:brightness-110">Done</button>
      </form>
    </FloatingMenu>
  );
}
