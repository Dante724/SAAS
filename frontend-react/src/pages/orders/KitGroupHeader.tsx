import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../api/client";
import { M } from "../../components/Shell";
import FloatingMenu from "../../components/FloatingMenu";
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

export default function KitGroupHeader({ orderId, groupId, name, qty, mode, locked, total, count, seePrices, closed, onChanged, onFlash, onAddInto, onRemove, children }: {
  orderId: string; groupId: string; name: string; qty: number; mode: KitPrintMode; locked: number | null; total: number; count: number;
  seePrices: boolean; closed?: boolean; onChanged?: () => void; onFlash: (m: string) => void; onAddInto?: (groupId: string) => void;
  onRemove?: () => void;   // removes the whole kit from the job (all its lines)
  children: ReactNode;
}) {
  const { can } = useMe();
  const [open, setOpen] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
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
      { title: "Reason", placeholder: "Price match", confirmLabel: "Save price" });
    if (!reason || reason.trim().length < 3) { onFlash("Add a reason to change the kit price"); return; }
    await save({ price, reason: reason.trim() }, "Kit price changed");
  }

  const canAdd = !!onAddInto && !closed;
  const canDrop = !!onRemove && !closed;
  const hasMenu = canPrint || canPrice || canAdd || canDrop;
  const menuSel = `[data-kit-menu="${groupId}"]`;
  const item = "w-full flex items-center gap-2.5 px-2.5 h-9 rounded-lg text-left text-[13px] hover:bg-slate-50 focus-visible:bg-slate-100 outline-none";
  const pick = (fn: () => void) => () => { setMenuOpen(false); fn(); };
  const sub = ["Kit", locked != null ? "Fixed price" : "", `${count} ${count === 1 ? "item" : "items"}`, `Customer sees ${kitPrintLabel(mode).toLowerCase()}`].filter(Boolean).join(" · ");

  return (
    <div className="bg-slate-50/40 border-y border-slate-200/70 first:border-t-0 last:border-b-0" data-testid="kit-group">
      {/* Same columns as the line rows, so the kit total sits in the amount column and ⋯ in the
          remove slot: chevron | tile + name | — | total | actions. */}
      <div className="grid grid-cols-[28px_minmax(0,1fr)_auto_88px_40px] items-center gap-x-3 px-4 py-3 min-h-[56px] bg-slate-50/70">
        <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-label={`${open ? "Hide" : "Show"} what's in ${name}`}
          className="w-7 h-7 grid place-items-center rounded-md text-ink-tertiary hover:text-ink-primary hover:bg-white">
          <M n={open ? "expand_more" : "chevron_right"} c="text-base" />
        </button>
        <div className="min-w-0 flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg grid place-items-center flex-none bg-slate-100 text-ink-secondary"><M n="package_2" c="text-[15px]" /></div>
          <div className="min-w-0">
            <h5 className="text-[13px] font-semibold text-ink-primary truncate">{name}{qty > 1 ? ` × ${qty}` : ""}</h5>
            <p className="text-xs text-ink-secondary" title={locked != null ? "The customer's price for this kit stays the same when its parts change." : undefined}>{sub}</p>
          </div>
        </div>
        <div />
        <div className="text-right">{seePrices && <span className="tabular-nums text-[13px] font-semibold text-ink-primary">{money(total)}</span>}</div>
        <div className="w-10 h-10">
          {hasMenu && (
            <button type="button" data-kit-menu={groupId} onClick={() => setMenuOpen((v) => !v)} aria-label={`Kit actions for ${name}`} aria-haspopup="menu" aria-expanded={menuOpen}
              className="w-10 h-10 grid place-items-center rounded-lg text-ink-tertiary hover:text-ink-primary hover:bg-white"><M n="more_horiz" c="text-base" /></button>
          )}
          {menuOpen && (
            <FloatingMenu anchorSel={menuSel} onClose={() => setMenuOpen(false)} width={256}>
              <div className="text-[13px]">
                {canPrint && <>
                  <div className="px-2.5 pt-1 pb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Customer sees</div>
                  {KIT_PRINT_LABELS.map((o) => (
                    <button type="button" key={o.value} role="menuitemradio" aria-checked={mode === o.value} title={o.hint}
                      onClick={pick(() => { if (o.value !== mode) save({ print_mode: o.value }, "Kit print setting saved"); })}
                      className={`${item} ${mode === o.value ? "font-semibold text-ink-primary" : "text-ink-primary"}`}>
                      <span className="truncate">{o.label}</span>{mode === o.value && <M n="check" c="ml-auto text-[16px] text-primary" />}
                    </button>
                  ))}
                </>}
                {(canPrice || canAdd) && (
                  <div className={canPrint ? "mt-1 pt-1 border-t border-slate-100" : ""}>
                    {canPrice && <button type="button" role="menuitem" onClick={pick(changePrice)} className={`${item} text-ink-primary`}>Change price</button>}
                    {canAdd && <button type="button" role="menuitem" onClick={pick(() => onAddInto!(groupId))} className={`${item} text-ink-primary`}>Add to kit</button>}
                  </div>
                )}
                {canDrop && (
                  <div className={canPrint || canPrice || canAdd ? "mt-1 pt-1 border-t border-slate-100" : ""}>
                    <button type="button" role="menuitem" onClick={pick(onRemove!)} className={`${item} text-rose-600 hover:bg-rose-50`}>Remove kit</button>
                  </div>
                )}
              </div>
            </FloatingMenu>
          )}
        </div>
      </div>
      {canPrint && warning && mode !== "itemized" && (
        <p role="note" className="px-4 py-1.5 text-[11px] text-amber-800 bg-amber-50 border-t border-amber-100 flex items-start gap-1.5"><M n="info" c="text-[13px] mt-px" />{warning}</p>
      )}
      {/* Children indent so their icon sits under the kit's tile. */}
      {open && <div className="pl-10 divide-y divide-slate-100 border-t border-slate-100">{children}</div>}
    </div>
  );
}
