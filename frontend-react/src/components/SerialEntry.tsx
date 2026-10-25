import { useEffect, useMemo, useRef, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { addSerials, localCheck, mergeServer, MAX_SERIAL_LEN, SerialRow, SerialStatus } from "../pages/inventory/serialList";

/** 16b — scan or paste serials / IMEIs for serial-tracked stock, one per unit.
 * Scanners type the code and press Enter, so focus stays in the box for the next scan.
 * Each entry is checked as you go: repeats and units already in stock are blocked; a wrong
 * IMEI check digit or a unit sold before is a warning. The server enforces the same rules. */
export default function SerialEntry({ value, onChange, target, partId, onState, autoFocus, others }: {
  value: string[];
  onChange: (v: string[]) => void;
  target?: number;                         // how many units this line needs
  partId?: string | null;
  onState?: (s: { blocked: number; warned: number; checking: boolean }) => void;
  autoFocus?: boolean;
  others?: string[];                       // serials on the other lines of the same delivery
}) {
  const [text, setText] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [server, setServer] = useState<{ key: string; rows: { status: SerialStatus; message?: string | null }[] | null; error?: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const othersKey = (others || []).join("\n");
  const local = useMemo(() => localCheck(value, others || []), [value, othersKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const key = value.join("\n");
  const tooLong = value.some((v) => v.length > MAX_SERIAL_LEN);

  // Ask the server once typing pauses; a stale answer (list changed since) is ignored. An
  // over-long entry is already blocked here, and would make the server refuse the whole list.
  useEffect(() => {
    if (!value.length || tooLong) { setServer(null); return; }
    const t = setTimeout(() => {
      api.post<any>("/inventory/serials/check", { serials: value, part_id: partId || null })
        .then((r) => setServer({ key, rows: r.rows || [] }))
        .catch(() => setServer({ key, rows: null, error: "Couldn't check these against your stock just now. They'll be checked again when you save." }));
    }, 300);
    return () => clearTimeout(t);
  }, [key, partId, tooLong]); // eslint-disable-line react-hooks/exhaustive-deps

  const fresh = server && server.key === key ? server : null;
  const rows: SerialRow[] = mergeServer(local, fresh ? fresh.rows : null);
  const checking = value.length > 0 && !tooLong && !fresh;
  const blocked = rows.filter((r) => r.status === "block").length;
  const warned = rows.filter((r) => r.status === "warn").length;
  useEffect(() => { onState?.({ blocked, warned, checking }); }, [blocked, warned, checking]); // eslint-disable-line react-hooks/exhaustive-deps

  function add(raw: string) {
    if (!raw.trim()) return;
    const r = addSerials(value, raw);
    if (target && r.list.length > target) setNote(`Only ${target} needed — remove one first.`);
    else setNote(r.skipped ? `${r.skipped} already in the list — skipped.` : null);
    onChange(target ? r.list.slice(0, Math.max(target, value.length)) : r.list);
    setText("");
    inputRef.current?.focus();
  }
  const remove = (i: number) => { onChange(value.filter((_, j) => j !== i)); inputRef.current?.focus(); };

  const tone = (s: SerialStatus) => s === "block" ? "border-rose-200 bg-rose-50 text-rose-700"
    : s === "warn" ? "border-amber-200 bg-amber-50 text-amber-800" : "border-slate-200 bg-white text-ink-primary";
  const done = target ? value.length >= target : false;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <M n="barcode_scanner" c="absolute left-2.5 top-2 text-ink-tertiary text-sm" />
          <input ref={inputRef} autoFocus={autoFocus} value={text} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(text); } }}
            onPaste={(e) => { const t = e.clipboardData.getData("text"); if (/[\r\n,;\t]/.test(t.trim())) { e.preventDefault(); add(t); } }}
            disabled={done}
            aria-label="Scan or type a serial or IMEI"
            placeholder={done ? "All units have a serial" : "Scan or type a serial / IMEI, then Enter — or paste a list"}
            className="glass-input w-full pl-8 pr-3 py-1.5 text-xs rounded-lg font-mono disabled:opacity-60" />
        </div>
        <span className={`text-[11px] font-bold tabular-nums whitespace-nowrap ${target && value.length === target ? "text-emerald-700" : "text-ink-secondary"}`}>
          {target ? `${value.length} of ${target}` : `${value.length} entered`}
        </span>
      </div>
      {note && <p className="text-[11px] text-ink-tertiary">{note}</p>}
      {rows.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Entered serials">
          {rows.map((r, i) => (
            <li key={`${r.serial}-${i}`} title={r.message || undefined}
              className={`flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-md border text-[11px] font-mono ${tone(r.status)}`}>
              {r.status === "block" && <M n="block" c="text-[12px]" />}
              {r.status === "warn" && <M n="warning" c="text-[12px]" />}
              <span>{r.serial || r.input}</span>
              <button type="button" aria-label={`Remove ${r.serial || r.input}`} onClick={() => remove(i)}
                className="w-4 h-4 grid place-items-center rounded hover:bg-black/5"><M n="close" c="text-[12px]" /></button>
            </li>
          ))}
        </ul>
      )}
      {rows.some((r) => r.message && r.status !== "ok") && (
        <ul className="space-y-0.5">
          {rows.filter((r) => r.message && r.status !== "ok").map((r, i) => (
            <li key={i} className={`text-[11px] ${r.status === "block" ? "text-rose-700" : "text-amber-800"}`}>
              <span className="font-mono">{r.serial || r.input}</span> — {r.message}
            </li>
          ))}
        </ul>
      )}
      {checking && <p className="text-[11px] text-ink-tertiary">Checking…</p>}
      {fresh?.error && <p className="text-[11px] text-amber-800">{fresh.error}</p>}
    </div>
  );
}
