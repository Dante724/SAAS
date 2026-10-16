// 144be — drawer reconciliation history (Shopmonkey's "Cash Tracking Report" equivalent). Lists
// recent drawers with balanced / short / over status, who opened, expected vs counted. Admin-only
// surface (the GET /shifts endpoint requires can_view_finance).
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { money } from "../orders/types";

type Shift = { id: string; open: boolean; opening_float?: number; closing_float?: number;
               expected_cash?: number | null; discrepancy?: number | null; opened_by?: string | null;
               opened_at?: string | null; closed_at?: string | null };

function status(s: Shift): { label: string; cls: string } {
  if (s.open) return { label: "OPEN", cls: "bg-blue-100 text-blue-700" };
  if (s.discrepancy == null) return { label: "CLOSED", cls: "bg-slate-100 text-ink-secondary" };
  if (Math.abs(s.discrepancy) < 0.01) return { label: "BALANCED", cls: "bg-emerald-100 text-emerald-700" };
  return { label: s.discrepancy > 0 ? "OVER" : "SHORT", cls: "bg-rose-100 text-rose-700" };
}

export default function ShiftHistoryModal({ sym, onClose }: { sym?: string; onClose: () => void }) {
  const [rows, setRows] = useState<Shift[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api.get<{ items: Shift[] }>("/finance/shifts").then((r) => setRows(r.items || []))
      .catch((e) => setErr(e?.message || "Could not load drawer history"));
  }, []);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-2xl p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-bold text-ink-primary flex items-center gap-2"><M n="history" c="text-base text-primary" />Drawer history &amp; reconciliation</h3>
          <button aria-label="Close" onClick={onClose} className="w-7 h-7 rounded-full flex items-center justify-center text-ink-tertiary hover:bg-slate-100"><M n="close" c="text-base" /></button>
        </div>
        {err && <p className="text-xs text-rose-600 py-4">{err}</p>}
        {!rows && !err && <p className="text-xs text-ink-tertiary py-6 text-center">Loading…</p>}
        {rows && !rows.length && <p className="text-xs text-ink-tertiary py-6 text-center">No drawers opened yet.</p>}
        {rows && !!rows.length && (
          <div className="overflow-y-auto max-h-96 -mx-1">
            <table className="w-full text-xs">
              <thead><tr className="text-[11px] font-bold text-ink-tertiary text-left border-b border-slate-200">
                <th className="py-2 px-2">Opened</th><th className="px-2">By</th><th className="px-2 text-right">Opening</th>
                <th className="px-2 text-right">Expected</th><th className="px-2 text-right">Counted</th>
                <th className="px-2 text-right">Diff</th><th className="px-2 text-right">Status</th></tr></thead>
              <tbody>
                {rows.map((s) => {
                  const st = status(s);
                  return (
                    <tr key={s.id} className="border-b border-slate-100">
                      <td className="py-2 px-2 text-ink-secondary">{s.opened_at ? new Date(s.opened_at).toLocaleString() : "—"}</td>
                      <td className="px-2 text-ink-secondary">{s.opened_by || "—"}</td>
                      <td className="px-2 text-right tabular-nums">{money(s.opening_float, sym)}</td>
                      <td className="px-2 text-right tabular-nums">{s.expected_cash != null ? money(s.expected_cash, sym) : "—"}</td>
                      <td className="px-2 text-right tabular-nums">{s.closing_float != null ? money(s.closing_float, sym) : "—"}</td>
                      <td className={`px-2 text-right tabular-nums font-bold ${s.discrepancy == null ? "text-ink-tertiary" : Math.abs(s.discrepancy) < 0.01 ? "text-emerald-600" : "text-rose-600"}`}>{s.discrepancy != null ? (s.discrepancy > 0 ? "+" : "") + money(s.discrepancy, sym) : "—"}</td>
                      <td className="px-2 text-right"><span className={`text-[11px] px-1.5 py-0.5 rounded font-bold ${st.cls}`}>{st.label}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
