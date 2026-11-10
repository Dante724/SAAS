import { Children, isValidElement, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type SelectHTMLAttributes } from "react";
import { createPortal } from "react-dom";
import { Check, Search } from "lucide-react";

// The app's one dropdown. It takes the same props and children as a native <select> (value,
// onChange, disabled, className, <option>/<optgroup>), so a `<select>` becomes `<TeklySelect>`
// with nothing else changed, and `onChange={(e) => …e.target.value}` keeps working.
// - The closed control keeps the caller's className, so it sits and sizes exactly like before.
// - The open list is a Tekly panel (light and dark) instead of the operating system's menu;
//   long lists (more than 12 choices) get a search box.
// - On touch screens (phones, tablets) it stays a native <select>: their wheel picker is better.

type Opt = { value: string; label: string; disabled?: boolean; group?: string };

function textOf(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node)) return textOf((node.props as any).children);
  return "";
}

function collect(children: ReactNode, group?: string, out: Opt[] = []): Opt[] {
  Children.forEach(children, (c) => {
    if (!isValidElement(c)) return;
    const p = c.props as any;
    if (c.type === "option") out.push({ value: String(p.value ?? textOf(p.children)), label: textOf(p.children), disabled: !!p.disabled, group });
    else if (c.type === "optgroup") collect(p.children, p.label, out);
    else if (p && p.children) collect(p.children, group, out);   // fragments and wrappers
  });
  return out;
}

const isTouch = () => typeof window !== "undefined" && !!window.matchMedia?.("(pointer: coarse)").matches;
// Unit tests drive the native control (fireEvent / selectOptions); TeklySelect.test.tsx covers the panel.
const NATIVE_IN_TESTS = import.meta.env.MODE === "test";

export default function TeklySelect(props: SelectHTMLAttributes<HTMLSelectElement> & { forcePanel?: boolean }) {
  const { forcePanel, ...native } = props;
  const { children, value, onChange, disabled, className = "", id, title, style, onBlur, autoFocus, ...rest } = native;
  const [touch] = useState(isTouch);
  if (!forcePanel && (touch || NATIVE_IN_TESTS)) return <select {...native} />;
  const aria = Object.fromEntries(Object.entries(rest).filter(([k]) => k.startsWith("aria-") || k.startsWith("data-")));
  return <Menu opts={collect(children)} value={value == null ? "" : String(value)} disabled={!!disabled} className={className}
    id={id} title={title} style={style} autoFocus={autoFocus} aria={aria}
    onBlur={onBlur as any}
    onPick={(v) => onChange?.({ target: { value: v }, currentTarget: { value: v } } as any)} />;
}

function Menu({ opts, value, disabled, className, id, title, style, autoFocus, aria, onBlur, onPick }: {
  opts: Opt[]; value: string; disabled: boolean; className: string; id?: string; title?: string; style?: React.CSSProperties;
  autoFocus?: boolean; aria: Record<string, any>; onBlur?: () => void; onPick: (v: string) => void;
}) {
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(-1);
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number; width: number; maxH: number } | null>(null);
  const cur = opts.find((o) => o.value === value);
  const searchable = opts.length > 12;
  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    return n ? opts.filter((o) => o.label.toLowerCase().includes(n) || o.value.toLowerCase().includes(n)) : opts;
  }, [opts, q]);

  const place = () => {
    const r = btn.current?.getBoundingClientRect(); if (!r) return;
    const below = window.innerHeight - r.bottom - 8, above = r.top - 8;
    const width = Math.max(r.width, 200);
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));   // grows rightward up to 420px (see maxWidth)
    if (below >= 240 || below >= above) setPos({ left, top: r.bottom + 6, width, maxH: Math.min(360, below - 6) });
    else setPos({ left, bottom: window.innerHeight - r.top + 6, width, maxH: Math.min(360, above - 6) });
  };
  useLayoutEffect(() => { if (open) place(); }, [open]); // eslint-disable-line
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => { if (!panel.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) setOpen(false); };
    const reflow = (e: Event) => { if (!panel.current?.contains(e.target as Node)) place(); };
    document.addEventListener("mousedown", close);
    window.addEventListener("resize", place); window.addEventListener("scroll", reflow, true);
    return () => { document.removeEventListener("mousedown", close); window.removeEventListener("resize", place); window.removeEventListener("scroll", reflow, true); };
  }, [open]); // eslint-disable-line
  useEffect(() => { if (open) { setActive(Math.max(0, shown.findIndex((o) => o.value === value))); } else { setQ(""); } }, [open]); // eslint-disable-line
  useEffect(() => { (panel.current?.querySelector(`[data-i="${active}"]`) as HTMLElement | null)?.scrollIntoView?.({ block: "nearest" }); }, [active]);

  const pick = (o?: Opt) => { if (!o || o.disabled) return; setOpen(false); btn.current?.focus(); if (o.value !== value) onPick(o.value); };
  const move = (d: number) => {
    if (!shown.length) return;
    let i = active;
    for (let k = 0; k < shown.length; k++) { i = (i + d + shown.length) % shown.length; if (!shown[i].disabled) break; }
    setActive(i);
  };
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); open ? move(1) : setOpen(true); }
    else if (e.key === "ArrowUp") { e.preventDefault(); open ? move(-1) : setOpen(true); }
    else if (e.key === "Enter" || (e.key === " " && !searchable)) { if (open) { e.preventDefault(); pick(shown[active]); } }
    else if (e.key === "Escape" && open) { e.preventDefault(); e.stopPropagation(); setOpen(false); btn.current?.focus(); }
    else if (e.key === "Tab") setOpen(false);
    else if (!searchable && open && e.key.length === 1) {   // type-ahead on short lists
      const i = shown.findIndex((o) => o.label.toLowerCase().startsWith(e.key.toLowerCase())); if (i >= 0) setActive(i);
    }
  };

  let lastGroup: string | undefined;
  return (
    <>
      <button ref={btn} type="button" id={id} title={title} style={style} disabled={disabled} autoFocus={autoFocus} {...aria}
        role="combobox" aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
        onClick={() => setOpen((o) => !o)} onKeyDown={keys} onBlur={onBlur}
        className={`tk-select text-left truncate ${/\bpr-/.test(className) ? "" : "pr-8"} ${className} ${disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}>
        {/* Not positioned, so an icon the caller lays over the control (a person icon, its own chevron)
            stays visible; the arrow is a background image (index.css .tk-select), skipped with bg-none. */}
        <span className="block truncate">{cur ? cur.label : (opts[0]?.label ?? "")}</span>
      </button>
      {open && pos && createPortal(
        <div ref={panel} onKeyDown={keys}
          className="fixed z-[200] bg-white rounded-2xl border border-slate-200/90 shadow-drawer p-1.5 flex flex-col"
          style={{ left: pos.left, top: pos.top, bottom: pos.bottom, minWidth: pos.width, width: "max-content", maxWidth: Math.min(420, window.innerWidth - pos.left - 8), maxHeight: pos.maxH }}>
          {searchable && (
            <div className="relative p-1 flex-shrink-0">
              <Search aria-hidden size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-tertiary pointer-events-none" />
              <input autoFocus value={q} onChange={(e) => { setQ(e.target.value); setActive(0); }} placeholder="Search…" aria-label="Search the list"
                className="glass-input w-full h-9 pl-8 pr-3 rounded-xl text-sm" />
            </div>
          )}
          <div id={listId} role="listbox" className="overflow-y-auto min-h-0">
            {shown.map((o, i) => {
              const head = o.group && o.group !== lastGroup ? o.group : null; lastGroup = o.group;
              const on = o.value === value;
              return (
                <div key={`${o.group || ""}:${o.value}:${i}`}>
                  {head && <p className="px-3 pt-2 pb-1 text-2xs font-semibold text-ink-tertiary">{head}</p>}
                  <button type="button" role="option" aria-selected={on} data-i={i} disabled={o.disabled}
                    onMouseEnter={() => setActive(i)} onClick={() => pick(o)}
                    className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left text-sm disabled:opacity-40 disabled:cursor-not-allowed ${on ? "text-primary font-semibold" : "text-ink-primary"} ${i === active ? "bg-slate-100" : ""}`}>
                    <span className="flex-1 truncate">{o.label}</span>
                    <Check aria-hidden size={15} className={on ? "text-primary" : "invisible"} />
                  </button>
                </div>
              );
            })}
            {shown.length === 0 && <p className="px-3 py-3 text-xs text-ink-tertiary">Nothing matches “{q}”.</p>}
          </div>
        </div>, document.body)}
    </>
  );
}
