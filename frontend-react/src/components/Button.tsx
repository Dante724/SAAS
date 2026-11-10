import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

// The app's one button. Every action uses it (or buttonClass() for a link or label that must look
// like one), so size, corners, colour, hover and disabled look the same on every screen.
// Sizes: sm 32px, md 36px (default), lg 44px. On touch screens every button also gets a 44px tap
// area (the `.hit44` rule in index.css) without growing visually.
export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "dangerSolid" | "success" | "link";
export type ButtonSize = "sm" | "md" | "lg";

const BASE = "hit44 inline-flex items-center justify-center gap-1.5 rounded-xl font-semibold whitespace-nowrap transition-colors select-none disabled:opacity-50 disabled:cursor-not-allowed";
const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-primary text-white hover:bg-primary-container disabled:hover:bg-primary",
  secondary: "bg-white border border-slate-200 text-ink-secondary hover:bg-slate-50 hover:text-ink-primary disabled:hover:bg-white",
  ghost: "text-ink-secondary hover:bg-slate-100 hover:text-ink-primary disabled:hover:bg-transparent",
  danger: "bg-white border border-rose-200 text-rose-700 hover:bg-rose-50 disabled:hover:bg-white",
  dangerSolid: "bg-rose-600 text-white hover:bg-rose-700 disabled:hover:bg-rose-600",
  // A running timer's Stop (green = the clock is on, as everywhere else for "on the clock").
  success: "bg-emerald-600 text-white hover:bg-emerald-700 disabled:hover:bg-emerald-600",
  link: "text-primary hover:underline",
};
const SIZE: Record<ButtonSize, string> = { sm: "h-8 px-3 text-xs", md: "h-9 px-3.5 text-xs", lg: "h-11 px-4 text-sm" };

export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "md", extra = "") {
  return [BASE, VARIANT[variant], variant === "link" ? "text-xs" : SIZE[size], extra].filter(Boolean).join(" ");
}

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize; icon?: ReactNode; busy?: boolean };

const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "secondary", size = "md", icon, busy = false, className = "", type = "button", disabled, children, ...rest }, ref) {
  return (
    <button ref={ref} type={type} disabled={disabled || busy} aria-busy={busy || undefined} className={buttonClass(variant, size, className)} {...rest}>
      {icon}{children}
    </button>
  );
});
export default Button;

// A square, icon-only button. `label` is required: it is the accessible name and the tooltip.
type IconProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & { label: string; icon: ReactNode; variant?: "ghost" | "secondary"; size?: "sm" | "md" };

export const IconButton = forwardRef<HTMLButtonElement, IconProps>(function IconButton(
  { label, icon, variant = "ghost", size = "md", className = "", type = "button", ...rest }, ref) {
  const box = size === "sm" ? "h-8 w-8" : "h-9 w-9";
  return (
    <button ref={ref} type={type} aria-label={label} title={label}
      className={[BASE, VARIANT[variant], box, "shrink-0 text-ink-tertiary", className].join(" ")} {...rest}>
      {icon}
    </button>
  );
});
