import type { ReactNode, ThHTMLAttributes, TdHTMLAttributes } from "react";
import { money } from "../pages/orders/types";

// Shared table pieces. Header cells all look the same; number and money columns are right-aligned
// with tabular digits in both the header and the cells, so totals line up.
export const tableClass = "w-full text-left border-collapse text-xs";
export const theadRowClass = "bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-2xs font-semibold";

export function Th({ num = false, className = "", ...rest }: ThHTMLAttributes<HTMLTableCellElement> & { num?: boolean }) {
  return <th className={`py-3 px-4 ${num ? "text-right" : ""} ${className}`} {...rest} />;
}

export function Td({ num = false, className = "", ...rest }: TdHTMLAttributes<HTMLTableCellElement> & { num?: boolean }) {
  return <td className={`py-3 px-4 ${num ? "text-right tabular-nums whitespace-nowrap" : ""} ${className}`} {...rest} />;
}

// An amount in the shop's currency: tabular digits, minus sign before the symbol, whole units unless
// `cents` (money that is never a whole number, like a unit cost).
export function Money({ value, symbol, cents = false, className = "" }: { value?: number | null; symbol?: string; cents?: boolean; className?: string }): ReactNode {
  return <span className={`tabular-nums whitespace-nowrap ${className}`}>{money(value ?? 0, symbol, cents)}</span>;
}
