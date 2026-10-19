import { useEffect, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { useMe } from "../context/Me";
import { promptDialog } from "../lib/dialog";
import { money } from "../pages/orders/types";
import { getLocale } from "../i18n";

// A short list of recent money documents (counter sales, refunds) with a Delete button for
// roles allowed to void them. Deleting keeps the row (struck through) and the API puts back
// everything the document moved: stock, rewards, cash, commissions.
export type VoidRow = { id: string; title: string; sub?: string; amount: number; created_at?: string | null;
  voided_at?: string | null; void_reason?: string | null };

export default function VoidableList({ load, deletePath, perm, noun, empty, onFlash }: {
  load: () => Promise<VoidRow[]>; deletePath: (id: string) => string; perm: string; noun: string;
  empty: string; onFlash: (m: string) => void;
}) {
  const { can } = useMe();
  const [rows, setRows] = useState<VoidRow[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const refresh = () => load().then(setRows).catch(() => setRows([]));
  useEffect(() => { refresh(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  async function del(r: VoidRow) {
    const reason = await promptDialog(`Delete ${noun} ${r.title}? Everything it moved is put back. Why is it being deleted?`,
      { title: `Delete ${noun}`, placeholder: "Reason", confirmLabel: "Delete" });
    if (reason === null) return;
    setBusy(r.id);
    try {
      await api.del(`${deletePath(r.id)}?reason=${encodeURIComponent(reason.trim() || "Deleted")}`);
      onFlash(`${noun[0].toUpperCase()}${noun.slice(1)} ${r.title} deleted`);
      refresh();
    } catch (e: any) { onFlash(e.message || "Could not delete"); } finally { setBusy(null); }
  }

  if (rows === null) return <p className="text-xs text-ink-tertiary p-4">Loading…</p>;
  if (rows.length === 0) return <p className="text-xs text-ink-tertiary p-4">{empty}</p>;
  return (
    <div className="divide-y divide-slate-100">
      {rows.map((r) => (
        <div key={r.id} className="flex items-center gap-3 px-4 py-2.5 text-xs">
          <div className={`flex-1 min-w-0 ${r.voided_at ? "line-through text-ink-tertiary" : ""}`}>
            <div className="font-semibold text-ink-primary truncate">{r.title}</div>
            <div className="text-[11px] text-ink-tertiary truncate">{[r.sub, r.created_at ? new Date(r.created_at).toLocaleString(getLocale()) : null].filter(Boolean).join(" · ")}</div>
          </div>
          <span className={`tabular-nums font-bold ${r.voided_at ? "line-through text-ink-tertiary" : "text-ink-primary"}`}>{money(r.amount)}</span>
          {r.voided_at
            ? <span className="text-[11px] px-1.5 py-0.5 rounded-md bg-slate-100 text-ink-tertiary" title={r.void_reason || undefined}>Deleted</span>
            : can(perm) && <button onClick={() => del(r)} disabled={busy === r.id} aria-label={`Delete ${noun} ${r.title}`}
                className="p-1 rounded-lg text-ink-tertiary hover:text-rose-600 hover:bg-rose-50 disabled:opacity-50"><M n="delete" c="text-base" /></button>}
        </div>
      ))}
    </div>
  );
}
