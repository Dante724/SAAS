import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { alertDialog, confirmDialog } from "../../lib/dialog";
import { loadFailed } from "../../lib/notice";
import { clickable } from "../../lib/clickable";

/* Settings › Checklists — template list (with starters when empty) and the template editor.
   Same API as before (/checklist-templates). The editor works on steps → checks; a template's
   sections are folded into its step, and every per-check setting the editor doesn't show
   (upsell rules, descriptions) is carried through untouched on save. */

export const KINDS: { k: string; label: string; icon: string }[] = [
  { k: "check_result", label: "Pass / Fail", icon: "check_circle" },
  { k: "yes_no", label: "Yes / No", icon: "done_all" },
  { k: "text", label: "Short answer", icon: "text_fields" },
  { k: "textarea", label: "Long answer", icon: "notes" },
  { k: "dropdown", label: "Pick one", icon: "expand_more" },
  { k: "multiselect", label: "Pick several", icon: "checklist" },
  { k: "date", label: "Date", icon: "calendar_today" },
  { k: "file", label: "Photo or file", icon: "photo_camera" },
  { k: "catalog_pick", label: "Part or service", icon: "inventory_2" },
];
const kindOf = (k: string) => KINDS.find((x) => x.k === k) || KINDS[0];
const hasOptions = (k: string) => k === "dropdown" || k === "multiselect";

type Item = { label: string; field_kind: string; required: boolean; client_visible: boolean; affects_result: boolean; options: string; raw?: any };
type Step = { title: string; description?: string | null; allow_notes?: boolean; items: Item[] };

const blank = (): Item => ({ label: "", field_kind: "check_result", required: true, client_visible: false, affects_result: true, options: "" });

export function toSteps(t: any): Step[] {
  return (t?.steps || []).map((s: any) => ({
    title: s.title || "", description: s.description ?? null,
    allow_notes: (s.sections || []).some((sec: any) => sec.allow_notes),
    items: (s.sections || []).flatMap((sec: any) => sec.items || []).map((i: any) => ({
      label: i.label || "", field_kind: i.field_kind || "check_result", required: i.required !== false,
      client_visible: !!i.client_visible, affects_result: i.affects_result !== false,
      options: (i.options || []).join(", "), raw: i,
    })),
  }));
}

export type AutoKey = { record_type: "work_order" | "estimate"; key: string };

// autoKeys undefined = leave the template's auto-attach statuses as they are (the API keeps them).
export function toBody(name: string, scope: string, typeIds: string[], steps: Step[], autoKeys?: AutoKey[]) {
  return {
    name: name.trim(), scope, type_ids: scope === "selected" ? typeIds : [],
    ...(autoKeys !== undefined ? { auto_status_keys: autoKeys } : {}),
    steps: steps.map((st) => ({
      title: st.title.trim() || "Step", description: st.description ?? null,
      sections: [{ title: "", allow_notes: !!st.allow_notes, items: st.items.map((i) => ({
        description: i.raw?.description ?? null,
        upsell_ref_id: i.raw?.upsell_ref_id ?? null, upsell_kind: i.raw?.upsell_kind ?? null,
        upsell_price: i.raw?.upsell_price ?? null, upsell_threshold: i.raw?.upsell_threshold ?? null,
        upsell_comparator: i.raw?.upsell_comparator ?? null,
        label: i.label.trim(), field_kind: i.field_kind, required: i.required, client_visible: i.client_visible,
        affects_result: i.affects_result,
        options: hasOptions(i.field_kind) ? i.options.split(",").map((o) => o.trim()).filter(Boolean) : [],
      })) }],
    })),
  };
}

const STARTERS: { name: string; blurb: string; icon: string; steps: Step[] }[] = [
  { name: "Intake check", blurb: "Condition and photos when a job comes in", icon: "photo_camera", steps: [
    { title: "Condition", items: [
      { ...blank(), label: "Powers on" },
      { ...blank(), label: "Screen", field_kind: "dropdown", options: "Perfect, Scratched, Cracked", affects_result: false },
      { ...blank(), label: "Signs of water damage", field_kind: "yes_no", affects_result: false, client_visible: true },
    ] },
    { title: "Photos", items: [{ ...blank(), label: "Photos of the item", field_kind: "file", affects_result: false, client_visible: true }] },
  ] },
  { name: "Quality check before pickup", blurb: "Test everything before the customer collects", icon: "verified", steps: [
    { title: "Tests", items: [
      { ...blank(), label: "Repair works as expected", client_visible: true },
      { ...blank(), label: "Other functions still work", client_visible: true },
      { ...blank(), label: "Cleaned", field_kind: "yes_no", affects_result: false },
    ] },
  ] },
];

const counts = (t: any) => {
  const items = (t?.steps || []).flatMap((s: any) => (s.sections || []).flatMap((sec: any) => sec.items || []));
  return { steps: (t?.steps || []).length, checks: items.length, visible: items.filter((i: any) => i.client_visible).length, required: items.filter((i: any) => i.required !== false).length };
};

export function ChecklistList({ templates, onOpen, onNew, onChanged, onFlash }: {
  templates: any[] | null | undefined; onOpen: (t: any) => void; onNew: (starter?: any) => void; onChanged: () => void; onFlash: (m: string) => void;
}) {
  const [types, setTypes] = useState<any[]>([]);
  const [menu, setMenu] = useState<string | null>(null);
  useEffect(() => { api.get<any>("/forms/types").then((r) => setTypes(r.items || [])).catch(loadFailed("job types")); }, []);
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close); return () => window.removeEventListener("click", close);
  }, [menu]);

  const appliesTo = (t: any) => t?.scope === "selected"
    ? ((t.type_ids || []).map((id: string) => types.find((x) => x.id === id)?.name).filter(Boolean).join(", ") || `${(t.type_ids || []).length} job types`)
    : "All job types";

  async function duplicate(t: any) {
    setMenu(null);
    try {
      await api.post("/checklist-templates", toBody(`${t.name} (copy)`, t.scope || "all", t.type_ids || [], toSteps(t)));
      onFlash("Checklist duplicated"); onChanged();
    } catch (e: any) { onFlash(e?.message || "Couldn't duplicate"); }
  }
  async function remove(t: any) {
    setMenu(null);
    if (!(await confirmDialog(`Delete "${t.name}"? Checklists already on jobs stay as they are.`, { title: "Delete checklist", confirmLabel: "Delete", danger: true }))) return;
    try { await api.del(`/checklist-templates/${t.id}`); onFlash("Checklist deleted"); onChanged(); }
    catch (e: any) { onFlash(e?.message || "Couldn't delete"); }
  }

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center gap-2">
        <div>
          <h2 className="text-base font-semibold text-ink-primary">Checklists</h2>
          <p className="text-xs text-ink-tertiary">Steps your team works through on a job. Customers see the checks you choose on their tracking page.</p>
        </div>
        <button onClick={() => onNew()} className="ml-auto inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-primary hover:border-slate-300 hover:bg-slate-50 flex-shrink-0"><M n="add" c="text-sm" />New checklist</button>
      </div>

      {templates == null && <div className="bg-white rounded-2xl border border-slate-200/90 p-5 text-xs text-ink-tertiary">Loading checklists…</div>}

      {templates && templates.length === 0 && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-6">
          <div className="text-sm font-semibold text-ink-primary">Start your first checklist</div>
          <p className="text-xs text-ink-tertiary mt-0.5 mb-4">Pick a starter and change it to fit your shop, or build one from scratch.</p>
          <div className="grid sm:grid-cols-2 gap-3">
            {STARTERS.map((s) => (
              <button key={s.name} onClick={() => onNew({ name: s.name, scope: "all", steps: s.steps })}
                className="text-left rounded-xl border border-slate-200 p-4 hover:border-primary/40 hover:bg-slate-50 transition-colors">
                <span className="w-9 h-9 rounded-lg bg-primary/10 text-primary grid place-items-center mb-2.5"><M n={s.icon} c="text-lg" /></span>
                <div className="text-sm font-medium text-ink-primary">{s.name}</div>
                <div className="text-xs text-ink-tertiary">{s.blurb}</div>
                <div className="text-xs text-ink-secondary mt-2">{s.steps.reduce((n, st) => n + st.items.length, 0)} checks in {s.steps.length} {s.steps.length === 1 ? "step" : "steps"}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {templates && templates.length > 0 && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-2">
          {templates.map((t) => {
            const c = counts(t);
            return (
              <div key={t.id} {...clickable(() => onOpen(t))} className="group relative flex items-center gap-3 px-3 py-3 rounded-xl cursor-pointer hover:bg-slate-50 transition-colors">
                <span className="w-10 h-10 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="fact_check" c="text-lg" /></span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-ink-primary truncate">{t.name}</span>
                  <span className="block text-xs text-ink-tertiary truncate">{c.checks} {c.checks === 1 ? "check" : "checks"} in {c.steps} {c.steps === 1 ? "step" : "steps"} · {appliesTo(t)}</span>
                </span>
                <span className="ml-auto flex items-center gap-1.5 flex-shrink-0">
                  {(t.auto_status_keys || []).length > 0 && <span title="Attaches itself when a job reaches a chosen status" className="inline-flex items-center h-6 px-2 rounded-md bg-primary/10 text-[11px] font-medium text-primary">Adds itself</span>}
                  {c.visible > 0 && <span title={`${c.visible} shown to the customer`} className="hidden sm:inline-flex items-center gap-1 h-6 px-2 rounded-md bg-slate-100 text-[11px] text-ink-secondary"><M n="visibility" c="text-[13px]" />{c.visible}</span>}
                  {c.required > 0 && <span title={`${c.required} must be filled in`} className="hidden sm:inline-flex items-center gap-1 h-6 px-2 rounded-md bg-slate-100 text-[11px] text-ink-secondary"><M n="priority_high" c="text-[13px]" />{c.required}</span>}
                  <button aria-label={`More actions for ${t.name}`} onClick={(e) => { e.stopPropagation(); setMenu(menu === t.id ? null : t.id); }}
                    className="w-8 h-8 rounded-lg grid place-items-center text-ink-tertiary hover:bg-slate-100 hover:text-ink-primary sm:opacity-0 sm:group-hover:opacity-100 focus:opacity-100"><M n="more_horiz" c="text-base" /></button>
                </span>
                {menu === t.id && (
                  <div className="absolute right-2 top-12 z-20 w-40 bg-white rounded-xl border border-slate-200 shadow-clay-drawer py-1 text-xs" onClick={(e) => e.stopPropagation()}>
                    <button onClick={() => { setMenu(null); onOpen(t); }} className="w-full text-left px-3 py-2 hover:bg-slate-50 flex items-center gap-2"><M n="edit" c="text-sm" />Edit</button>
                    <button onClick={() => duplicate(t)} className="w-full text-left px-3 py-2 hover:bg-slate-50 flex items-center gap-2"><M n="content_copy" c="text-sm" />Duplicate</button>
                    <button onClick={() => remove(t)} className="w-full text-left px-3 py-2 hover:bg-rose-50 text-rose-600 flex items-center gap-2"><M n="delete" c="text-sm" />Delete</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function ChecklistEditor({ tpl, onClose, onSaved }: { tpl: any | null; onClose: () => void; onSaved: () => void }) {
  const isEdit = !!tpl?.id;
  const [name, setName] = useState(tpl?.name || "");
  const [scope, setScope] = useState<string>(tpl?.scope || "all");
  const [typeIds, setTypeIds] = useState<string[]>(tpl?.type_ids || []);
  const [steps, setSteps] = useState<Step[]>(() => {
    if (tpl?.steps?.length && tpl.steps[0]?.items) return tpl.steps.map((s: Step) => ({ ...s, items: s.items.map((i) => ({ ...i })) }));  // a starter
    return tpl?.steps?.length ? toSteps(tpl) : [{ title: "Step 1", items: [blank()] }];
  });
  const [types, setTypes] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);   // "si:ii" of the check showing its details
  const [autoKeys, setAutoKeys] = useState<AutoKey[]>(tpl?.auto_status_keys || []);
  const [statuses, setStatuses] = useState<{ work_order: any[]; estimate: any[] } | null>(null);
  useEffect(() => { api.get<any>("/forms/types").then((r) => setTypes(r.items || [])).catch(loadFailed("job types")); }, []);
  useEffect(() => {
    Promise.all((["work_order", "estimate"] as const).map((rt) => api.get<any>(`/statuses?record_type=${rt}`).then((r) => (r.items || r || []).filter((s: any) => s.active !== false)).catch(() => [])))
      .then(([wo, est]) => setStatuses({ work_order: wo, estimate: est }));
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const upStep = (si: number, patch: Partial<Step>) => setSteps((s) => s.map((st, i) => (i === si ? { ...st, ...patch } : st)));
  const upItem = (si: number, ii: number, patch: Partial<Item>) => setSteps((s) => s.map((st, i) => (i !== si ? st : { ...st, items: st.items.map((it, j) => (j === ii ? { ...it, ...patch } : it)) })));
  const addItem = (si: number) => { setSteps((s) => s.map((st, i) => (i === si ? { ...st, items: [...st.items, blank()] } : st))); setOpen(null); };
  const delItem = (si: number, ii: number) => setSteps((s) => s.map((st, i) => (i === si ? { ...st, items: st.items.filter((_, j) => j !== ii) } : st)));
  const moveItem = (si: number, ii: number, d: number) => setSteps((s) => s.map((st, i) => {
    if (i !== si) return st; const j = ii + d; if (j < 0 || j >= st.items.length) return st;
    const items = [...st.items]; [items[ii], items[j]] = [items[j], items[ii]]; return { ...st, items };
  }));
  const moveStep = (si: number, d: number) => setSteps((s) => { const j = si + d; if (j < 0 || j >= s.length) return s; const n = [...s]; [n[si], n[j]] = [n[j], n[si]]; return n; });

  const all = steps.flatMap((s) => s.items);
  const visible = all.filter((i) => i.client_visible).length;

  async function save() {
    if (!name.trim()) { alertDialog("Give the checklist a name"); return; }
    const unlabeled = steps.find((st) => st.items.some((i) => !i.label.trim()));
    if (unlabeled) { alertDialog(`Every check in "${unlabeled.title || "Step"}" needs a name, or remove the empty one`); return; }
    if (!all.length) { alertDialog("Add at least one check"); return; }
    setBusy(true);
    try {
      const body = toBody(name, scope, typeIds, steps, autoKeys);
      if (isEdit) await api.put(`/checklist-templates/${tpl.id}`, body); else await api.post("/checklist-templates", body);
      onSaved();
    } catch (e: any) { alertDialog(e?.message || "Couldn't save the checklist"); setBusy(false); }
  }

  const Toggle = ({ on, onClick, icon, label }: { on: boolean; onClick: () => void; icon: string; label: string }) => (
    <button type="button" onClick={onClick} aria-pressed={on}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border transition-colors ${on ? "bg-primary/10 text-primary border-primary/30" : "border-slate-200 text-ink-tertiary hover:text-ink-secondary hover:border-slate-300"}`}>
      <M n={icon} c="text-[13px]" />{label}
    </button>
  );

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/30" aria-hidden="true" onClick={onClose} />
      <div role="dialog" aria-label={isEdit ? `Edit ${name}` : "New checklist"} className="relative w-[600px] max-w-full bg-white h-full shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.28s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <div className="flex items-center gap-3 px-5 pt-5 pb-3">
          <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="fact_check" c="text-lg" /></span>
          <div className="flex-1 min-w-0">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Checklist name, like Intake check" autoFocus={!isEdit} aria-label="Checklist name"
              className="w-full text-lg font-semibold text-ink-primary bg-transparent outline-none placeholder:text-ink-tertiary placeholder:font-normal border-b border-transparent focus:border-slate-200 py-0.5" />
            <div className="text-xs text-ink-tertiary">{all.length} {all.length === 1 ? "check" : "checks"} in {steps.length} {steps.length === 1 ? "step" : "steps"}{visible ? ` · ${visible} shown to the customer` : ""}</div>
          </div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary flex-shrink-0"><M n="close" c="text-base" /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pb-6 space-y-5">
          <div>
            <div className="text-xs font-semibold text-ink-secondary mb-1.5">Use on</div>
            <div className="inline-flex rounded-xl bg-slate-100 p-1">
              {([["all", "All job types"], ["selected", "Some job types"]] as [string, string][]).map(([v, l]) => (
                <button key={v} type="button" onClick={() => setScope(v)} aria-pressed={scope === v}
                  className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${scope === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
              ))}
            </div>
            {scope === "selected" && (
              <div className="flex flex-wrap gap-1.5 mt-2.5">
                {types.length === 0 && <span className="text-xs text-ink-tertiary">No job types yet. Add them in Forms & fields.</span>}
                {types.map((t) => { const on = typeIds.includes(t.id); return (
                  <button key={t.id} type="button" onClick={() => setTypeIds((s) => (on ? s.filter((x) => x !== t.id) : [...s, t.id]))} aria-pressed={on}
                    className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-colors ${on ? "bg-white border-primary/40 text-ink-primary shadow-sm" : "border-dashed border-slate-300 text-ink-tertiary hover:text-ink-secondary"}`}>
                    <Dot color={t.color} />{t.name}{on && <M n="check" c="text-[13px] text-primary -mr-0.5" />}
                  </button>
                ); })}
              </div>
            )}
          </div>

          <AutoAttachPicker value={autoKeys} onChange={setAutoKeys} statuses={statuses} />

          {steps.map((st, si) => (
            <div key={si} className="rounded-2xl border border-slate-200 bg-white">
              <div className="group/step flex items-center gap-3 px-4 pt-3.5 pb-2">
                <span className="w-7 h-7 rounded-full bg-ink-primary text-white text-xs font-semibold grid place-items-center flex-shrink-0">{si + 1}</span>
                <input value={st.title} onChange={(e) => upStep(si, { title: e.target.value })} placeholder="Step name" aria-label={`Step ${si + 1} name`}
                  className="flex-1 min-w-0 text-sm font-semibold text-ink-primary bg-transparent outline-none border-b border-transparent focus:border-slate-200 py-0.5" />
                <span className="flex items-center gap-0.5 opacity-0 group-hover/step:opacity-100 focus-within:opacity-100 transition-opacity">
                  <button aria-label="Move step up" disabled={si === 0} onClick={() => moveStep(si, -1)} className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:bg-slate-100 disabled:opacity-20"><M n="expand_less" c="text-base" /></button>
                  <button aria-label="Move step down" disabled={si === steps.length - 1} onClick={() => moveStep(si, 1)} className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:bg-slate-100 disabled:opacity-20"><M n="expand_more" c="text-base" /></button>
                  {steps.length > 1 && <button aria-label="Delete step" onClick={() => setSteps((s) => s.filter((_, i) => i !== si))} className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:bg-rose-50 hover:text-rose-600"><M n="delete" c="text-base" /></button>}
                </span>
              </div>

              <div className="px-2 pb-2">
                {st.items.map((it, ii) => {
                  const key = `${si}:${ii}`;
                  const k = kindOf(it.field_kind);
                  const isOpen = open === key;
                  return (
                    <div key={ii} className={`group rounded-xl transition-colors ${isOpen ? "bg-slate-50 ring-1 ring-slate-200" : "hover:bg-slate-50"}`}>
                      <div className="flex items-center gap-2.5 px-2 py-1.5">
                        <span className="w-7 h-7 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0" title={k.label}><M n={k.icon} c="text-[15px]" /></span>
                        <input value={it.label} onChange={(e) => upItem(si, ii, { label: e.target.value })} placeholder="What to check, like Screen works" aria-label="Check name"
                          className="flex-1 min-w-0 text-sm text-ink-primary bg-transparent outline-none py-1" />
                        {it.required && <span className="text-rose-500 text-xs" title="Must be filled in">*</span>}
                        {it.client_visible && <span title="Shown to the customer"><M n="visibility" c="text-[14px] text-ink-tertiary" /></span>}
                        <span className="text-[11px] text-ink-tertiary whitespace-nowrap hidden sm:inline">{k.label}</span>
                        <span className="flex items-center opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                          <button aria-label="Move check up" disabled={ii === 0} onClick={() => moveItem(si, ii, -1)} className="w-6 h-6 grid place-items-center text-ink-tertiary hover:text-primary disabled:opacity-20"><M n="expand_less" c="text-sm" /></button>
                          <button aria-label="Move check down" disabled={ii === st.items.length - 1} onClick={() => moveItem(si, ii, 1)} className="w-6 h-6 grid place-items-center text-ink-tertiary hover:text-primary disabled:opacity-20"><M n="expand_more" c="text-sm" /></button>
                          <button aria-label="Remove check" onClick={() => delItem(si, ii)} className="w-6 h-6 grid place-items-center text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>
                        </span>
                        <button aria-label="Check settings" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : key)}
                          className={`w-7 h-7 rounded-lg grid place-items-center flex-shrink-0 ${isOpen ? "bg-white text-primary shadow-sm" : "text-ink-tertiary hover:bg-slate-100"}`}><M n="tune" c="text-[15px]" /></button>
                      </div>
                      {isOpen && (
                        <div className="px-3 pb-3 pt-1 space-y-3">
                          <div className="flex flex-wrap gap-1.5">
                            {KINDS.map((x) => (
                              <button key={x.k} type="button" onClick={() => upItem(si, ii, { field_kind: x.k })} aria-pressed={it.field_kind === x.k}
                                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] border transition-colors ${it.field_kind === x.k ? "bg-white border-primary/40 text-ink-primary shadow-sm font-semibold" : "border-slate-200 text-ink-secondary hover:border-slate-300"}`}>
                                <M n={x.icon} c="text-[13px]" />{x.label}
                              </button>
                            ))}
                          </div>
                          {hasOptions(it.field_kind) && (
                            <input value={it.options} onChange={(e) => upItem(si, ii, { options: e.target.value })} placeholder="Choices, separated by commas: Perfect, Scratched, Cracked" aria-label="Choices"
                              className="w-full bg-white border border-slate-200 rounded-lg text-xs px-3 py-2 text-ink-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />
                          )}
                          <div className="flex flex-wrap gap-1.5">
                            <Toggle on={it.required} onClick={() => upItem(si, ii, { required: !it.required })} icon="priority_high" label="Must be filled in" />
                            <Toggle on={it.client_visible} onClick={() => upItem(si, ii, { client_visible: !it.client_visible })} icon="visibility" label="Customer sees it" />
                            {it.field_kind === "check_result" && <Toggle on={it.affects_result} onClick={() => upItem(si, ii, { affects_result: !it.affects_result })} icon="flag" label="A fail fails the checklist" />}
                          </div>
                          {it.raw?.upsell_kind && <p className="text-xs text-ink-tertiary"><M n="sell" c="text-[12px] mr-1" />This check suggests an extra service when it fails. That setting is kept.</p>}
                        </div>
                      )}
                    </div>
                  );
                })}
                <button onClick={() => addItem(si)} className="mt-1 ml-2 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold text-primary hover:bg-primary/5"><M n="add" c="text-sm" />Add check</button>
              </div>
            </div>
          ))}

          <button onClick={() => setSteps((s) => [...s, { title: `Step ${s.length + 1}`, items: [blank()] }])}
            className="w-full py-3 rounded-2xl border border-dashed border-slate-300 text-xs font-semibold text-ink-secondary hover:bg-slate-50 hover:border-slate-400 inline-flex items-center justify-center gap-1.5"><M n="add" c="text-sm" />Add step</button>
        </div>

        <div className="px-5 py-4 border-t border-slate-100 flex items-center gap-2">
          <button onClick={onClose} className="ml-auto px-4 py-2 rounded-xl border border-slate-200 text-ink-secondary hover:bg-slate-50 font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-5 py-2 rounded-xl bg-ink-primary text-white font-semibold text-xs hover:opacity-90 disabled:opacity-60">{busy ? "Saving…" : isEdit ? "Save" : "Create checklist"}</button>
        </div>
      </div>
    </div>
  );
}

// A status or job-type colour dot; neutral when the record has no colour of its own.
const Dot = ({ color }: { color?: string | null }) =>
  color ? <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} /> : <span className="w-2 h-2 rounded-full flex-shrink-0 bg-slate-400" />;

/* "When a job reaches…" (attach automatically): pick statuses (work orders and estimates are
   separate pipelines, so each status carries its record type). Entering one attaches this
   checklist to the job once; it's never added twice to the same job. */
export function AutoAttachPicker({ value, onChange, statuses }: {
  value: AutoKey[]; onChange: (v: AutoKey[]) => void; statuses: { work_order: any[]; estimate: any[] } | null;
}) {
  const has = (rt: AutoKey["record_type"], key: string) => value.some((k) => k.record_type === rt && k.key === key);
  const flip = (rt: AutoKey["record_type"], key: string) =>
    onChange(has(rt, key) ? value.filter((k) => !(k.record_type === rt && k.key === key)) : [...value, { record_type: rt, key }]);
  const groups: [AutoKey["record_type"], string][] = [["work_order", "Work orders"], ["estimate", "Estimates"]];
  return (
    <div>
      <div className="text-xs font-semibold text-ink-secondary mb-0.5">When a job reaches…</div>
      <p className="text-xs text-ink-tertiary mb-2">{value.length ? "Added to the job once, the first time it reaches one of these statuses." : "Off. Pick statuses to add this checklist to jobs on its own, or keep attaching it by hand."}</p>
      {!statuses && <div className="text-xs text-ink-tertiary">Loading statuses…</div>}
      {statuses && groups.map(([rt, label]) => statuses[rt].length > 0 && (
        <div key={rt} className="mb-2" role="group" aria-label={`${label} statuses`}>
          <div className="text-xs text-ink-secondary mb-1">{label}</div>
          <div className="flex flex-wrap gap-1.5">
            {statuses[rt].map((s: any) => { const on = has(rt, s.key); return (
              <button key={s.key} type="button" onClick={() => flip(rt, s.key)} aria-pressed={on}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-colors ${on ? "bg-white border-primary/40 text-ink-primary shadow-sm" : "border-dashed border-slate-300 text-ink-tertiary hover:text-ink-secondary"}`}>
                <Dot color={s.color} />{s.name}{on && <M n="check" c="text-[13px] text-primary -mr-0.5" />}
              </button>
            ); })}
          </div>
        </div>
      ))}
    </div>
  );
}
