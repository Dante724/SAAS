import { M } from "../../components/Shell";
import { Input } from "../../components/Field";

// The search box for the front-desk toolbars (Toolbar `search` slot): the shared field look with a
// magnifier, 288px wide on desktop and full width on phones, so it sits in the same spot on every page.
export default function SearchBox({ value, onChange, label, placeholder, disabled, className = "sm:w-72" }: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div className={`relative w-full ${className}`}>
      <M n="search" c="absolute left-3 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" />
      <Input value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} placeholder={placeholder} disabled={disabled} className="pl-8" />
    </div>
  );
}
