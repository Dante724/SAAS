import type { ReactNode } from "react";

// One card look (white, hairline border, flat shadow, 16px corners), one number tile and one badge.
export default function Card({ children, padded = true, className = "" }: { children: ReactNode; padded?: boolean; className?: string }) {
  return <div className={`bg-white rounded-2xl border border-slate-200/90 shadow-clay-card ${padded ? "p-5" : "overflow-hidden"} ${className}`}>{children}</div>;
}

// A labelled number: label on top, the value large and tabular, an optional line under it.
export function StatTile({ label, value, hint, tone = "neutral" }: { label: ReactNode; value: ReactNode; hint?: ReactNode; tone?: "neutral" | "good" | "bad" }) {
  const color = tone === "good" ? "text-emerald-700" : tone === "bad" ? "text-rose-700" : "text-ink-primary";
  return (
    <div className="min-w-0">
      <div className="text-xs text-ink-tertiary">{label}</div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${color}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-ink-tertiary">{hint}</div>}
    </div>
  );
}

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger";
const TONE: Record<BadgeTone, [string, string]> = {
  neutral: ["bg-slate-100 text-ink-secondary", "bg-slate-400"],
  info: ["bg-blue-50 text-blue-700", "bg-blue-500"],
  success: ["bg-emerald-50 text-emerald-700", "bg-emerald-500"],
  warning: ["bg-amber-50 text-amber-700", "bg-amber-500"],
  danger: ["bg-rose-50 text-rose-700", "bg-rose-500"],
};

// A soft-tint pill (fill ~10%, darker text, optional dot) for a status or a count.
// Sizes: md 24px (default), sm 20px for pills inside table rows and dense lists.
export function Badge({ children, tone = "neutral", dot = false, size = "md", className = "" }: { children: ReactNode; tone?: BadgeTone; dot?: boolean; size?: "sm" | "md"; className?: string }) {
  const [fill, dotColor] = TONE[tone];
  return (
    <span className={`inline-flex items-center gap-1.5 ${size === "sm" ? "h-5 px-1.5" : "h-6 px-2"} rounded-full text-2xs font-semibold whitespace-nowrap ${fill} ${className}`}>
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${dotColor}`} aria-hidden="true" />}{children}
    </span>
  );
}
