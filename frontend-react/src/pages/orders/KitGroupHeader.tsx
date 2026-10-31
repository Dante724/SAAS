import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../api/client";
import { M } from "../../components/Shell";
import { useMe } from "../../context/Me";
import { promptDialog } from "../../lib/dialog";
import { KIT_PRINT_LABELS, kitPrintLabel, type KitPrintMode } from "../../lib/kitGroups";
import { money } from "./types";

// One kit on a job: a header over its lines (expand/collapse), how it prints for the customer on
// this job, and — for a fixed-price kit — its locked price, which a manager can change with a
// reason. Each control is behind its own role permission so a shop can switch it off per role.

let warningCache: Promise<string | null> | null = null;
function printWarning(): Promise<string | null> {
  warningCache ||= api.get<{ warning: string | null }>("/bundles/print-warning").then((r) => r.warning).catch(() => null);
  return warningCache;
}

export default function KitGroupHeader({ orderId, groupId, name, qty, mode, locked, total, count, seePrices, closed, onChanged, onFlash, onAddInto, children }: {
  orderId: string; groupId: string; name: string; qty: number; mode: KitPrintMode; locked: number | null; total: number; count: number;
  seePrices: boolean; closed?: boolean; onChanged?: () => void; onFlash: (m: string) => void; onAddInto?: (groupId: string) => void; children: ReactNode;
}) {
  const { can } = useMe();
  const [open, setOpen] = useState(true);
  const [warning, setWarning] = useState<string | null>(null);
  const canPrint = can("can_change_kit_print") && !closed;
  const canPrice = can("can_change_kit_price") && !closed && locked != null;
  useEffect(() => { if (canPrint) printWarning().then(setWarning); }, [canPrint]);

  async function save(body: Record<string, unknown>, done: string) {
    try { await api.patch(`/orders/${orderId}/kits/${groupId}`, body); onFlash(done); onChanged?.(); }
    catch (e: any) { onFlash(e.message || "Could not update the kit"); }
  }
  async function changePrice() {
    const p = await promptDialog(`New price for ${name} on this job`, { title: "Change kit price", defaultValue: String(locked ?? ""), confirmLabel: "Next" });
    if (p == null || p.trim() === "") return;
    const price = Number(p);
    if (!Number.isFinite(price) || price < 0) { onFlash("Enter a price of 0 or more"); return; }
    // Repeat both prices so a slip ("90008000" for "8000") is caught before it's saved.
    const reason = await promptDialog(`${name}: ${money(locked ?? 0)} → ${money(price)}. Why is the price changing? This is saved in the activity log.`,
      { title: "Reason", placeholder: "e.g. Price match", confirmLabel: "Save price" });
    if (!reason || reason.trim().length < 3) { onFlash("Add a reason to change the kit price"); return; }
    await save({ price, reason: reason.trim() }, "Kit price changed");
  }

  return (
    <div className="border-b border-slate-100 last:border-b-0" data-testid="kit-group">
      {/* Row 1: what the kit is and what it costs. Row 2: its controls — they wrap under the name
          on a phone instead of squeezing it. */}
      <div className="px-3.5 py-2.5 bg-slate-50/80 space-y-2">
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-label={`${open ? "Hide" : "Show"} what's in ${name}`}
            className="w-6 h-6 grid place-items-center rounded-md flex-none text-ink-tertiary hover:text-ink-primary hover:bg-white">
            <M n={open ? "expand_more" : "chevron_right"} c="text-base" />
          </button>
          <div className="w-8 h-8 rounded-lg grid place-items-center flex-none bg-amber-50 text-amber-700"><M n="package_2" c="text-base" /></div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-x-2 gap-y-1 flex-wrap">
              <h5 className="text-xs font-bold text-ink-primary">{name}{qty > 1 ? ` × ${qty}` : ""}</h5>
              <span className="text-[11px] px-1.5 py-0.5 rounded font-bold bg-amber-50 text-amber-700">Kit</span>
              {locked != null && <span className="text-[11px] px-1.5 py-0.5 rounded font-semibold bg-slate-100 text-ink-secondary whitespace-nowrap" title="The customer's price for this kit stays the same when its parts change.">Fixed price</span>}
            </div>
            <p className="text-[11px] text-ink-tertiary">{count} {count === 1 ? "item" : "items"} · Customer sees: {kitPrintLabel(mode)}</p>
          </div>
          {seePrices && <span className="tabular-nums text-xs font-bold text-ink-primary flex-none">{money(total)}</span>}
        </div>
        {(canPrint || canPrice || (onAddInto && !closed)) && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pl-16">
            {canPrint && (
              <select value={mode} onChange={(e) => save({ print_mode: e.target.value }, "Kit print setting saved")} aria-label={`How ${name} prints for the customer`}
                className="glass-input appearance-none text-[11px] font-semibold text-ink-secondary rounded-lg pl-2 pr-6 py-1 max-w-full">
                {KIT_PRINT_LABELS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            )}
            {canPrice && (
              <button type="button" onClick={changePrice} className="text-[11px] font-semibold text-primary hover:underline">Change price</button>
            )}
            {onAddInto && !closed && (
              <button type="button" onClick={() => onAddInto(groupId)} className="text-[11px] font-semibold text-primary hover:underline">Add to kit</button>
            )}
          </div>
        )}
      </div>
      {canPrint && warning && mode !== "itemized" && (
        <p role="note" className="px-4 py-1.5 text-[11px] text-amber-800 bg-amber-50 border-t border-amber-100 flex items-start gap-1.5"><M n="info" c="text-[13px] mt-px" />{warning}</p>
      )}
      {open && <div className="divide-y divide-slate-100 pl-1 ml-3 sm:pl-6 sm:ml-5 border-l-2 border-amber-100">{children}</div>}
    </div>
  );
}
