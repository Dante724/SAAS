import { useEffect, useState } from "react";
import { api } from "../api/client";
import { promptDialog } from "../lib/dialog";

// Renders org-configured custom fields (Settings → Forms) into any form, reading/
// writing the record's custom_data. Backend resolve_custom_data enforces required +
// lock_after_create; this stays dumb on purpose.
// ponytail: no client-side required validation (server rejects), add if inline errors wanted.
type Field = { id: string; field_key: string; field_label: string; field_type: string; required: boolean; lock_after_create: boolean; placeholder?: string; options?: string[]; directory_key?: string | null; parent_field_key?: string | null; show_when_field_key?: string | null; show_when_op?: string | null; show_when_value?: string | null };

// 144gp — conditional visibility: a field with a show-rule renders only when the
// controlling field's current value matches. Mirrors the server's _field_visible.
function isVisible(f: Field, values: Record<string, any>): boolean {
  const key = f.show_when_field_key, op = f.show_when_op;
  if (!key || !op) return true;
  const cur = (values || {})[key];
  const empty = cur === undefined || cur === null || String(cur).trim() === "";
  if (op === "filled") return !empty;
  if (empty) return false;
  const want = f.show_when_value;
  if (op === "is") return String(cur) === String(want);
  if (op === "is_not") return String(cur) !== String(want);
  const n = parseFloat(cur), w = parseFloat(String(want));
  if (Number.isNaN(n) || Number.isNaN(w)) return false;
  return op === "gt" ? n > w : op === "lt" ? n < w : false;
}

export default function CustomFields({ entity, typeId, value, onChange, creating, autofillCtx }: {
  entity?: "product" | "client" | "device"; typeId?: string; value: Record<string, any>; onChange: (v: Record<string, any>) => void; creating: boolean;
  autofillCtx?: { device_id?: string; client_id?: string };
}) {
  const [fields, setFields] = useState<Field[]>([]);
  type DItem = { id: string; value: string; parent_id?: string | null };
  const [dirItems, setDirItems] = useState<Record<string, DItem[]>>({});
  useEffect(() => {
    const url = typeId ? `/forms/types/${typeId}/fields` : entity ? `/forms/entity-fields?entity=${entity}` : null;
    if (!url) { setFields([]); return; }
    api.get<{ items: Field[] }>(url).then((r) => setFields(r.items || [])).catch(() => setFields([]));
  }, [entity, typeId]);
  const fetchDir = (k: string) => api.get<{ items: DItem[] }>(`/directories/by-key/${k}/items`)
    .then((r) => setDirItems((s) => ({ ...s, [k]: r.items || [] }))).catch(() => {});
  // 144hi — pull items for any directory-bound select/multiselect
  useEffect(() => {
    Array.from(new Set(fields.map((f) => f.directory_key).filter(Boolean) as string[]))
      .forEach((k) => { if (!dirItems[k]) fetchDir(k); });
  }, [fields]);  // eslint-disable-line react-hooks/exhaustive-deps
  // 144hj — cascade: a field with parent_field_key shows only children of the parent's
  // selected value; a plain directory field shows root items (flat lists are all roots).
  const optsFor = (f: Field): string[] => {
    if (!f.directory_key) return f.options || [];
    const items = dirItems[f.directory_key] || [];
    if (f.parent_field_key) {
      const pv = (value || {})[f.parent_field_key];
      const parent = items.find((i) => i.value === pv);
      return parent ? items.filter((i) => i.parent_id === parent.id).map((i) => i.value) : [];
    }
    return items.filter((i) => !i.parent_id).map((i) => i.value);
  };
  // 144hi — let staff add a missing value to the bound directory without leaving the form
  const addToDir = async (f: Field) => {
    if (!f.directory_key) return;
    const v = await promptDialog(`Add to ${f.field_label}`); if (!v || !v.trim()) return;
    let parent_id: string | undefined;
    if (f.parent_field_key) parent_id = (dirItems[f.directory_key] || []).find((i) => i.value === (value || {})[f.parent_field_key!])?.id;
    await api.post(`/directories/by-key/${f.directory_key}/items`, { value: v.trim(), parent_id });
    await fetchDir(f.directory_key); set(f.field_key, v.trim());
  };
  // 144gq — on a fresh form, pre-fill empties from asset/last-order sources.
  useEffect(() => {
    if (!creating) return;
    const p = new URLSearchParams();
    if (typeId) p.set("type_id", typeId); else if (entity) p.set("entity", entity);
    if (autofillCtx?.device_id) p.set("device_id", autofillCtx.device_id);
    if (autofillCtx?.client_id) p.set("client_id", autofillCtx.client_id);
    api.get<{ values: Record<string, any> }>(`/forms/autofill?${p.toString()}`).then((r) => {
      const vals = r.values || {};
      if (!Object.keys(vals).length) return;
      const cur = value || {};
      const merged = { ...cur };
      let changed = false;
      for (const k of Object.keys(vals)) { if (cur[k] === undefined || cur[k] === "" || cur[k] === null) { merged[k] = vals[k]; changed = true; } }
      if (changed) onChange(merged);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entity, typeId, creating, autofillCtx?.device_id, autofillCtx?.client_id]);
  if (!fields.length) return null;

  const set = (k: string, v: any) => onChange({ ...(value || {}), [k]: v });
  const lbl = "block font-semibold text-ink-secondary mb-1";
  const field = "glass-input w-full px-3 py-2 rounded-lg text-ink-primary text-xs";

  return (
    <div className="p-4 rounded-xl bg-slate-50/90 border border-slate-200/80 space-y-3">
      <div className="text-[11px] font-bold text-ink-primary uppercase tracking-wider">Custom fields</div>
      {fields.filter((f) => isVisible(f, value)).map((f) => {
        const v = (value || {})[f.field_key] ?? "";
        const locked = f.lock_after_create && !creating;
        const label = <label className={lbl}>{f.field_label}{f.required && <span className="text-rose-500"> *</span>}{locked && <span className="ml-1 text-[10px] text-ink-tertiary">(locked)</span>}</label>;
        if (f.field_type === "header") return <div key={f.id} className="text-[11px] font-bold text-ink-primary uppercase tracking-wider pt-1">{f.field_label}</div>;
        if (f.field_type === "bool") return <label key={f.id} className="flex items-center justify-between cursor-pointer"><span className="text-ink-secondary font-medium">{f.field_label}</span><input type="checkbox" disabled={locked} checked={!!v} onChange={(e) => set(f.field_key, e.target.checked)} className="w-4 h-4 rounded text-primary border-slate-300" /></label>;
        return (
          <div key={f.id}>
            {label}
            {f.field_type === "textarea" ? <textarea disabled={locked} value={v} onChange={(e) => set(f.field_key, e.target.value)} placeholder={f.placeholder || ""} className={`${field} h-16 resize-none disabled:opacity-60`} />
              : f.field_type === "select" ? <select disabled={locked} value={v} onChange={(e) => set(f.field_key, e.target.value)} className={`${field} appearance-none disabled:opacity-60`}><option value="">—</option>{optsFor(f).map((o) => <option key={o} value={o}>{o}</option>)}</select>
              : f.field_type === "multiselect" ? <div className="flex flex-wrap gap-1.5">{optsFor(f).map((o) => { const arr: string[] = Array.isArray(v) ? v : []; const on = arr.includes(o); return <button type="button" key={o} disabled={locked} onClick={() => set(f.field_key, on ? arr.filter((x) => x !== o) : [...arr, o])} className={`px-2.5 py-1 rounded-full text-[11px] font-semibold border ${on ? "bg-primary text-white border-primary" : "bg-white text-ink-secondary border-slate-200"} disabled:opacity-60`}>{o}</button>; })}</div>
              : f.field_type === "link" ? <input disabled={locked} type="url" value={v} onChange={(e) => set(f.field_key, e.target.value)} placeholder={f.placeholder || "https://"} className={`${field} disabled:opacity-60`} />
              : <input disabled={locked} type={f.field_type === "number" ? "number" : f.field_type === "date" ? "date" : f.field_type === "datetime" ? "datetime-local" : "text"} value={v} onChange={(e) => set(f.field_key, e.target.value)} placeholder={f.placeholder || ""} className={`${field} disabled:opacity-60`} />}
            {f.directory_key && !locked && (f.field_type === "select" || f.field_type === "multiselect") &&
              <button type="button" onClick={() => addToDir(f)} className="mt-1 text-[10px] font-semibold text-primary hover:underline">+ Add {f.field_label.toLowerCase()}</button>}
          </div>
        );
      })}
    </div>
  );
}
