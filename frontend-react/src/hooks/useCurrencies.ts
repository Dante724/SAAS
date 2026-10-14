import { useEffect, useState } from "react";
import { api } from "../api/client";

export type CurBase = { code: string; symbol: string; position?: string };
export type CurItem = { code: string; symbol: string; position?: string; rate: number };

/** Shared multi-currency options, sourced from /api/finance/currencies (real Currency
 *  registry). `multi` is true only when the org has added currencies beyond base —
 *  callers use it to hide currency pickers entirely for single-currency shops. */
export function useCurrencies() {
  const [base, setBase] = useState<CurBase | null>(null);
  const [items, setItems] = useState<CurItem[]>([]);
  useEffect(() => {
    api.get<{ base: CurBase; items: CurItem[] }>("/finance/currencies")
      .then((r) => { setBase(r.base); setItems(r.items || []); })
      .catch(() => {});
  }, []);
  const opts: [string, string][] = base
    ? [[base.code, `${base.code} — ${base.symbol} (base)`],
       ...items.map((c) => [c.code, `${c.code} — ${c.symbol}`] as [string, string])]
    : [];
  const byCode = (code?: string | null): CurItem | CurBase | null =>
    !code ? null : (base && code === base.code ? base : items.find((c) => c.code === code) || null);
  return { base, items, opts, byCode, multi: items.length > 0 };
}
