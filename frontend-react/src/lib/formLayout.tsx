import { useEffect, useState, type ReactElement, type ReactNode } from "react";
import { api } from "../api/client";
import { isVisible } from "../components/CustomFields";

// One ordered list per form (Settings → Forms → Customize): the form's built-in blocks and its
// custom fields, interleaved. Drawers pass their built-in blocks by key; this places them, drops
// hidden ones, and groups runs of custom fields so they render as one <CustomFields only=…>.
export type LayoutItem = { ref: string; kind: "system" | "custom" | "header"; key: string; label: string; visible: boolean;
  required: boolean; require?: "" | "intake" | "close"; field?: any };

export function useFormLayout(form: "client" | "device" | "product", enabled = true): LayoutItem[] | null {
  const [items, setItems] = useState<LayoutItem[] | null>(null);
  useEffect(() => {
    if (!enabled) return;
    api.get<{ items: LayoutItem[] }>(`/forms/layout?form=${form}`).then((r) => setItems(r.items || [])).catch(() => setItems(null));
  }, [form, enabled]);
  return items;
}

/** A built-in block: half-width blocks pair up two per row; the rest take the full row. */
export type Block = { el: ReactNode; half?: boolean };

export function LayoutFields({ layout, blocks, custom, gap = "gap-3" }: {
  layout: LayoutItem[] | null; blocks: Record<string, Block | null | undefined>;
  /** Render a run of custom fields (ids in order); null layout → called once with every field. */
  custom: (ids: string[] | undefined) => ReactNode; gap?: string;
}) {
  const out: ReactElement[] = [];
  const push = (key: string, b: Block) => out.push(<div key={key} className={b.half ? "col-span-2 sm:col-span-1" : "col-span-2"}>{b.el}</div>);
  if (!layout) {
    // Layout not loaded (or failed): today's order, every built-in, custom fields at the end.
    Object.entries(blocks).forEach(([k, b]) => b && push(k, b));
    out.push(<div key="cf" className="col-span-2">{custom(undefined)}</div>);
  } else {
    let run: string[] = [];
    const flush = () => { if (run.length) out.push(<div key={`cf-${run[0]}`} className="col-span-2">{custom(run)}</div>); run = []; };
    for (const it of layout) {
      if (!it.visible) continue;
      if (it.kind === "system") { flush(); const b = blocks[it.key]; if (b) push(it.ref, b); }
      else run.push(it.ref.slice(3));
    }
    flush();
  }
  return <div className={`grid grid-cols-2 ${gap}`}>{out}</div>;
}

/** Red star for a built-in the form requires. */
export function reqStar(layout: LayoutItem[] | null, key: string): ReactNode {
  return (layout || []).some((i) => i.kind === "system" && i.key === key && i.visible && i.required)
    ? <span className="text-rose-500"> *</span> : null;
}

const empty = (v: any) => v === undefined || v === null || (Array.isArray(v) ? !v.length : String(v).trim() === "");

/** "Fill in Phone, Company" when required fields are empty, else null. `filled` maps built-in keys;
 *  built-ins not in it (not editable on this screen) are skipped. */
export function missingRequired(layout: LayoutItem[] | null, filled: Record<string, boolean>, values: Record<string, any> | undefined): string | null {
  const v = values || {};
  const miss = (layout || []).filter((i) => i.visible && i.required && (i.kind === "system"
    ? i.key in filled && !filled[i.key]
    : i.kind === "custom" && i.field && i.field.field_type !== "bool" && isVisible(i.field, v) && empty(v[i.key])));
  return miss.length ? `Fill in ${miss.map((i) => i.label).join(", ")}` : null;
}

export const has = (v: any) => !empty(v);
