import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { M } from "./Shell";

// J4 — pick a job by its number or the customer's name, instead of typing a raw id.
export type PickedJob = { id: string; order_number?: string | null };

export default function JobPicker({ value, onChange, className = "" }: {
  value: PickedJob | null; onChange: (job: PickedJob | null) => void; className?: string;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ id: string; order_number?: string; client?: string; device?: string }[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const term = q.trim();
    if (!term) { setHits([]); return; }
    let live = true;
    const t = setTimeout(() => {
      api.get<{ items: any[] }>(`/orders?q=${encodeURIComponent(term)}&limit=8`)
        .then((r) => { if (live) setHits((r.items || []).map((o: any) => ({ id: o.id, order_number: o.order_number, client: o.client_name || o.client?.name, device: o.device_summary }))); })
        .catch(() => { if (live) setHits([]); });
    }, 220);
    return () => { live = false; clearTimeout(t); };
  }, [q]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  if (value && !editing) {
    return (
      <div className={`flex items-center gap-3 ${className}`}>
        <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-blue-100 text-blue-800 inline-flex items-center gap-1"><M n="handyman" c="text-xs" />{value.order_number || "Job"}</span>
        <button type="button" onClick={() => setEditing(true)} className="text-xs font-semibold text-primary hover:underline">Change</button>
        <button type="button" onClick={() => onChange(null)} className="text-xs font-semibold text-ink-secondary hover:text-ink-primary">Remove</button>
      </div>
    );
  }
  return (
    <div ref={box} className={`relative ${className}`}>
      <input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
        autoFocus={editing} onKeyDown={(e) => { if (e.key === "Escape") { setQ(""); setOpen(false); setEditing(false); } }}
        aria-label="Find a job" placeholder="Job number or customer name"
        className="glass-input w-full py-2 px-3 text-xs rounded-xl" />
      {open && q.trim() && (
        <ul role="listbox" className="absolute z-30 mt-1 w-full max-h-56 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg">
          {hits.length === 0 && <li className="px-3 py-2 text-xs text-ink-tertiary">No jobs match.</li>}
          {hits.map((h) => (
            <li key={h.id}>
              <button type="button" role="option" aria-selected={false}
                onClick={() => { onChange({ id: h.id, order_number: h.order_number }); setQ(""); setOpen(false); setEditing(false); }}
                className="w-full text-left px-3 py-2 text-xs hover:bg-slate-50 flex items-center gap-2">
                <span className="font-mono font-bold text-ink-primary">{h.order_number || "Job"}</span>
                <span className="truncate text-ink-secondary">{[h.client, h.device].filter(Boolean).join(" · ")}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
