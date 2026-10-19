// The shop's own intake questions for a job type (Settings → Forms): passcode, accessories left,
// condition notes… whatever the shop set up. Loaded from GET /forms/layout?form=order&type_id=.
// Values go into the job's custom_data, exactly as the web form saves them.
import { Check } from "lucide-react";
import type { IntakeField } from "../lib/newJob";

type LayoutItem = { kind: string; key: string; label: string; visible: boolean; require?: string;
  field?: { field_type: string; options?: string[] } };

/** The custom fields worth asking at the counter, in the shop's order. */
export function intakeFieldsFrom(items: LayoutItem[]): IntakeField[] {
  return items
    .filter((i) => i.kind === "custom" && i.visible && i.field)
    .map((i) => ({ key: i.key, label: i.label, field_type: i.field!.field_type, options: i.field!.options || [], required: i.require === "intake" }));
}

const field = "w-full min-h-[3rem] rounded-2xl border border-slate-200 bg-white px-4 text-[1rem] text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary focus:ring-4 focus:ring-primary/15";
const chip = (on: boolean) => `min-h-[2.75rem] px-4 rounded-full border text-[0.9375rem] font-medium inline-flex items-center gap-1.5 ${on ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`;

export default function IntakeFields({ fields, values, onChange, missing }: {
  fields: IntakeField[]; values: Record<string, unknown>; onChange: (key: string, v: unknown) => void; missing?: Set<string>;
}) {
  if (!fields.length) return null;
  return (
    <div className="space-y-4">
      {fields.map((f) => {
        const v = values[f.key];
        const bad = missing?.has(f.label);
        const label = (
          <span className={`mb-1.5 block px-1 text-[0.8125rem] font-semibold ${bad ? "text-rose-700" : "text-ink-tertiary"}`}>
            {f.label}{f.required && f.field_type !== "bool" && <span className="text-rose-600"> *</span>}
          </span>
        );
        if (f.field_type === "bool") {
          return (
            <button key={f.key} onClick={() => onChange(f.key, !v)} aria-pressed={!!v} aria-label={f.label}
              className="w-full min-h-[3rem] flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-4 text-left">
              <span className={`h-6 w-6 shrink-0 rounded-md border-2 inline-flex items-center justify-center ${v ? "border-primary bg-primary text-white" : "border-slate-300"}`}>{!!v && <Check size={16} />}</span>
              <span className="text-[1rem] text-ink-primary">{f.label}</span>
            </button>
          );
        }
        if (f.field_type === "multiselect" || (f.field_type === "select" && f.options.length && f.options.length <= 6)) {
          const multi = f.field_type === "multiselect";
          const list = multi ? (Array.isArray(v) ? (v as string[]) : []) : [];
          return (
            <div key={f.key} role="group" aria-label={f.label}>
              {label}
              <div className="flex flex-wrap gap-2">
                {f.options.map((o) => {
                  const on = multi ? list.includes(o) : v === o;
                  return (
                    <button key={o} onClick={() => onChange(f.key, multi ? (on ? list.filter((x) => x !== o) : [...list, o]) : on ? "" : o)} aria-pressed={on} className={chip(on)}>
                      {on && <Check size={16} />}{o}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        }
        return (
          <label key={f.key} className="block">
            {label}
            {f.field_type === "select" ? (
              <select value={(v as string) || ""} onChange={(e) => onChange(f.key, e.target.value)} aria-label={f.label} className={field}>
                <option value="">Choose…</option>{f.options.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : f.field_type === "textarea" || f.field_type === "address" ? (
              <textarea value={(v as string) || ""} onChange={(e) => onChange(f.key, e.target.value)} aria-label={f.label} aria-invalid={bad || undefined} rows={2} className={`${field} py-3 min-h-[5rem] resize-none`} />
            ) : (
              <input value={(v as string) ?? ""} onChange={(e) => onChange(f.key, f.field_type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)}
                type={f.field_type === "number" ? "number" : f.field_type === "date" ? "date" : f.field_type === "datetime" ? "datetime-local" : f.field_type === "link" ? "url" : "text"}
                inputMode={f.field_type === "number" ? "decimal" : undefined}
                aria-label={f.label} aria-invalid={bad || undefined} autoComplete="off" className={field} />
            )}
          </label>
        );
      })}
    </div>
  );
}
