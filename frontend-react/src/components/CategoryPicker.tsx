import { useEffect, useRef, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";

// Reusable category picker bound to the shared category tree (category_id). Searchable,
// hierarchical, with inline "+ Create" so you assign a category without leaving the form.
// Replaces the legacy free-text category inputs. entity scopes the tree (product|service).
type Node = { id: string; name: string; children?: Node[] };
type Flat = { id: string; name: string; depth: number };

export default function CategoryPicker({ entity, value, onChange, placeholder = "Uncategorized" }:
  { entity: "product" | "service"; value?: string | null; onChange: (id: string | null) => void; placeholder?: string }) {
  const [flat, setFlat] = useState<Flat[]>([]);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const load = () => api.get<{ tree: Node[] }>(`/catalog/categories?entity_type=${entity}`).then((r) => {
    const out: Flat[] = [];
    const walk = (ns: Node[], d: number) => (ns || []).forEach((n) => { out.push({ id: n.id, name: n.name, depth: d }); if (n.children?.length) walk(n.children, d + 1); });
    walk(r.tree || [], 0); setFlat(out);
  }).catch(() => setFlat([]));
  useEffect(() => { load(); }, [entity]); // eslint-disable-line

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as any)) setOpen(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, [open]);

  const selected = flat.find((f) => f.id === value);
  const term = q.trim().toLowerCase();
  const shown = term ? flat.filter((f) => f.name.toLowerCase().includes(term)) : flat;
  const exact = flat.some((f) => f.name.toLowerCase() === term);

  async function create() {
    const name = q.trim(); if (!name || busy) return; setBusy(true); setErr(null);
    try {
      const r = await api.post<any>("/catalog/categories", { entity_type: entity, name, parent_id: null });
      await load(); onChange(r.id); setQ(""); setOpen(false);
    } catch (e: any) { setErr(e?.message || "Couldn't create category (need inventory-manage permission?)"); } finally { setBusy(false); }
  }

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} className="glass-input w-full px-3 py-2 rounded-lg text-sm text-left flex items-center justify-between gap-2">
        <span className={selected ? "text-ink-primary font-semibold truncate" : "text-ink-tertiary"}>{selected ? selected.name : placeholder}</span>
        <M n="expand_more" c="text-sm text-ink-tertiary flex-shrink-0" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white rounded-xl border border-slate-200 shadow-drawer max-h-72 overflow-y-auto">
          <div className="p-2 sticky top-0 bg-white border-b border-slate-100">
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search or add…"
              onKeyDown={(e) => { if (e.key === "Enter" && term && !exact) { e.preventDefault(); create(); } }}
              className="glass-input w-full px-2.5 py-1.5 rounded-lg text-xs" />
          </div>
          <div className="py-1">
            <button type="button" onClick={() => { onChange(null); setOpen(false); }} className={`w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 ${!value ? "text-primary font-bold" : "text-ink-tertiary"}`}>Uncategorized</button>
            {shown.map((f) => (
              <button type="button" key={f.id} onClick={() => { onChange(f.id); setOpen(false); }} style={{ paddingLeft: 12 + f.depth * 14 }}
                className={`w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 truncate ${f.id === value ? "text-primary font-bold" : "text-ink-secondary"}`}>{f.name}</button>
            ))}
            {term && !exact && (
              <button type="button" onClick={create} disabled={busy} className="w-full text-left px-3 py-2 text-xs font-semibold text-primary hover:bg-primary/5 flex items-center gap-1.5 border-t border-slate-100">
                <M n="add" c="text-xs" />Create “{q.trim()}”
              </button>
            )}
            {shown.length === 0 && !term && <div className="px-3 py-2 text-xs text-ink-tertiary">No categories yet — type a name to add one.</div>}
            {err && <div className="mx-2 my-1 px-2 py-1.5 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-[11px]">{err}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
