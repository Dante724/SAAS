// D3 — tag filter chip for the Contacts list: pick a tag to show only contacts carrying it
// (GET /clients?tag=<id>); the chosen tag shows as a removable chip. Same height and radius
// as the search field next to it.
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";

type Tag = { id: string; name: string; color: string };

const chip = "inline-flex items-center gap-1.5 h-9 rounded-lg border text-xs font-semibold";

export default function TagFilterChip({ value, onChange }: { value: string | null; onChange: (id: string | null) => void }) {
  const [tags, setTags] = useState<Tag[] | null>(null);
  useEffect(() => { api.get<{ items: Tag[] }>("/tags").then((r) => setTags(r.items || [])).catch(() => setTags([])); }, []);
  if (!tags || tags.length === 0) return null;
  const current = tags.find((t) => t.id === value);
  if (value && current) {
    return (
      <span className={`${chip} pl-3 pr-1 bg-primary/10 border-primary/30 text-primary`} title={`Showing contacts tagged ${current.name}`}>
        <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: current.color }} />
        {current.name}
        <button type="button" aria-label="Clear tag filter" onClick={() => onChange(null)} className="w-7 h-7 grid place-items-center rounded-md hover:bg-primary/15"><M n="close" c="text-[14px]" /></button>
      </span>
    );
  }
  return (
    <label className={`${chip} pl-3 pr-2 border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 cursor-pointer`}>
      <M n="sell" c="text-sm text-ink-tertiary" />
      <select aria-label="Filter by tag" value="" onChange={(e) => onChange(e.target.value || null)} className="bg-transparent text-xs font-semibold outline-none cursor-pointer pr-1">
        <option value="">Tag</option>
        {tags.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
    </label>
  );
}
