// D3 — tag filter chip for the Contacts list: pick a tag to show only contacts carrying it
// (GET /clients?tag=<id>); the chosen tag shows as a removable chip. Same height and radius
// as the search field next to it.
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";

type Tag = { id: string; name: string; color: string };

const chip = "inline-flex items-center gap-1.5 h-9 rounded-xl text-xs font-semibold";

export default function TagFilterChip({ value, onChange, disabled = false }: { value: string | null; onChange: (id: string | null) => void; disabled?: boolean }) {
  const [tags, setTags] = useState<Tag[] | null>(null);
  useEffect(() => { api.get<{ items: Tag[] }>("/tags").then((r) => setTags(r.items || [])).catch(() => setTags([])); }, []);
  if (!tags || tags.length === 0) return null;
  const current = tags.find((t) => t.id === value);
  if (value && current) {
    return (
      <span className={`${chip} pl-3 pr-1 border bg-primary/10 border-primary/30 text-primary`} title={`Showing contacts tagged ${current.name}`}>
        <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: current.color }} />
        {current.name}
        <button type="button" aria-label="Clear tag filter" disabled={disabled} onClick={() => onChange(null)} className="hit44 w-7 h-7 grid place-items-center rounded-lg hover:bg-primary/15 disabled:opacity-50"><M n="close" c="text-sm" /></button>
      </span>
    );
  }
  return (
    <label className={`${chip} glass-input pl-3 pr-2 text-ink-secondary ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}>
      <M n="sell" c="text-sm text-ink-tertiary" />
      <select aria-label="Filter by tag" value="" disabled={disabled} onChange={(e) => onChange(e.target.value || null)} className="bg-transparent text-xs font-semibold outline-none cursor-pointer pr-1">
        <option value="">Tag</option>
        {tags.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
    </label>
  );
}
