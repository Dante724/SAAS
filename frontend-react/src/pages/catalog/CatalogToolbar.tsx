// The one toolbar every Catalog tab renders: the "New …" button, Categories, then the tab's own
// third action on the left; the category filter and search pinned right. A control that doesn't
// apply on a tab (no categories on Plans, no import on Job kits) stays in place, greyed, so
// switching tabs never moves anything.
import { useEffect, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import Button from "../../components/Button";
import { Toolbar } from "../../components/PageLayout";
import { fieldClass } from "../../components/Field";
import { SearchBox } from "../invoices/billing";

export type CatOption = { id: string; name: string; depth: number };

export default function CatalogToolbar({ newLabel, onNew, canNew = true, onCategories, third, cats, catFilter = "", onCatFilter, q, onQ, searchLabel, placeholder }: {
  newLabel: string; onNew: () => void; canNew?: boolean;
  onCategories?: () => void;            // omitted: Categories shows greyed
  third: ReactNode;                     // the tab's own action (Import, From a template …)
  cats?: CatOption[];                   // omitted: the category filter shows greyed
  catFilter?: string; onCatFilter?: (id: string) => void;
  q: string; onQ: (v: string) => void; searchLabel: string; placeholder: string;
}) {
  const filterOn = !!cats && !!catFilter;
  return (
    <Toolbar
      actions={<>
        {/* A fixed minimum width so "New service" / "New plan" don't nudge the buttons after them. */}
        <Button variant="primary" className="min-w-[8.5rem]" icon={<M n="add" c="text-sm" />} onClick={onNew} disabled={!canNew}>{newLabel}</Button>
        <Button icon={<M n="inventory" c="text-sm" />} onClick={onCategories} disabled={!onCategories}>Categories</Button>
        {third}
      </>}
      filters={
        <select aria-label="Category" value={cats ? catFilter : ""} onChange={(e) => onCatFilter?.(e.target.value)} disabled={!cats}
          className={fieldClass(`pr-8 !w-auto max-w-[12rem] text-xs font-semibold cursor-pointer disabled:cursor-not-allowed ${filterOn ? "!bg-primary/10 !text-primary !border-primary/30" : ""}`)}>
          <option value="">Category: All</option>
          {(cats || []).map((c) => <option key={c.id} value={c.id}>{`${"  ".repeat(c.depth)}${c.name}`}</option>)}
        </select>}
      search={<SearchBox value={q} onChange={onQ} label={searchLabel} placeholder={placeholder} className="flex-1 sm:flex-none" />}
    />
  );
}

/* True on a phone-width screen. Catalog and stock lists show one card per item there (with the
   price on it) instead of a wide table that hides the money columns off-screen. */
export function usePhone(): boolean {
  const q = "(max-width: 639px)";
  const [phone, setPhone] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(q).matches);
  useEffect(() => {
    const mq = window.matchMedia?.(q);
    if (!mq) return;
    const on = () => setPhone(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return phone;
}
