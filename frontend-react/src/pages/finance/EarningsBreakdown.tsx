import { useState } from "react";
import { Link } from "react-router-dom";
import { M } from "../../components/Shell";
import { money } from "../orders/types";

/* Commissions behind a pay figure, three levels deep: the rule that paid → the work order (or
   task) it paid on → the line item, with each accrual and reversal underneath. Shared by
   My earnings and the payroll calculation's expanded rows, so both read the same way. */

export type BreakdownEntry = { id: string; date: string; amount: number; kind: "earned" | "reversed"; note?: string };
export type BreakdownItem = { line_id: string | null; name: string; qty: number | null; total: number; entries: BreakdownEntry[] };
export type BreakdownDoc = { doc_type: "order" | "estimate" | "task"; doc_id: string; number: string | null; title: string | null; total: number; items: BreakdownItem[] };
export type BreakdownRule = {
  rule_id: string | null; rule_name: string; event: string | null; calc_type: string | null;
  rate: number | null; percent_basis: string | null; active: boolean; total: number; documents: BreakdownDoc[];
};

const EVENT_LABEL: Record<string, string> = {
  labor_performed: "Labour done", product_sold: "Parts sold", order_closed: "Order closed",
  order_closed_success: "Order closed successfully", order_closed_fail: "Order closed unsuccessfully",
  order_created: "Order created", assignee_labor: "Labour on their orders", assignee_product: "Parts on their orders",
  task_closed: "Task done",
};

export function eventLabel(e?: string | null) {
  return (e && EVENT_LABEL[e]) || "Commission";
}

export function ruleSummary(r: BreakdownRule) {
  if (r.calc_type === "flat") return `${money(r.rate ?? 0)} each`;
  if (r.calc_type === "percent") return `${r.rate ?? 0}% of ${r.percent_basis === "margin" ? "margin" : "the total"}`;
  return "";
}

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const signed = (n: number) => `${n < 0 ? "−" : ""}${money(Math.abs(n))}`;

function Amount({ n, strong }: { n: number; strong?: boolean }) {
  return <span className={`tabular-nums whitespace-nowrap ${n < 0 ? "text-rose-600" : strong ? "text-ink-primary" : "text-ink-secondary"} ${strong ? "font-semibold" : ""}`}>{signed(n)}</span>;
}

function DocLink({ d }: { d: BreakdownDoc }) {
  if (d.doc_type === "task") return <span className="inline-flex items-center gap-1.5 min-w-0"><M n="checklist" c="text-sm text-ink-tertiary" /><span className="truncate">{d.title || "Task"}</span></span>;
  const label = d.number || (d.doc_type === "estimate" ? "Estimate" : "Work order");
  return (
    <Link to={`/orders?open=${d.doc_id}`} className="inline-flex items-center gap-1.5 min-w-0 text-primary hover:underline">
      <M n="description" c="text-sm" /><span className="truncate">{label}</span>
    </Link>
  );
}

export default function EarningsBreakdown({ rules, empty = "No commissions in this period." }: { rules: BreakdownRule[]; empty?: string }) {
  const [openRule, setOpenRule] = useState<Record<string, boolean>>({});
  const [openDoc, setOpenDoc] = useState<Record<string, boolean>>({});
  if (!rules.length) return <div className="px-4 py-6 text-xs text-ink-tertiary text-center">{empty}</div>;
  return (
    <ul className="divide-y divide-slate-100">
      {rules.map((r) => {
        const rk = r.rule_id || "other";
        const ro = !!openRule[rk];
        return (
          <li key={rk}>
            <button type="button" onClick={() => setOpenRule((o) => ({ ...o, [rk]: !ro }))} aria-expanded={ro}
              className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50">
              <M n={ro ? "expand_more" : "chevron_right"} c="text-base text-ink-tertiary flex-shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-ink-primary truncate">{r.rule_name}{!r.active && r.rule_id && <span className="ml-2 text-[11px] font-normal text-ink-tertiary">(rule removed)</span>}</span>
                <span className="block text-[11px] text-ink-tertiary truncate">{[eventLabel(r.event), ruleSummary(r), `${r.documents.length} ${r.documents.length === 1 ? "job" : "jobs"}`].filter(Boolean).join(" · ")}</span>
              </span>
              <Amount n={r.total} strong />
            </button>
            {ro && (
              <ul className="bg-slate-50/60 border-t border-slate-100 divide-y divide-slate-100">
                {r.documents.map((d) => {
                  const dk = `${rk}:${d.doc_type}:${d.doc_id}`;
                  const dop = !!openDoc[dk];
                  return (
                    <li key={dk}>
                      <div className="flex items-center gap-3 pl-10 pr-4 py-2.5 text-xs">
                        <button type="button" aria-label={dop ? "Hide line items" : "Show line items"} aria-expanded={dop} onClick={() => setOpenDoc((o) => ({ ...o, [dk]: !dop }))}
                          className="w-6 h-6 -ml-1 rounded-md grid place-items-center text-ink-tertiary hover:bg-white flex-shrink-0"><M n={dop ? "expand_more" : "chevron_right"} c="text-sm" /></button>
                        <span className="min-w-0 flex-1 flex items-center gap-2">
                          <DocLink d={d} />
                          <span className="text-ink-tertiary whitespace-nowrap">{d.items.length} {d.items.length === 1 ? "item" : "items"}</span>
                        </span>
                        <Amount n={d.total} />
                      </div>
                      {dop && (
                        <ul className="pb-2">
                          {d.items.map((it) => (
                            <li key={it.line_id || it.name} className="pl-[4.5rem] pr-4 py-1.5 text-xs">
                              <div className="flex items-center gap-3">
                                <span className="min-w-0 flex-1 truncate text-ink-primary">{it.name}{it.qty != null && it.qty !== 1 && <span className="text-ink-tertiary"> × {it.qty}</span>}</span>
                                <Amount n={it.total} />
                              </div>
                              {it.entries.length > 1 && (
                                <ul className="mt-1 space-y-0.5">
                                  {it.entries.map((e) => (
                                    <li key={e.id} className="flex items-center gap-3 text-[11px] text-ink-tertiary">
                                      <span className="w-14 flex-shrink-0">{day(e.date)}</span>
                                      <span className="flex-1 min-w-0 truncate">{e.kind === "reversed" ? "Taken back" : "Earned"}{e.note ? ` · ${e.note}` : ""}</span>
                                      <span className={`tabular-nums ${e.amount < 0 ? "text-rose-600" : ""}`}>{signed(e.amount)}</span>
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}
