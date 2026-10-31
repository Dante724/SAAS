import { useEffect, useMemo, useRef, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { confirmDialog, promptDialog } from "../lib/dialog";

// "Customize this form": the form's fields as the staff will see them, in order. Drag a card (or use
// the arrows) to move it, click it to edit, "+ Add a field" to add one exactly where it belongs —
// from suggestions for the shop's industry, a hidden field, or a blank one. Built-in and custom
// fields live in one list; a "section" is a heading that groups the fields under it. Saves itself.
export type FormKey = "order" | "client" | "device" | "product" | "lead";
type Field = { id: string; field_label: string; field_type: string; required: boolean; placeholder?: string | null;
  options?: string[]; directory_key?: string | null; [k: string]: any };
// required: "" optional · "intake" when the record is created · "close" before a work order closes.
type Require = "" | "intake" | "close";
type Item = { ref: string; kind: "system" | "custom" | "header"; key: string; label: string; visible: boolean; required: boolean; require: Require;
  requirable?: boolean; field?: Field };
type Sugg = { label: string; field_type: string; options: string[] };

const TYPES: [string, string][] = [["text", "Short text"], ["textarea", "Long text"], ["number", "Number"], ["select", "Pick one"],
  ["multiselect", "Pick several"], ["bool", "Yes / no"], ["date", "Date"], ["link", "Link"]];
const TYPE_LABEL = Object.fromEntries(TYPES) as Record<string, string>;
const HINT: Record<string, string> = { text: "Type here", textarea: "Longer answer", number: "0", select: "Choose…",
  multiselect: "Choose one or more", bool: "Yes / No", date: "mm/dd/yyyy", link: "https://" };
const SYS_HINT: Record<string, string> = { client: "Search by name or phone", asset: "Brand · model · IMEI / serial",
  complaint: "What's the issue?", priority: "Normal", manager: "Select", assignee: "Unassigned", scheduled_for: "Date and time",
  phone: "(555) 555-0123", email: "name@email.com", address: "Billing / home address", source: "Choose…", loyalty_tier: "Standard",
  discount_card: "Member ID", notes: "Internal notes", asset_type: "Device", condition: "Good", brand: "Apple", model: "iPhone 15",
  identifiers: "Serial / IMEI / VIN", color: "Black", category: "Choose…", description: "Optional details", code: "Code", sku: "SKU",
  stock_policy: "Expiry · serial numbers · min stock", pricing: "Cost and store price", compatibility: "Brands and models it fits" };

export default function FormCustomizer({ form, typeId, title }: { form: FormKey; typeId?: string | null; title?: string }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [sugg, setSugg] = useState<Sugg[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [addAt, setAddAt] = useState<number | null>(null);
  const [state, setState] = useState<"" | "saving" | "saved" | "error">("");
  const [dirs, setDirs] = useState<{ key: string; name: string }[]>([]);
  const dragFrom = useRef<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const q = form === "order" ? `form=order&type_id=${typeId}` : `form=${form}`;

  const load = () => api.get<{ items: Item[]; suggestions: Sugg[] }>(`/forms/layout?${q}`)
    .then((r) => { setItems(r.items || []); setSugg(r.suggestions || []); }).catch(() => setItems([]));
  useEffect(() => { setItems(null); setSel(null); setAddAt(null); load(); /* eslint-disable-next-line */ }, [form, typeId]);
  useEffect(() => { api.get<any>("/directories").then((r) => setDirs(r.items || [])).catch(() => {}); }, []);

  async function persist(next: Item[]) {
    setItems(next); setState("saving");
    try {
      await api.put("/forms/layout", { form, type_id: typeId || null, items: next.map((i) => ({ ref: i.ref, visible: i.visible, required: i.require })) });
      setState("saved"); setTimeout(() => setState((s) => (s === "saved" ? "" : s)), 1800);
    } catch { setState("error"); }
  }
  const move = (from: number, to: number) => {
    if (!items || to < 0 || to >= items.length || from === to) return;
    const next = [...items]; const [it] = next.splice(from, 1); next.splice(to, 0, it); persist(next);
  };
  const patch = (ref: string, p: Partial<Item>) => items && persist(items.map((i) => (i.ref === ref ? { ...i, ...p } : i)));

  async function saveField(it: Item, p: Partial<Field>) {
    if (!it.field) return;
    const body = { ...it.field, ...p };
    setState("saving");
    try {
      const f = await api.put<Field>(`/forms/fields/${it.field.id}`, body);
      setItems((cur) => (cur || []).map((x) => (x.ref === it.ref ? { ...x, field: f, label: f.field_label } : x)));
      setState("saved"); setTimeout(() => setState((s) => (s === "saved" ? "" : s)), 1800);
    } catch (e: any) { setState("error"); }
  }

  async function createField(label: string, field_type: string, options: string[] = [], at = items?.length ?? 0) {
    const url = form === "order" ? `/forms/types/${typeId}/fields` : "/forms/entity-fields";
    const body: any = { field_label: label, field_type, options, required: false };
    if (form !== "order") body.entity_type = form;
    const f = await api.post<Field>(url, body);
    const it: Item = { ref: `cf:${f.id}`, kind: field_type === "header" ? "header" : "custom", key: f.field_key || "", label: f.field_label, visible: true, required: false, require: "", field: f };
    const next = [...(items || [])]; next.splice(at, 0, it);
    await persist(next); setSel(it.ref); setAddAt(null);
    setSugg((s) => s.filter((x) => x.label.toLowerCase() !== label.toLowerCase()));
  }
  async function blankField(at: number) {
    const label = (await promptDialog("Name the field", { placeholder: "e.g. Screen protector installed", confirmLabel: "Add" }))?.trim();
    if (label) await createField(label, "text", [], at);
  }
  async function addSection(at: number) {
    const label = (await promptDialog("Name the section", { placeholder: "e.g. Device check-in", confirmLabel: "Add section" }))?.trim();
    if (label) await createField(label, "header", [], at);
  }
  async function remove(it: Item) {
    if (!it.field) return;
    const what = it.kind === "header" ? "section heading" : "field";
    if (!(await confirmDialog(`Delete the “${it.label}” ${what}? Answers already saved on past records stay there.`, { danger: true, confirmLabel: "Delete" }))) return;
    await api.del(`/forms/fields/${it.field.id}`).catch(() => {});
    const next = (items || []).filter((x) => x.ref !== it.ref); setSel(null); persist(next);
  }

  const selected = useMemo(() => (items || []).find((i) => i.ref === sel) || null, [items, sel]);
  const hidden = (items || []).filter((i) => !i.visible);

  if (items === null) return <div className="p-6 text-sm text-ink-tertiary">Loading form…</div>;

  const addSlot = (at: number) => addAt === at ? (
    <div className="rounded-xl border border-dashed border-primary/60 bg-blue-50/40 p-3 space-y-2.5">
      <div className="flex items-center justify-between"><span className="text-xs font-bold text-primary">Add a field here</span>
        <button onClick={() => setAddAt(null)} className="text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-sm" /></button></div>
      {sugg.length > 0 && <div className="flex flex-wrap gap-1.5">
        {sugg.map((s) => <button key={s.label} onClick={() => createField(s.label, s.field_type, s.options, at)}
          className="px-2.5 py-1.5 rounded-lg bg-white border border-slate-200 text-xs font-semibold text-ink-secondary hover:border-primary hover:text-primary flex items-center gap-1"><M n="add" c="text-xs" />{s.label}</button>)}
      </div>}
      {hidden.length > 0 && <div className="flex flex-wrap gap-1.5 items-center"><span className="text-[11px] text-ink-tertiary">Hidden:</span>
        {hidden.map((h) => <button key={h.ref} onClick={() => { const next = (items || []).filter((x) => x.ref !== h.ref); next.splice(Math.min(at, next.length), 0, { ...h, visible: true }); persist(next); setAddAt(null); setSel(h.ref); }}
          className="px-2.5 py-1.5 rounded-lg bg-white border border-slate-200 text-xs font-semibold text-ink-tertiary hover:text-primary flex items-center gap-1"><M n="visibility" c="text-xs" />{h.label}</button>)}
      </div>}
      <div className="flex gap-2">
        <button onClick={() => blankField(at)} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold">Blank field…</button>
        <button onClick={() => addSection(at)} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-ink-secondary">Section…</button>
      </div>
    </div>
  ) : (
    <div className="relative h-2">
      <button onClick={() => { setAddAt(at); setSel(null); }} aria-label="Add a field here"
        className="group absolute inset-x-0 -top-1.5 h-5 z-10 flex items-center gap-2 text-[11px] font-semibold text-transparent hover:text-primary">
        <span className="flex-1 h-px bg-transparent group-hover:bg-primary/40" /><span className="flex items-center gap-1 px-2 rounded-full group-hover:bg-white"><M n="add" c="text-sm" />Add a field</span><span className="flex-1 h-px bg-transparent group-hover:bg-primary/40" />
      </button>
    </div>
  );

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] gap-5 items-start">
      <div className="bg-slate-50/80 rounded-2xl border border-slate-200/80 p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="text-sm font-bold text-ink-primary">{title || "Form"}</span>
          <span className="text-xs text-ink-tertiary">Drag to reorder · click to edit</span>
          <span className={`ml-auto text-xs font-semibold flex items-center gap-1 ${state === "error" ? "text-rose-600" : "text-emerald-700"}`}>
            {state === "saving" && "Saving…"}{state === "saved" && <><M n="check" c="text-sm" />Saved</>}{state === "error" && "Couldn't save — try again"}</span>
        </div>
        {(form === "client" || form === "product") && (
          <div className="rounded-xl border border-slate-200 bg-white/60 px-3 py-2.5 mb-2 flex items-center gap-2.5" title="Always first and always required">
            <M n="lock" c="text-ink-tertiary text-base" />
            <div className="flex-1 min-w-0"><div className="text-xs font-semibold text-ink-secondary mb-1">Name<span className="text-rose-500">*</span>
              <span className="ml-1.5 px-1.5 py-px rounded bg-slate-100 text-[11px] font-semibold text-ink-tertiary">Always first</span></div>
              <div className="h-8 rounded-lg bg-slate-50 border border-slate-200/80" /></div>
          </div>
        )}
        {items.filter((i) => i.visible).length === 0 && (
          <div className="text-sm text-ink-tertiary py-3 flex items-center gap-2">No fields yet.
            <button onClick={() => setAddAt(0)} className="font-semibold text-primary hover:underline">Add the first one</button></div>
        )}
        {items.filter((i) => i.visible).length === 0 && addAt === 0 && addSlot(0)}
        <div>
          {items.map((it, idx) => !it.visible ? null : (
            <div key={it.ref}>
              {idx === items.findIndex((x) => x.visible) && addSlot(idx)}
              <div draggable onDragStart={() => { dragFrom.current = idx; }} onDragEnd={() => { dragFrom.current = null; setDragOver(null); }}
                onDragOver={(e) => { e.preventDefault(); setDragOver(idx); }} onDragLeave={() => setDragOver((d) => (d === idx ? null : d))}
                onDrop={(e) => { e.preventDefault(); const f = dragFrom.current; setDragOver(null); if (f !== null) move(f, idx); }}
                onClick={() => { setSel(it.ref); setAddAt(null); }}
                className={`group rounded-xl border bg-white transition-colors cursor-pointer ${sel === it.ref ? "border-primary ring-2 ring-primary/15" : "border-slate-200 hover:border-slate-300"} ${dragOver === idx ? "border-t-4 border-t-primary" : ""}`}>
                {it.kind === "header" ? (
                  <div className="flex items-center gap-2 px-3 py-2">
                    <M n="drag_indicator" c="text-ink-tertiary cursor-grab text-base" />
                    <span className="text-[11px] font-bold text-ink-primary flex-1">{it.label}</span>
                    <span className="text-[11px] font-semibold text-ink-tertiary">Section</span>
                    <Arrows idx={idx} n={items.length} move={move} />
                  </div>
                ) : (
                  <div className="flex items-center gap-2.5 px-3 py-2.5">
                    <M n="drag_indicator" c="text-ink-tertiary cursor-grab text-base" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 text-xs font-semibold text-ink-secondary mb-1">
                        {it.label}{it.require === "intake" && <span className="text-rose-500">*</span>}
                        {it.require === "close" && <span className="px-1.5 py-px rounded bg-amber-50 text-[11px] font-semibold text-amber-700">Before closing</span>}
                        {it.kind === "system" && <span className="px-1.5 py-px rounded bg-slate-100 text-[11px] font-semibold text-ink-tertiary">Built-in</span>}
                        {it.field?.directory_key && <span className="px-1.5 py-px rounded bg-violet-50 text-[11px] font-semibold text-violet-700">List: {dirs.find((d) => d.key === it.field?.directory_key)?.name || it.field.directory_key}</span>}
                      </div>
                      <div className="h-8 rounded-lg bg-slate-50 border border-slate-200/80 flex items-center px-3 text-xs text-ink-tertiary truncate">
                        {it.kind === "system" ? SYS_HINT[it.key] || "" : (it.field?.placeholder || HINT[it.field?.field_type || "text"])}</div>
                    </div>
                    <Arrows idx={idx} n={items.length} move={move} />
                  </div>
                )}
              </div>
              {addSlot(idx + 1)}
            </div>
          ))}
        </div>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-4 lg:sticky lg:top-4 space-y-3.5 text-sm">
        {!selected ? (
          <div className="text-ink-tertiary text-sm space-y-2">
            <div className="font-bold text-ink-primary">Customize this form</div>
            <p>Drag a field to move it, or click it to rename it, make it required, change the answer type or hide it.</p>
            <p>Use <span className="font-semibold text-primary">Add a field</span> between fields to add one exactly where it belongs.</p>
          </div>
        ) : (
          <FieldPanel key={selected.ref} it={selected} dirs={dirs} form={form}
            onLabel={(v) => selected.field && saveField(selected, { field_label: v })}
            onField={(p) => saveField(selected, p)}
            onRequired={(v) => patch(selected.ref, { require: v, required: v === "intake" })}
            onHide={() => { patch(selected.ref, { visible: false, required: false, require: "" }); setSel(null); }}
            onDelete={() => remove(selected)} />
        )}
      </div>
    </div>
  );
}

function Arrows({ idx, n, move }: { idx: number; n: number; move: (a: number, b: number) => void }) {
  return (
    <div className="flex flex-col opacity-40 group-hover:opacity-100" onClick={(e) => e.stopPropagation()}>
      <button aria-label="Move up" disabled={idx === 0} onClick={() => move(idx, idx - 1)} className="text-ink-tertiary hover:text-primary disabled:opacity-20 leading-none"><M n="expand_less" c="text-base" /></button>
      <button aria-label="Move down" disabled={idx === n - 1} onClick={() => move(idx, idx + 1)} className="text-ink-tertiary hover:text-primary disabled:opacity-20 leading-none"><M n="expand_more" c="text-base" /></button>
    </div>
  );
}

function FieldPanel({ it, dirs, form, onLabel, onField, onRequired, onHide, onDelete }: {
  it: Item; dirs: { key: string; name: string }[]; form: FormKey;
  onLabel: (v: string) => void; onField: (p: Partial<Field>) => void; onRequired: (v: Require) => void; onHide: () => void; onDelete: () => void;
}) {
  const [label, setLabel] = useState(it.label);
  const [opts, setOpts] = useState((it.field?.options || []).join(", "));
  const [ph, setPh] = useState(it.field?.placeholder || "");
  const f = it.field;
  const lbl = "block text-[11px] font-bold   text-ink-tertiary mb-1.5";
  const chip = (on: boolean) => `px-3 py-1.5 rounded-lg text-xs font-semibold border ${on ? "bg-primary text-white border-primary" : "bg-white text-ink-secondary border-slate-200 hover:border-slate-300"}`;
  const isChoice = f && (f.field_type === "select" || f.field_type === "multiselect");
  // A yes/no always has an answer at intake, so "required" for it only means "ticked before closing".
  const isBool = f?.field_type === "bool";
  return (
    <>
      <div className="flex items-center gap-2">
        <span className="font-bold text-ink-primary flex-1 truncate">{it.label}</span>
        <span className="text-[11px] font-semibold text-ink-tertiary">{it.kind === "system" ? "Built-in field" : it.kind === "header" ? "Section" : TYPE_LABEL[f?.field_type || ""] || f?.field_type}</span>
      </div>
      {it.kind !== "system" && (
        <div><label className={lbl}>{it.kind === "header" ? "Section name" : "Label"}</label>
          <input value={label} onChange={(e) => setLabel(e.target.value)} onBlur={() => label.trim() && label !== it.label && onLabel(label.trim())}
            onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} className="glass-input w-full rounded-lg px-3 py-2 text-sm font-semibold" /></div>
      )}
      {it.kind !== "header" && it.requirable !== false && !(isBool && form !== "order") && (
        <div><label className={lbl}>Required</label>
          <div className="flex flex-wrap gap-1.5"><button onClick={() => onRequired("")} className={chip(!it.require)}>Optional</button>
            {!isBool && <button onClick={() => onRequired("intake")} className={chip(it.require === "intake")}>At intake</button>}
            {form === "order" && <button onClick={() => onRequired("close")} className={chip(it.require === "close")}>Before closing</button>}</div>
          <p className="text-[11px] text-ink-tertiary mt-1.5">{it.require === "intake" ? "Can't save the form without it."
            : it.require === "close" ? `Can be empty at check-in, but the work order can't be closed until it's ${isBool ? "ticked" : "filled"}.`
            : "Staff can leave it empty."}</p></div>
      )}
      {it.kind === "custom" && f && (<>
        <div><label className={lbl}>Answer type</label>
          <select value={f.field_type} onChange={(e) => onField({ field_type: e.target.value })} className="glass-input w-full rounded-lg px-3 py-2 text-sm">
            {TYPES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        {isChoice && (
          <div><label className={lbl}>Choices</label>
            <select value={f.directory_key || ""} onChange={(e) => onField({ directory_key: e.target.value || null })} className="glass-input w-full rounded-lg px-3 py-2 text-sm mb-2">
              <option value="">Type the choices here</option>
              {dirs.map((d) => <option key={d.key} value={d.key}>Use the “{d.name}” list</option>)}</select>
            {!f.directory_key && <input value={opts} onChange={(e) => setOpts(e.target.value)} onBlur={() => onField({ options: opts.split(",").map((o) => o.trim()).filter(Boolean) })}
              placeholder="Charger, Case, SIM card" className="glass-input w-full rounded-lg px-3 py-2 text-sm" />}
          </div>
        )}
        {form !== "product" && (
          <div><label className={lbl}>Shows on</label>
            <div className="flex flex-wrap gap-1.5">
              <button onClick={onHide} title="Hide this field from the form" className={chip(true)}>Form</button>
              {form === "order" && <button disabled={it.require === "close"} onClick={() => onField({ show_on_job_card: !f.show_on_job_card })}
                className={`${chip(f.show_on_job_card !== false || it.require === "close")} disabled:opacity-60`}>Job card</button>}
              <button onClick={() => onField({ show_on_print: !f.show_on_print })} className={chip(!!f.show_on_print)}>Printed receipt</button>
              <button onClick={() => onField({ show_in_portal: !f.show_in_portal })} className={chip(!!f.show_in_portal)}>Customer portal</button>
              {(form === "order" || form === "device") && f.field_type !== "header" && (
                <button onClick={() => onField({ ask_at_checkin: !f.ask_at_checkin })} className={chip(!!f.ask_at_checkin)}>Self check-in</button>)}
            </div>
            <p className="text-[11px] text-ink-tertiary mt-1.5">
              {form === "order" && it.require === "close" ? "Needed before closing, so it always shows on the job card. " : ""}
              Receipts list it wherever a template has the “Form details” block; the portal shows it on the customer's tracking page.
              {(form === "order" || form === "device") && " Self check-in asks the customer this on the check-in page, so only tick fields a customer should see."}</p></div>
        )}
        {f.field_type !== "bool" && (
          <div><label className={lbl}>Hint inside the box</label>
            <input value={ph} onChange={(e) => setPh(e.target.value)} onBlur={() => ph !== (f.placeholder || "") && onField({ placeholder: ph || null })}
              placeholder="If applicable" className="glass-input w-full rounded-lg px-3 py-2 text-sm" /></div>
        )}
      </>)}
      {it.kind === "system" && <p className="text-xs text-ink-tertiary">{it.requirable === false
        ? "Built-in — can be moved or hidden. It always has a value, so it can't be made required."
        : "Built-in fields can be moved, hidden or made required. Their answer type is fixed."}</p>}
      <div className="flex items-center justify-between pt-2 border-t border-slate-100">
        {it.kind !== "header" && <button onClick={onHide} className="text-xs font-semibold text-ink-secondary hover:text-ink-primary flex items-center gap-1"><M n="visibility_off" c="text-sm" />Hide from form</button>}
        {it.kind !== "system" && <button onClick={onDelete} className="text-xs font-semibold text-rose-600 hover:text-rose-700 flex items-center gap-1 ml-auto"><M n="delete" c="text-sm" />Delete</button>}
      </div>
    </>
  );
}
