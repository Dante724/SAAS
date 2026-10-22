import { useEffect, useState } from "react";
import { api } from "../api/client";

// Client picker search, shared by every "find a client" box: while the box is open it lists
// the newest clients before you type, then matches name / phone / email from the first letter.
export function useClientSearch<T = any>(q: string, active: boolean, limit = 8): T[] {
  const [items, setItems] = useState<T[]>([]);
  useEffect(() => {
    if (!active) { setItems([]); return; }
    const s = q.trim();
    const url = s ? `/clients?limit=${limit}&search=${encodeURIComponent(s)}` : `/clients?limit=${limit}&sort=recent`;
    let live = true;
    const h = setTimeout(() => api.get<{ items: T[] }>(url).then((r) => { if (live) setItems(r.items || []); }).catch(() => { if (live) setItems([]); }), s ? 200 : 0);
    return () => { live = false; clearTimeout(h); };
  }, [q, active, limit]);
  return items;
}
