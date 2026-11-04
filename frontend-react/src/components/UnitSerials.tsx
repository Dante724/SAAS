import { useEffect, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { inputCls } from "../pages/invoices/billing";

/** QA F2 — the IMEI / serial of each unit of a serialized product that moves: sold at the till, put
 * in a transfer box, written off or brought back. Scan or type one per unit (Enter adds it), or pick
 * from the units that can move (`status`: in stock for most moves, sold for a customer return). */
export default function UnitSerials({ partId, need, value, onChange, label, status = "in_stock", disabled = false }: {
  partId: string;
  need: number;                          // how many units this line moves
  value: string[];
  onChange: (v: string[]) => void;
  label: string;                         // the product, for the field's accessible name
  status?: "in_stock" | "sold";
  disabled?: boolean;                    // the sale is saved: show the units, change nothing
}) {
  const [text, setText] = useState("");
  const [units, setUnits] = useState<string[]>([]);
  useEffect(() => {
    if (!partId) { setUnits([]); return; }
    api.get<{ items: { serial_number: string; status: string }[] }>(`/serials?part_id=${encodeURIComponent(partId)}&status=${status}`)
      .then((r) => setUnits((r.items || []).filter((u) => u.status === status).map((u) => u.serial_number)))
      .catch(() => setUnits([]));
  }, [partId, status]);
  const add = (raw: string) => {
    const sn = raw.trim();
    if (!sn || value.includes(sn) || value.length >= need) { setText(""); return; }
    onChange([...value, sn]); setText("");
  };
  const left = need - value.length;
  const listId = `sn-${partId}-${status}`;
  return (
    <div className="mt-2 space-y-1.5">
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {value.map((sn) => (
            <span key={sn} className="inline-flex items-center gap-1 h-6 pl-2 pr-1 rounded-md border border-slate-200 bg-slate-50 text-[11px] font-mono text-ink-primary">
              {sn}{!disabled && <button type="button" aria-label={`Remove ${sn}`} onClick={() => onChange(value.filter((x) => x !== sn))} className="w-5 h-5 grid place-items-center rounded text-ink-tertiary hover:text-rose-600"><M n="close" c="text-xs" /></button>}
            </span>
          ))}
        </div>
      )}
      {left > 0 && !disabled && (
        <>
          <input value={text} onChange={(e) => setText(e.target.value)} list={listId} aria-label={`IMEI or serial for ${label}`}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(text); } }}
            onBlur={() => { if (units.includes(text.trim())) add(text); }}
            className={`${inputCls} h-9 text-xs font-mono !border-amber-300`} placeholder={left === 1 ? "Scan or type the IMEI / serial" : `Scan or type ${left} more IMEIs / serials`} />
          <datalist id={listId}>{units.filter((u) => !value.includes(u)).map((u) => <option key={u} value={u} />)}</datalist>
        </>
      )}
    </div>
  );
}

/** Lines of a serialized product still missing an IMEI / serial for some unit. */
export function unnamedUnits<T extends { serialized?: boolean; serials?: string[]; qty: number }>(lines: T[]): T[] {
  return lines.filter((l) => l.serialized && (l.serials || []).length < l.qty);
}
