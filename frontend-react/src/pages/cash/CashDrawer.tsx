// 144bc — the cash drawer (shift) UI. The backend had open/close/pay-in-out/reconciliation
// endpoints but nothing in the app reached them; this is the operator surface.
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { money } from "../orders/types";
import { promptDialog, alertDialog } from "../../lib/dialog";
import DenomCountModal from "./DenomCountModal";
import ShiftHistoryModal from "./ShiftHistoryModal";
import { btnGhost, btnPrimary } from "../invoices/billing";

type Movement = { direction: string; amount: number; note: string };
type Shift = { open: boolean; id?: string; opening_float?: number; opened_by?: string | null;
               opened_at?: string | null; movements?: Movement[] };

export default function CashDrawer({ sym, currencyCode }: { sym?: string; currencyCode?: string }) {
  const [loc, setLoc] = useState("");
  const [shift, setShift] = useState<Shift | null>(null);
  const [busy, setBusy] = useState(false);
  const [count, setCount] = useState<null | "open" | "close">(null);
  const [history, setHistory] = useState(false);

  async function load(locId: string) {
    if (!locId) return;
    try { setShift(await api.get<Shift>(`/finance/shifts/current?location_id=${encodeURIComponent(locId)}`)); }
    catch { setShift({ open: false }); }
  }
  useEffect(() => {
    api.get<{ items: { id: string }[] }>("/staff/locations")
      .then((r) => { const id = r.items?.[0]?.id || ""; setLoc(id); load(id); }).catch(() => setShift({ open: false }));
  }, []);

  async function doOpen(denoms: Record<string, number>, total: number) {
    setBusy(true);
    try {
      await api.post("/finance/shifts/open",
        Object.keys(denoms).length ? { location_id: loc, denoms } : { location_id: loc, opening_float: total });
      setCount(null); await load(loc);
    } catch (e: any) { await alertDialog(e?.message || "Could not open the drawer"); } finally { setBusy(false); }
  }

  async function doClose(denoms: Record<string, number>, total: number) {
    if (!shift?.id) return;
    setBusy(true);
    try {
      const r = await api.post<any>(`/finance/shifts/${shift.id}/close`,
        Object.keys(denoms).length ? { denoms } : { closing_float: total });
      setCount(null);
      if (r.expected_cash != null) {
        const d = r.discrepancy as number;
        if (d === 0) {
          await alertDialog(`Counted ${money(total, sym)} — matches expected exactly.`, "Drawer balanced ✓");
        } else {
          let msg = `Expected ${money(r.expected_cash, sym)}, you counted ${money(total, sym)}.\n${money(Math.abs(d), sym)} ${d > 0 ? "OVER" : "SHORT"}.`;
          if (r.explanation?.movements?.length)
            msg += `\n\n${r.explanation.movements.length} pay-in/out this shift — review their notes first.`;
          await alertDialog(msg, "Drawer closed — needs review");
        }
      } else {
        await alertDialog("Drawer closed. A manager will review the count.", "Closed");
      }
      await load(loc);
    } catch (e: any) { await alertDialog(e?.message || "Could not close the drawer"); } finally { setBusy(false); }
  }

  async function move(direction: "in" | "out") {
    if (!shift?.id) return;
    const label = direction === "in" ? "Pay in" : "Pay out";
    const v = await promptDialog(`Amount to ${direction === "in" ? "add to" : "remove from"} the drawer:`,
      { title: label, placeholder: "0.00" });
    if (v == null) return;
    const amt = parseFloat(v); if (isNaN(amt) || amt <= 0) return;
    const note = await promptDialog("Reason (required, it goes on the slip):",
      { title: label, placeholder: direction === "in" ? "e.g. change top-up" : "e.g. bank run / supplier cash" });
    if (!note || !note.trim()) { await alertDialog("A note is required for every pay-in / pay-out."); return; }
    setBusy(true);
    try { await api.post(`/finance/shifts/${shift.id}/movement`, { direction, amount: amt, note: note.trim() }); await load(loc); }
    catch (e: any) { await alertDialog(e?.message || "Failed"); } finally { setBusy(false); }
  }


  if (!shift) return null;

  if (!shift.open) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-slate-100 text-ink-tertiary flex items-center justify-center border border-slate-200"><M n="point_of_sale" c="text-xl" /></div>
          <div><h4 className="text-sm font-bold text-ink-primary">Cash drawer closed</h4>
            <p className="text-xs text-ink-tertiary">Open the drawer to start taking cash and tracking the till.</p></div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setCount("open")} disabled={busy || !loc} className={btnPrimary}><M n="lock_open" c="text-sm" />Open drawer</button>
          <button onClick={() => setHistory(true)} className={btnGhost}><M n="history" c="text-sm" />History</button>
        </div>
        {count === "open" && <DenomCountModal title="Open drawer — count the till" currencyCode={currencyCode} sym={sym} confirmLabel="Open drawer" busy={busy} onConfirm={doOpen} onCancel={() => setCount(null)} />}
        {history && <ShiftHistoryModal sym={sym} onClose={() => setHistory(false)} />}
      </div>
    );
  }

  const net = (shift.movements || []).reduce((s, m) => s + (m.direction === "in" ? m.amount : -m.amount), 0);
  return (
    <div className="bg-white rounded-2xl border border-emerald-200 shadow-clay-card p-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-50 text-emerald-600 flex items-center justify-center border border-emerald-100"><M n="point_of_sale" c="text-xl" /></div>
          <div>
            <h4 className="text-sm font-bold text-ink-primary flex items-center gap-2">Drawer open
              <span className="text-[11px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-bold ">Live</span></h4>
            <p className="text-xs text-ink-tertiary">Opened with {money(shift.opening_float, sym)}{shift.opened_by ? ` · ${shift.opened_by}` : ""}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setCount("close")} disabled={busy} className={btnPrimary}><M n="lock" c="text-sm" />Close drawer</button>
          <button onClick={() => move("in")} disabled={busy} className={btnGhost}><M n="add" c="text-sm text-emerald-600" />Pay in</button>
          <button onClick={() => move("out")} disabled={busy} className={btnGhost}><M n="remove" c="text-sm text-rose-600" />Pay out</button>
          <button onClick={() => setHistory(true)} className={btnGhost}><M n="history" c="text-sm" />History</button>
        </div>
      </div>
      {!!(shift.movements || []).length && (
        <div className="mt-3 pt-2.5 border-t border-slate-100 space-y-1">
          {(shift.movements || []).map((m, i) => (
            <div key={i} className="flex items-center justify-between text-xs">
              <span className="text-ink-secondary truncate">{m.direction === "in" ? "Pay in" : "Pay out"} · {m.note}</span>
              <span className={`tabular-nums font-semibold ${m.direction === "in" ? "text-emerald-600" : "text-rose-600"}`}>{m.direction === "in" ? "+" : "−"}{money(m.amount, sym)}</span>
            </div>
          ))}
          <div className="flex items-center justify-between text-[11px] font-bold text-ink-tertiary pt-1"><span>Net pay-in/out</span><span className="tabular-nums">{net >= 0 ? "+" : "−"}{money(Math.abs(net), sym)}</span></div>
        </div>
      )}
      {count === "close" && <DenomCountModal title="Close drawer — count the till" currencyCode={currencyCode} sym={sym} confirmLabel="Close drawer" busy={busy} onConfirm={doClose} onCancel={() => setCount(null)} />}
      {history && <ShiftHistoryModal sym={sym} onClose={() => setHistory(false)} />}
    </div>
  );
}
