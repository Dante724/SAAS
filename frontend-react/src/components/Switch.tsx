// The app's one on/off switch. Use it for every setting that is simply on or off, instead of a
// checkbox or a local copy. `label` is the accessible name (a visible label sits beside it).
export default function Switch({ on, onChange, label, disabled = false, size = "md" }: {
  on: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
  size?: "sm" | "md";
}) {
  const track = size === "sm" ? "h-5 w-9" : "h-6 w-11";
  const knob = size === "sm" ? "h-4 w-4" : "h-5 w-5";
  const onPos = size === "sm" ? "left-[18px]" : "left-[22px]";
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative ${track} shrink-0 rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-50 disabled:cursor-not-allowed ${on ? "bg-primary" : "bg-slate-300"}`}>
      <span className={`absolute top-0.5 ${knob} rounded-full bg-white shadow transition-all ${on ? onPos : "left-0.5"}`} />
    </button>
  );
}
