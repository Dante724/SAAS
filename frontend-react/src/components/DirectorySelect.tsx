import { useEffect, useState } from "react";
import { api } from "../api/client";
import { promptDialog } from "../lib/dialog";
import { humanize } from "../lib/humanize";

// A dropdown fed by a Settings → Directories list (lead sources, cancellation reasons, …).
// Options can be added right here ("+ Add option…") — the counter can't stop to visit Settings —
// and "Manage options" opens the list for renaming, reordering and merging. A value that isn't in
// the list (typed before the list existed) stays selectable so editing a record never loses it.
type Item = { id: string; value: string };
const ADD = "__add__";
const MANAGE = "__manage__";

export function useDirectory(key: string) {
  const [items, setItems] = useState<Item[] | null>(null);
  const load = () => api.get<{ items: Item[] }>(`/directories/by-key/${key}/items`)
    .then((r) => setItems(r.items || [])).catch(() => setItems([]));
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [key]);
  async function add(): Promise<string | null> {
    const v = (await promptDialog("New option", { placeholder: "e.g. Nextdoor", confirmLabel: "Add" }))?.trim();
    if (!v) return null;
    const r = await api.post<{ value: string }>(`/directories/by-key/${key}/items`, { value: v });
    await load();
    return r.value || v;
  }
  return { items, add, reload: load };
}

export default function DirectorySelect({ dirKey, value, onChange, className = "", placeholder = "— Select —", manageLink = true }: {
  dirKey: string; value: string; onChange: (v: string) => void; className?: string; placeholder?: string; manageLink?: boolean;
}) {
  const { items, add } = useDirectory(dirKey);
  const values = (items || []).map((i) => i.value);
  const legacy = value && !values.includes(value) ? value : null;
  async function pick(v: string) {
    if (v === ADD) { const added = await add(); if (added) onChange(added); return; }
    if (v === MANAGE) { window.open(`/settings?section=directories&dir=${dirKey}`, "_blank", "noopener"); return; }
    onChange(v);
  }
  return (
    <select value={value || ""} onChange={(e) => pick(e.target.value)} className={className} disabled={items === null}>
      <option value="">{items === null ? "Loading…" : placeholder}</option>
      {legacy && <option value={legacy}>{/^[a-z0-9_]+$/.test(legacy) ? humanize(legacy) : legacy}</option>}
      {values.map((v) => <option key={v} value={v}>{v}</option>)}
      <option disabled>──────────</option>
      <option value={ADD}>+ Add option…</option>
      {manageLink && <option value={MANAGE}>Manage options ↗</option>}
    </select>
  );
}
