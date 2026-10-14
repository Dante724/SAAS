import { useEffect, useRef, useState } from "react";

/**
 * Glass-clay date / time / datetime field. Drop-in replacement for the native
 * <input type="date|time|datetime-local"> we used app-wide — same string value
 * semantics, but our own popover so the OS calendar chrome never appears.
 *
 * value / onChange strings match the native inputs they replace:
 *   date     -> "YYYY-MM-DD"
 *   time     -> "HH:MM"        (24h)
 *   datetime -> "YYYY-MM-DDTHH:MM"
 *
 * ponytail: absolute-positioned popover (relative wrapper). Upgrade to a portal
 * only if a scroll-clipped drawer actually cuts it off.
 */

type Mode = "date" | "time" | "datetime";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DOW = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

function splitVal(v: string): { date: string; time: string } {
  if (!v) return { date: "", time: "" };
  const [d, t] = v.split("T");
  return { date: d || "", time: (t || "").slice(0, 5) };
}
function joinVal(mode: Mode, date: string, time: string): string {
  if (mode === "time") return time;
  if (mode === "date") return date;
  return date ? `${date}T${time || "00:00"}` : "";
}
function pad(n: number) { return String(n).padStart(2, "0"); }
function ymd(d: Date) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }

function fmtDate(iso: string): string {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return "";
  return `${d} ${MONTHS[m - 1]} ${y}`;
}
function fmtTime(hm: string): string {
  if (!hm) return "";
  const [h, m] = hm.split(":").map(Number);
  const ap = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${pad(m || 0)} ${ap}`;
}

export default function DateField({
  mode = "date",
  value,
  onChange,
  className = "",
  placeholder,
  min,
  disabled = false,
}: {
  mode?: Mode;
  value: string;
  onChange: (v: string) => void;
  className?: string;
  placeholder?: string;
  min?: string; // "YYYY-MM-DD" lower bound for date selection
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const { date, time } = splitVal(value);

  // month the calendar is showing
  const initial = date ? new Date(date + "T00:00") : new Date();
  const [view, setView] = useState({ y: initial.getFullYear(), m: initial.getMonth() });

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  useEffect(() => { // re-sync view when value changes externally
    if (date) { const d = new Date(date + "T00:00"); setView({ y: d.getFullYear(), m: d.getMonth() }); }
  }, [date]);

  const label =
    mode === "time" ? (fmtTime(time) || placeholder || "Select time")
      : mode === "date" ? (fmtDate(date) || placeholder || "Select date")
        : (date ? `${fmtDate(date)}${time ? `, ${fmtTime(time)}` : ""}` : (placeholder || "Select date & time"));
  const empty = mode === "time" ? !time : !date;

  const pickDate = (iso: string) => {
    onChange(joinVal(mode, iso, time || (mode === "datetime" ? "09:00" : "")));
    if (mode === "date") setOpen(false);
  };
  const pickTime = (hm: string) => onChange(joinVal(mode, date || ymd(new Date()), hm));

  // calendar grid for the viewed month (Monday-first)
  const first = new Date(view.y, view.m, 1);
  const startPad = (first.getDay() + 6) % 7; // Mon=0
  const daysIn = new Date(view.y, view.m + 1, 0).getDate();
  const cells: (number | null)[] = [];
  for (let i = 0; i < startPad; i++) cells.push(null);
  for (let d = 1; d <= daysIn; d++) cells.push(d);
  const todayIso = ymd(new Date());

  const showTime = mode === "time" || mode === "datetime";
  const showCal = mode === "date" || mode === "datetime";
  const [th, tm] = (time || "").split(":");

  return (
    <div ref={wrap} className={`relative ${className}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className={`glass-input rounded-lg px-3 py-2 text-sm w-full flex items-center justify-between gap-2 text-left disabled:opacity-50 disabled:cursor-not-allowed ${empty ? "text-ink-tertiary" : "text-ink-primary font-semibold"}`}
      >
        <span className="truncate">{label}</span>
        <span className="material-symbols-outlined text-base text-primary shrink-0">{mode === "time" ? "schedule" : "calendar_month"}</span>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 left-0 bg-white dark:bg-[#151b2e] rounded-xl shadow-clay-card border border-slate-200/80 p-2 w-[212px]">
          {showCal && (
            <>
              <div className="flex items-center justify-between mb-1">
                <button type="button" onClick={() => setView((v) => { const m = v.m - 1; return m < 0 ? { y: v.y - 1, m: 11 } : { y: v.y, m }; })} className="w-6 h-6 grid place-items-center rounded-md hover:bg-slate-100 text-ink-secondary">
                  <span className="material-symbols-outlined text-sm">chevron_left</span>
                </button>
                <span className="text-xs font-bold text-ink-primary">{MONTHS[view.m]} {view.y}</span>
                <button type="button" onClick={() => setView((v) => { const m = v.m + 1; return m > 11 ? { y: v.y + 1, m: 0 } : { y: v.y, m }; })} className="w-6 h-6 grid place-items-center rounded-md hover:bg-slate-100 text-ink-secondary">
                  <span className="material-symbols-outlined text-sm">chevron_right</span>
                </button>
              </div>
              <div className="grid grid-cols-7 gap-px mb-0.5">
                {DOW.map((d) => <div key={d} className="text-[9px] font-bold text-ink-tertiary text-center">{d}</div>)}
              </div>
              <div className="grid grid-cols-7 gap-px">
                {cells.map((d, i) => {
                  if (d === null) return <div key={i} />;
                  const iso = `${view.y}-${pad(view.m + 1)}-${pad(d)}`;
                  const sel = iso === date;
                  const isToday = iso === todayIso;
                  const disabled = min ? iso < min : false;
                  return (
                    <button
                      key={i}
                      type="button"
                      disabled={disabled}
                      onClick={() => pickDate(iso)}
                      className={`h-6 w-6 rounded-md text-[11px] flex items-center justify-center transition-colors
                        ${sel ? "bg-primary text-white font-bold" : disabled ? "text-ink-placeholder cursor-not-allowed" : "text-ink-primary hover:bg-blue-50"}
                        ${isToday && !sel ? "ring-1 ring-primary/40" : ""}`}
                    >{d}</button>
                  );
                })}
              </div>
            </>
          )}

          {showTime && (
            <div className={`flex items-center gap-1.5 ${showCal ? "mt-2 pt-2 border-t border-slate-100" : ""}`}>
              <span className="material-symbols-outlined text-sm text-ink-tertiary">schedule</span>
              <select value={th ?? "09"} onChange={(e) => pickTime(`${e.target.value}:${tm ?? "00"}`)} className="glass-input rounded-md px-1.5 py-1 text-xs font-semibold text-ink-primary">
                {Array.from({ length: 24 }, (_, h) => <option key={h} value={pad(h)}>{pad(h)}</option>)}
              </select>
              <span className="text-ink-tertiary font-bold text-xs">:</span>
              <select value={tm ?? "00"} onChange={(e) => pickTime(`${th ?? "09"}:${e.target.value}`)} className="glass-input rounded-md px-1.5 py-1 text-xs font-semibold text-ink-primary">
                {Array.from({ length: 12 }, (_, i) => pad(i * 5)).map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <span className="text-[10px] text-ink-tertiary ml-auto">{fmtTime(`${th ?? "09"}:${tm ?? "00"}`)}</span>
            </div>
          )}

          {(mode === "date" || mode === "datetime") && (
            <div className="flex justify-between mt-2 pt-1.5 border-t border-slate-100">
              <button type="button" onClick={() => pickDate(todayIso)} className="text-[11px] font-semibold text-primary hover:underline">Today</button>
              {!empty && <button type="button" onClick={() => { onChange(""); setOpen(false); }} className="text-[11px] font-semibold text-ink-tertiary hover:text-rose-600">Clear</button>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
