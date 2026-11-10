// The one list of fields (merge variables) in every view: search on top, "Most used" open, the
// other groups folded. Click a field to put it in at the cursor or after the selected element;
// drag it onto the page to drop it exactly where you want.
import { useMemo, useState, type DragEvent } from "react";
import { ChevronDown, ChevronRight, Search } from "lucide-react";
import { chipLabel } from "./templateHtml";

export type VarGroup = { group: string; items: string[] };

export default function FieldsPanel({ groups, onPick, onDragField, title = "Fields" }: {
  groups: VarGroup[];
  onPick: (tok: string) => void;
  /** Drag start for a field; when left out, the field drags as text (Text and Code views). */
  onDragField?: (tok: string, ev: DragEvent<HTMLButtonElement>) => void;
  title?: string;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const ql = q.trim().toLowerCase();
  const shown = useMemo(() => groups.map((g) => ({ ...g, items: ql ? g.items.filter((t) => t.toLowerCase().includes(ql)) : g.items })).filter((g) => g.items.length), [groups, ql]);
  const isOpen = (g: string, i: number) => !!ql || (open[g] ?? i === 0);
  const dragText = (tok: string, ev: DragEvent<HTMLButtonElement>) => {
    ev.dataTransfer.setData("text/plain", tok);
    ev.dataTransfer.setData("text/html", `<span data-tok="${tok.replace(/"/g, "&quot;")}"></span>`);
    ev.dataTransfer.effectAllowed = "copy";
  };
  return (
    <section aria-label={title} className="flex flex-col min-h-0">
      <div className="px-2.5 pt-2.5 pb-1.5">
        <div className="text-2xs font-semibold text-ink-secondary mb-1.5">{title}</div>
        <label className="flex items-center gap-1.5 px-2 h-8 rounded-lg border border-slate-200 bg-white focus-within:border-primary">
          <Search size={14} className="text-ink-tertiary" aria-hidden />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search fields" aria-label="Search fields" className="flex-1 bg-transparent text-xs text-ink-primary outline-none" />
        </label>
        <p className="text-2xs text-ink-tertiary mt-1.5">Click to add at the cursor, or drag onto the page.</p>
      </div>
      <div className="px-2.5 pb-3 space-y-2">
        {shown.length === 0 && <div className="text-xs text-ink-tertiary py-2">No fields match.</div>}
        {shown.map((g, i) => (
          <div key={g.group}>
            <button type="button" aria-expanded={isOpen(g.group, i)} onClick={() => setOpen((o) => ({ ...o, [g.group]: !isOpen(g.group, i) }))}
              className="w-full flex items-center gap-1.5 py-1 text-2xs font-semibold text-ink-secondary hover:text-ink-primary">
              {isOpen(g.group, i) ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}{g.group}
              <span className="ml-auto font-normal text-ink-tertiary">{g.items.length}</span>
            </button>
            {isOpen(g.group, i) && (
              <div className="flex flex-wrap gap-1 pt-0.5">
                {g.items.map((tok) => (
                  <button key={tok} type="button" draggable title={`Add ${chipLabel(tok)}`}
                    onClick={() => onPick(tok)}
                    onDragStart={(e) => (onDragField ? onDragField(tok, e) : dragText(tok, e))}
                    className="inline-flex items-center h-6 px-2 rounded-lg text-2xs font-semibold bg-primary/10 text-primary border border-slate-200 hover:border-primary cursor-grab active:cursor-grabbing">{chipLabel(tok)}</button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
