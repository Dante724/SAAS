import { M } from "./Shell";

// Building blocks for settings-style forms (see Settings › Memberships):
// a card of groups, each a list of rows — label + one line of help on the left, the control on the
// right — and a number field with its unit inside ("25 %", "90 days") instead of a box mid-sentence.

export function SettingRow({ label, help, htmlFor, children, dim }: {
  label: string; help?: string; htmlFor?: string; children: React.ReactNode; dim?: boolean;
}) {
  return (
    <div className={`flex items-center gap-6 py-4 first:pt-0 last:pb-0 ${dim ? "opacity-60" : ""}`}>
      <div className="min-w-0 flex-1">
        <label htmlFor={htmlFor} className="block text-sm font-medium text-ink-primary">{label}</label>
        {help && <p className="mt-0.5 text-xs text-ink-tertiary leading-relaxed">{help}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export function SettingGroup({ icon, title, children }: { icon?: string; title: string; children: React.ReactNode }) {
  return (
    <div className="px-6 py-5">
      <div className="mb-4 flex items-center gap-2">
        {icon && <M n={icon} c="text-base text-ink-tertiary" />}
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-tertiary">{title}</h3>
      </div>
      <div className="divide-y divide-slate-100">{children}</div>
    </div>
  );
}

export function NumField({ id, value, onChange, unit, disabled, max, min = 0, width = "w-28" }: {
  id?: string; value: number | null | undefined; onChange: (v: number) => void; unit: string;
  disabled?: boolean; max?: number; min?: number; width?: string;
}) {
  return (
    <div className={`glass-input h-9 ${width} rounded-xl flex items-center pr-3 focus-within:ring-2 focus-within:ring-primary/30 ${disabled ? "opacity-50" : ""}`}>
      <input id={id} inputMode="numeric" disabled={disabled} value={value ?? ""}
        onChange={(e) => {
          const n = Number(e.target.value.replace(/[^\d.]/g, "")) || 0;
          onChange(Math.max(min, max != null ? Math.min(n, max) : n));
        }}
        className="w-full min-w-0 bg-transparent pl-3 text-sm font-medium tabular-nums text-ink-primary outline-none disabled:cursor-not-allowed" />
      <span className="text-xs text-ink-tertiary shrink-0">{unit}</span>
    </div>
  );
}
