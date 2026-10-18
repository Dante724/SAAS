import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

// One page of a server-paged list (skip/limit + total). Lists used to request limit=1000 and
// page in the browser; the API now serves at most 200 rows per request, so the page, search
// and filters travel to the server instead. Changing `params` returns to page 1.
export function useServerPage<T, X = Record<string, unknown>>(path: string, params: Record<string, string | undefined | null> = {},
  opts: { pageSize?: number; enabled?: boolean } = {}) {
  const pageSize = opts.pageSize ?? 50;
  const enabled = opts.enabled ?? true;
  const key = JSON.stringify(params);
  const [page, setPage] = useState(1);
  const [prevKey, setPrevKey] = useState(key);
  if (prevKey !== key) { setPrevKey(key); setPage(1); }   // new filters → first page, same render
  const [rows, setRows] = useState<T[] | null>(null);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [extra, setExtra] = useState<X | null>(null);   // the rest of the last response (summaries, facets)
  const [tick, setTick] = useState(0);
  // Every row seen so far by id, so a selection kept across pages can still be acted on.
  const seen = useRef(new Map<string, T>());
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v) p.set(k, v);
    p.set("skip", String((page - 1) * pageSize)); p.set("limit", String(pageSize));
    api.get<{ items: T[]; total?: number } & X>(`${path}${path.includes("?") ? "&" : "?"}${p}`)
      .then((r) => {
        if (!live) return;
        const items = r.items || [];
        items.forEach((it: any) => it && it.id && seen.current.set(it.id, it));
        setRows(items); setTotal(r.total ?? items.length); setExtra(r); setErr(null);
      })
      .catch((e) => { if (live) setErr(String(e?.message || e)); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, key, page, pageSize, tick, enabled]);
  return { rows, total, page, setPage, pageSize, err, extra, reload: () => setTick((t) => t + 1), seen: seen.current };
}

// The value, settled for `ms` — keeps a search box from firing a request per keystroke.
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
