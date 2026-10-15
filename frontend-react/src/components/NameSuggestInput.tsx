import { useEffect, useId, useState } from "react";
import { api } from "../api/client";

// Brand / model text box with suggestions: what this shop already uses, then the built-in
// US/CA list (phones, tablets, computers, consoles…). Anything typed is fine — the server snaps
// it to the canonical spelling ("samsung" → "Samsung") and remembers new names for next time.
export default function NameSuggestInput({ field, brand, value, onChange, className, placeholder }: {
  field: "brand" | "model"; brand?: string; value: string; onChange: (v: string) => void;
  className?: string; placeholder?: string;
}) {
  const listId = useId();
  const [items, setItems] = useState<string[]>([]);
  useEffect(() => {
    if (field === "model" && !brand?.trim()) { setItems([]); return; }
    const t = setTimeout(() => {
      const p = new URLSearchParams({ field, q: value.trim() });
      if (brand) p.set("brand", brand.trim());
      api.get<{ items: string[] }>(`/assets/suggest?${p}`).then((r) => setItems(r.items || [])).catch(() => setItems([]));
    }, 200);
    return () => clearTimeout(t);
  }, [field, brand, value]);
  return (
    <>
      <input value={value} onChange={(e) => onChange(e.target.value)} list={listId} autoComplete="off"
        className={className} placeholder={placeholder} />
      <datalist id={listId}>{items.map((i) => <option key={i} value={i} />)}</datalist>
    </>
  );
}
