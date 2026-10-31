import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { promptDialog } from "../../lib/dialog";
import { money } from "./types";

// E5 — an estimate's terms and history: valid until, the deposit asked for at approval, the
// customer's decline reason, and earlier versions (revisions).
type Revision = {
  id: string; revision: number; total?: number; decision?: string | null; decline_reason?: string | null;
  skipped?: string[]; note?: string | null; created_by_name?: string | null; created_at?: string | null;
  lines: { description: string; qty: number; unit_price?: number; total?: number }[];
};

export type EstimateInfo = {
  estimate_valid_until?: string | null; estimate_revision?: number; estimate_decline_reason?: string | null;
  estimate_approved_at?: string | null; deposit_amount?: number; total?: number;
};

const fmtDay = (iso?: string | null) => (iso ? new Date(iso.length === 10 ? iso + "T00:00:00" : iso).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "");
const DECISION: Record<string, string> = { approved: "Approved", declined: "Declined" };

export function useRevisions(orderId: string, rev?: number) {
  const [items, setItems] = useState<Revision[]>([]);
  const load = () => api.get<{ items: Revision[] }>(`/orders/${orderId}/estimate/revisions`).then((r) => setItems(r.items || [])).catch(() => setItems([]));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [orderId, rev]);
  return { items, load };
}

export async function startRevision(orderId: string): Promise<number | null> {
  const note = await promptDialog("What changed in this version? (optional)", { title: "New revision", confirmLabel: "Start revision" });
  if (note === null) return null;
  const r = await api.post<{ revision: number }>(`/orders/${orderId}/estimate/revisions`, { note: note || undefined });
  return r.revision;
}

export default function EstimateTerms({ orderId, d, onChanged, onFlash }: {
  orderId: string; d: EstimateInfo; onChanged: () => void; onFlash: (m: string, tone?: "warn") => void;
}) {
  const { items } = useRevisions(orderId, d.estimate_revision);
  const [showHist, setShowHist] = useState(false);
  const [dep, setDep] = useState(String(d.deposit_amount || ""));
  useEffect(() => { setDep(d.deposit_amount ? String(d.deposit_amount) : ""); }, [d.deposit_amount]);
  const today = new Date().toISOString().slice(0, 10);
  const expired = !!d.estimate_valid_until && d.estimate_valid_until < today;

  async function setValid(v: string) {
    try { await api.patch(`/orders/${orderId}/estimate`, { valid_until: v || null }); onFlash(v ? `Valid until ${fmtDay(v)}` : "Valid-until date cleared"); onChanged(); }
    catch (e: any) { onFlash(e.message || "Couldn't save the date", "warn"); }
  }
  async function saveDeposit() {
    const n = Number(dep || 0);
    if (!Number.isFinite(n) || n < 0) { onFlash("Enter an amount", "warn"); return; }
    if (n === (d.deposit_amount || 0)) return;
    try { const r = await api.patch<{ deposit_amount: number }>(`/orders/${orderId}/deposit`, { amount: n }); onFlash(r.deposit_amount > 0 ? `Deposit at approval: ${money(r.deposit_amount)}` : "No deposit asked"); onChanged(); }
    catch (e: any) { onFlash(e.message || "Couldn't save the deposit", "warn"); }
  }

  return (
    <div className="px-4 sm:px-6 pb-2.5 -mt-0.5 bg-white border-b border-slate-200/70 flex-shrink-0 space-y-2">
      {d.estimate_decline_reason && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 flex items-start gap-2">
          <M n="thumb_down" c="text-sm mt-px" />
          <span><b>The customer declined:</b> “{d.estimate_decline_reason}”. Change the estimate and start a new revision to send it again.</span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
        <label className="flex items-center gap-2">
          <span className="font-medium text-ink-secondary">Valid until</span>
          <span className="w-[150px]"><DateField mode="date" value={d.estimate_valid_until || ""} onChange={(v) => setValid(v)} /></span>
          {expired && <span className="text-[11px] font-semibold text-rose-600">Expired</span>}
        </label>
        <label className="flex items-center gap-2">
          <span className="font-medium text-ink-secondary">Deposit at approval</span>
          <input value={dep} onChange={(e) => setDep(e.target.value.replace(/[^0-9.]/g, ""))} onBlur={saveDeposit}
            onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
            inputMode="decimal" placeholder="None" aria-label="Deposit at approval" className="glass-input w-24 py-1.5 px-2 text-xs rounded-lg tabular-nums" />
        </label>
        <button type="button" onClick={() => setShowHist((v) => !v)} disabled={!items.length}
          className="flex items-center gap-1 text-ink-secondary hover:text-ink-primary disabled:opacity-60 disabled:hover:text-ink-secondary">
          <M n="history" c="text-sm" />Revision {d.estimate_revision || 1}{items.length ? ` · ${items.length} earlier` : ""}
          {items.length > 0 && <M n={showHist ? "expand_less" : "expand_more"} c="text-sm" />}
        </button>
      </div>
      {showHist && items.length > 0 && (
        <ol className="rounded-xl border border-slate-200 bg-slate-50/70 divide-y divide-slate-200/70">
          {[...items].reverse().map((r) => (
            <li key={r.id} className="px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                <span className="font-semibold text-ink-primary">Revision {r.revision}</span>
                {r.total != null && <span className="tabular-nums text-ink-primary">{money(r.total)}</span>}
                {r.decision && <span className={`text-[11px] font-semibold ${r.decision === "approved" ? "text-emerald-700" : "text-rose-600"}`}>{DECISION[r.decision] || r.decision}</span>}
                <span className="text-ink-tertiary">{fmtDay(r.created_at)}{r.created_by_name ? ` · ${r.created_by_name}` : ""}</span>
              </div>
              {r.note && <div className="mt-0.5 text-ink-secondary">{r.note}</div>}
              {r.decline_reason && <div className="mt-0.5 text-rose-700">“{r.decline_reason}”</div>}
              {(r.skipped || []).length > 0 && <div className="mt-0.5 text-ink-secondary">Left out: {(r.skipped || []).join(", ")}</div>}
              <ul className="mt-1 text-ink-tertiary">
                {r.lines.map((ln, i) => <li key={i} className="flex justify-between gap-3"><span className="truncate">{ln.qty !== 1 ? `${ln.qty} × ` : ""}{ln.description}</span>{ln.total != null && <span className="tabular-nums">{money(ln.total)}</span>}</li>)}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
