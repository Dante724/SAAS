// Tekly Go's one choice chip: a 44px pill used for every pick-one / pick-many row (job type,
// priority, who's on it, quick problems, cash tendered…). Picking a chip never changes its width,
// so its neighbours never move:
//   - `tick`: a picked chip shows a check mark in front of its label. While unpicked it keeps 11px of
//     extra padding on each side, the width of the 16px tick plus its 6px gap.
//   - `icon`: a fixed 16px icon in front (with `tick`, the tick takes its place when picked).
// `on` makes it a toggle for screen readers (aria-pressed); leave it out for a chip that just adds
// something (quick problems), which is never "picked".
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Check } from "lucide-react";

export function chipClass(on = false, reserveTick = false, extra = "") {
  return [
    "min-h-11 rounded-full border text-go-body font-medium inline-flex items-center gap-1.5 transition-colors disabled:opacity-50",
    reserveTick && !on ? "px-[27px]" : "px-4",
    on ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary active:bg-slate-50",
    extra,
  ].filter(Boolean).join(" ");
}

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { on?: boolean; tick?: boolean; icon?: ReactNode };

export default function Chip({ on, tick = false, icon, className = "", type = "button", children, ...rest }: Props) {
  const lead = tick && on ? <Check size={16} aria-hidden /> : icon;
  return (
    <button type={type} aria-pressed={on} className={chipClass(!!on, tick && !icon, className)} {...rest}>
      {lead}{children}
    </button>
  );
}
