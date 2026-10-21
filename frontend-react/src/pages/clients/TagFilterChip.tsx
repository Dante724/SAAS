// D3 — tag filter chip for the Contacts list: pick a tag to show only contacts carrying it
// (GET /clients?tag=<id>); the chosen tag shows as a removable chip.
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";

type Tag = { id: string; name: string; color: string };

export default function TagFilterChip({ value, onChange }: { value: string | null; onChange: (id: string | null) => void }) {
  const [tags, setTags] = useState<Tag[] | null>(null);
  useEffect(() => { api.get<{ items: Tag[] }>("/tags").then((r) => setTags(r.items || [])).catch(() => setTags([])); }, []);
  if (!tags || tags.length === 0) return null;
  const current = tags.find((t) => t.id === value);
  if (value && current) {
    return (
      <span className="inline-flex items-center gap-1.5 pl-2.5 pr-1 py-1 rounded-full bg-primary/10 border border-primary/30 text-primary text-xs font-semibold">
        <span className="w-2 h-2 rounded-full" style={{ background: current.color }} />
        Tag: {current.name}
        <button type="button" aria-label="Clear tag filter" onClick={() => onChange(null)} className="w-5 h-5 grid place-items-center rounded-full hover:bg-primary/15"><M n="close" c="text-[14px]" /></button>
      </span>
    );
  }
  return (
    <label className="inline-flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-full border border-slate-200 bg-white text-ink-secondary text-xs font-semibold hover:bg-slate-50 cursor-pointer">
      <M n="sell" c="text-sm" />
      <select aria-label="Filter by tag" value="" onChange={(e) => onChange(e.target.value || null)} className="bg-transparent text-xs font-semibold outline-none cursor-pointer pr-1">
        <option value="">Tag</option>
        {tags.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
    </label>
  );
}
