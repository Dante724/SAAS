import { useEffect, useState } from "react";
import { api } from "../api/client";

// Brand / model text box with suggestions: what this shop already uses, then the built-in
// US/CA list (phones, tablets, computers, consoles…). Anything typed is fine — the server snaps
// it to the canonical spelling ("samsung" → "Samsung") and remembers new names for next time.
// The list opens on tap (a <datalist> only opens on a second click in Chrome and barely in Safari).
export default function NameSuggestInput({ field, brand, value, onChange, className, placeholder }: {
  field: "brand" | "model"; brand?: string; value: string; onChange: (v: string) => void;
  className?: string; placeholder?: string;
}) {
  const [items, setItems] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(-1);
  const needsBrand = field === "model" && !brand?.trim();
  useEffect(() => {
    if (!open || needsBrand) { setItems([]); return; }
    const t = setTimeout(() => {
      const p = new URLSearchParams({ field, q: value.trim() });
      if (brand) p.set("brand", brand.trim());
      api.get<{ items: string[] }>(`/assets/suggest?${p}`).then((r) => { setItems(r.items || []); setHi(-1); }).catch(() => setItems([]));
    }, value.trim() ? 180 : 0);
    return () => clearTimeout(t);
  }, [field, brand, value, open, needsBrand]);
  const shown = items.filter((i) => i.toLowerCase() !== value.trim().toLowerCase()).slice(0, 12);
  const pick = (v: string) => { onChange(v); setOpen(false); };
  return (
    <div className="relative">
      <input value={value} autoComplete="off" role="combobox" aria-expanded={open && shown.length > 0}
        onChange={(e) => { onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)} onClick={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          else if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setHi((h) => Math.min(h + 1, shown.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
          else if (e.key === "Enter" && open && hi >= 0 && shown[hi]) { e.preventDefault(); pick(shown[hi]); }
        }}
        className={className} placeholder={placeholder ?? (needsBrand ? "Pick a brand first" : undefined)} />
      {open && shown.length > 0 && (
        <div role="listbox" className="absolute z-20 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1 max-h-56 overflow-y-auto">
          {shown.map((i, n) => (
            <button key={i} type="button" role="option" aria-selected={n === hi}
              onMouseDown={(e) => { e.preventDefault(); pick(i); }}
              className={`w-full text-left px-2.5 py-1.5 rounded-lg text-xs text-ink-primary ${n === hi ? "bg-blue-50/80" : "hover:bg-slate-50"}`}>{i}</button>
          ))}
        </div>
      )}
    </div>
  );
}
