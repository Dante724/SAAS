import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { M } from "./Shell";
import { fmtDay } from "../lib/serverTime";

/**
 * Date / time / datetime field. Drop-in replacement for the native
 * <input type="date|time|datetime-local"> we used app-wide — same string value
 * semantics, but our own popover so the OS calendar chrome never appears.
 *
 * value / onChange strings match the native inputs they replace:
 *   date     -> "YYYY-MM-DD"
 *   time     -> "HH:MM"        (24h)
 *   datetime -> "YYYY-MM-DDTHH:MM"
 *
 * The popover is portalled to <body> and fixed-positioned against the field, so a drawer's
 * scroll area can't clip it. It lines up with the field's right edge (under the calendar icon)
 * and opens upward when there's no room below. Clicking the month or year jumps by month/year,
 * so a birthday decades back is three clicks, not hundreds.
 */

type Mode = "date" | "time" | "datetime";
type View = "days" | "months" | "years";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DOW = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
const POP_W = 288;
const GAP = 6;

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
  return fmtDay(iso);   // the app's one date format ("Oct 29, 2026")
}
function fmtTime(hm: string): string {
  if (!hm) return "";
  const [h, m] = hm.split(":").map(Number);
  const ap = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${pad(m || 0)} ${ap}`;
}

const navBtn = "w-8 h-8 grid place-items-center rounded-lg text-ink-secondary hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40";
const cellBase = "rounded-lg text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40";

export default function DateField({
  mode = "date",
  value,
  onChange,
  className = "",
  placeholder,
  min,
  disabled = false,
  label: ariaName,
}: {
  mode?: Mode;
  value: string;
  onChange: (v: string) => void;
  className?: string;
  placeholder?: string;
  min?: string; // "YYYY-MM-DD" lower bound for date selection
  disabled?: boolean;
  label?: string; // accessible name when the field has no visible <label>
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<View>("days");
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const { date, time } = splitVal(value);

  // month the calendar is showing
  const initial = date ? new Date(date + "T00:00") : new Date();
  const [cur, setCur] = useState({ y: initial.getFullYear(), m: initial.getMonth() });

  // Place the popover under the field, right edges aligned; above it if there's no room below.
  const place = () => {
    const b = btn.current?.getBoundingClientRect();
    if (!b) return;
    const h = pop.current?.offsetHeight ?? 340;
    const below = window.innerHeight - b.bottom;
    let top = below < h + GAP && b.top > below ? b.top - h - GAP : b.bottom + GAP;
    top = Math.min(Math.max(8, top), Math.max(8, window.innerHeight - h - 8));   // short windows: stay on screen
    const left = Math.min(Math.max(8, b.right - POP_W), window.innerWidth - POP_W - 8);
    setPos({ top, left });
  };
  useLayoutEffect(() => { if (open) place(); }, [open, view]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (wrap.current?.contains(t) || pop.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); btn.current?.focus(); } };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);   // any scroll container, not just the window
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  useEffect(() => { // re-sync view when value changes externally
    if (date) { const d = new Date(date + "T00:00"); setCur({ y: d.getFullYear(), m: d.getMonth() }); }
  }, [date]);

  const label =
    mode === "time" ? (fmtTime(time) || placeholder || "Select time")
      : mode === "date" ? (fmtDate(date) || placeholder || "Select date")
        : (date ? `${fmtDate(date)}${time ? `, ${fmtTime(time)}` : ""}` : (placeholder || "Select date & time"));
  const empty = mode === "time" ? !time : !date;

  const toggle = () => { setView("days"); setOpen((o) => !o); };
  const pickDate = (iso: string) => {
    onChange(joinVal(mode, iso, time || (mode === "datetime" ? "09:00" : "")));
    if (mode === "date") setOpen(false);
  };
  const pickTime = (hm: string) => onChange(joinVal(mode, date || ymd(new Date()), hm));
  const shift = (n: number) => setCur((v) => {
    if (view === "years") return { ...v, y: v.y + 12 * n };
    if (view === "months") return { ...v, y: v.y + n };
    const m = v.m + n;
    return m < 0 ? { y: v.y - 1, m: 11 } : m > 11 ? { y: v.y + 1, m: 0 } : { y: v.y, m };
  });

  // calendar grid for the viewed month (Monday-first)
  const first = new Date(cur.y, cur.m, 1);
  const startPad = (first.getDay() + 6) % 7; // Mon=0
  const daysIn = new Date(cur.y, cur.m + 1, 0).getDate();
  const cells: (number | null)[] = [];
  for (let i = 0; i < startPad; i++) cells.push(null);
  for (let d = 1; d <= daysIn; d++) cells.push(d);
  const todayIso = ymd(new Date());
  const selY = date ? Number(date.slice(0, 4)) : null;
  const selM = date ? Number(date.slice(5, 7)) - 1 : null;
  const yearStart = cur.y - (((cur.y % 12) + 12) % 12);   // 12-year page containing cur.y

  const showTime = mode === "time" || mode === "datetime";
  const showCal = mode === "date" || mode === "datetime";
  const [th, tm] = (time || "").split(":");

  const popover = open && createPortal(
    <div
      ref={pop}
      role="dialog"
      aria-label={showCal ? "Choose a date" : "Choose a time"}
      style={{ position: "fixed", top: pos?.top ?? -9999, left: pos?.left ?? -9999, width: POP_W, zIndex: 1000 }}
      className="bg-raised rounded-2xl shadow-clay-card border border-slate-200/80 p-3"
    >
      {showCal && (
        <>
          <div className="flex items-center justify-between mb-2">
            <button type="button" aria-label="Previous" onClick={() => shift(-1)} className={navBtn}>
              <M n="chevron_left" c="text-lg" />
            </button>
            <div className="flex items-center gap-0.5">
              {view === "days" && (
                <button type="button" onClick={() => setView("months")} className="px-2 h-8 rounded-lg text-sm font-semibold text-ink-primary hover:bg-slate-100">
                  {MONTHS_LONG[cur.m]}
                </button>
              )}
              <button type="button" onClick={() => setView(view === "years" ? "days" : "years")} className="px-2 h-8 rounded-lg text-sm font-semibold text-ink-primary hover:bg-slate-100">
                {view === "years" ? `${yearStart} – ${yearStart + 11}` : cur.y}
              </button>
            </div>
            <button type="button" aria-label="Next" onClick={() => shift(1)} className={navBtn}>
              <M n="chevron_right" c="text-lg" />
            </button>
          </div>

          {view === "days" && (
            <>
              <div className="grid grid-cols-7 mb-1">
                {DOW.map((d) => <div key={d} className="h-8 grid place-items-center text-xs font-medium text-ink-tertiary">{d}</div>)}
              </div>
              <div className="grid grid-cols-7 gap-0.5">
                {cells.map((d, i) => {
                  if (d === null) return <div key={i} />;
                  const iso = `${cur.y}-${pad(cur.m + 1)}-${pad(d)}`;
                  const sel = iso === date;
                  const isToday = iso === todayIso;
                  const off = min ? iso < min : false;
                  return (
                    <button
                      key={i}
                      type="button"
                      disabled={off}
                      aria-pressed={sel}
                      onClick={() => pickDate(iso)}
                      className={`${cellBase} h-9 w-full grid place-items-center
                        ${sel ? "bg-primary text-white font-semibold" : off ? "text-ink-placeholder cursor-not-allowed" : "text-ink-primary hover:bg-slate-100"}
                        ${isToday && !sel ? "font-semibold text-primary" : ""}`}
                    >{d}</button>
                  );
                })}
              </div>
            </>
          )}

          {view === "months" && (
            <div className="grid grid-cols-3 gap-1">
              {MONTHS.map((m, i) => (
                <button key={m} type="button" onClick={() => { setCur((v) => ({ ...v, m: i })); setView("days"); }}
                  className={`${cellBase} h-11 ${selY === cur.y && selM === i ? "bg-primary text-white font-semibold" : "text-ink-primary hover:bg-slate-100"}`}>{m}</button>
              ))}
            </div>
          )}

          {view === "years" && (
            <div className="grid grid-cols-3 gap-1">
              {Array.from({ length: 12 }, (_, i) => yearStart + i).map((y) => (
                <button key={y} type="button" onClick={() => { setCur((v) => ({ ...v, y })); setView("months"); }}
                  className={`${cellBase} h-11 ${selY === y ? "bg-primary text-white font-semibold" : "text-ink-primary hover:bg-slate-100"}`}>{y}</button>
              ))}
            </div>
          )}
        </>
      )}

      {showTime && (
        <div className={`flex items-center gap-2 ${showCal ? "mt-3 pt-3 border-t border-slate-100" : ""}`}>
          <M n="schedule" c="text-base text-ink-tertiary" />
          <select aria-label="Hour" value={th ?? "09"} onChange={(e) => pickTime(`${e.target.value}:${tm ?? "00"}`)} className="glass-input rounded-lg px-2 py-1.5 text-sm text-ink-primary">
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={pad(h)}>{pad(h)}</option>)}
          </select>
          <span className="text-ink-tertiary">:</span>
          <select aria-label="Minute" value={tm ?? "00"} onChange={(e) => pickTime(`${th ?? "09"}:${e.target.value}`)} className="glass-input rounded-lg px-2 py-1.5 text-sm text-ink-primary">
            {Array.from({ length: 12 }, (_, i) => pad(i * 5)).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <span className="text-xs text-ink-tertiary ml-auto">{fmtTime(`${th ?? "09"}:${tm ?? "00"}`)}</span>
        </div>
      )}

      {showCal && (
        <div className="flex justify-between mt-3 pt-2 border-t border-slate-100">
          <button type="button" onClick={() => pickDate(todayIso)} className="text-sm font-medium text-primary px-1 rounded hover:underline">Today</button>
          {!empty && <button type="button" onClick={() => { onChange(""); setOpen(false); }} className="text-sm font-medium text-ink-tertiary px-1 rounded hover:text-rose-600">Clear</button>}
        </div>
      )}
    </div>,
    document.body,
  );

  return (
    <div ref={wrap} className={`relative ${className}`}>
      <button
        ref={btn}
        type="button"
        disabled={disabled}
        aria-haspopup="dialog"
        aria-label={ariaName ? `${ariaName}: ${label}` : undefined}
        aria-expanded={open}
        onClick={toggle}
        className={`glass-input rounded-xl px-3 py-2 text-sm w-full flex items-center justify-between gap-2 text-left disabled:opacity-50 disabled:cursor-not-allowed ${empty ? "text-ink-tertiary" : "text-ink-primary"}`}
      >
        <span className="truncate">{label}</span>
        <M n={mode === "time" ? "schedule" : "calendar_today"} c="text-lg text-ink-tertiary shrink-0" />
      </button>
      {popover}
    </div>
  );
}
