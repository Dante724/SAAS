import { useEffect, useMemo, useRef, useState } from "react";
import { Braces, ChevronDown, Search } from "lucide-react";

export type VarGroup = { group: string; items: string[] };

/** "Insert variable" button with a searchable list. Picking one inserts that placeholder. */
export default function VariableMenu({ groups, onPick, label = "Variable", onDark = false }: { groups: VarGroup[]; onPick: (tok: string) => void; label?: string; onDark?: boolean }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  const ql = q.trim().toLowerCase();
  const shown = useMemo(() => groups.map((g) => ({ ...g, items: ql ? g.items.filter((i) => i.toLowerCase().includes(ql)) : g.items })).filter((g) => g.items.length), [groups, ql]);
  return (
    <div ref={box} className="relative">
      <button type="button" aria-haspopup="listbox" aria-expanded={open} onMouseDown={(e) => e.preventDefault()} onClick={() => setOpen((o) => !o)}
        className={`h-8 px-2 rounded-lg flex items-center gap-1 text-xs font-semibold ${onDark ? "text-slate-100 hover:bg-white/10" : "text-primary hover:bg-primary/10"}`}>
        <Braces size={15} aria-hidden /> {label} <ChevronDown size={13} aria-hidden />
      </button>
      {open && (
        <div className={`absolute ${onDark ? "right-0" : "left-0"} top-9 z-30 w-72 bg-white border border-slate-200 rounded-xl shadow-drawer p-1.5`}>
          <label className="flex items-center gap-1.5 px-2 h-8 rounded-lg border border-slate-200 bg-white focus-within:border-primary">
            <Search size={14} className="text-ink-tertiary" aria-hidden />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search variables" aria-label="Search variables"
              className="flex-1 bg-transparent text-xs text-ink-primary outline-none" />
          </label>
          <div role="listbox" aria-label="Variables" className="max-h-72 overflow-y-auto mt-1">
            {shown.length === 0 && <div className="px-2 py-3 text-xs text-ink-tertiary">No matches.</div>}
            {shown.map((g) => (
              <div key={g.group} className="py-1">
                <div className="px-2 pb-0.5 text-2xs font-semibold text-ink-tertiary">{g.group}</div>
                {g.items.map((tok) => (
                  <button key={tok} type="button" role="option" aria-selected={false} onMouseDown={(e) => e.preventDefault()}
                    onClick={() => { onPick(tok); setOpen(false); setQ(""); }}
                    className="w-full text-left px-2 py-1 rounded-md text-xs text-ink-primary hover:bg-primary/10 hover:text-primary truncate">{tok.replace(/^\{|\}$/g, "")}</button>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
