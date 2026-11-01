// Small building blocks for Tekly Go: a light slate canvas, white inset grouped lists (radius 12,
// 0.5px hairlines, no shadow), ink-* text on Geist's five type steps (go-display … go-caption).
// Only sheets carry a shadow. Theme tokens (bg-canvas / bg-white = surface) keep dark mode working.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import type { StatusMeta } from "../lib/useGo";
import { clickable } from "../../lib/clickable";

/** A screen with an iOS-style large title that collapses into a compact bar when scrolled.
 *  `caption` sits above the title; `footer` is docked below the scroll area (it reserves its own
 *  height, so it never covers the last rows). */
export function Page({ title, caption, subtitle, right, back, footer, children }: {
  title: string; caption?: ReactNode; subtitle?: ReactNode; right?: ReactNode; back?: ReactNode; footer?: ReactNode; children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const on = () => setCompact(el.scrollTop > 36);
    el.addEventListener("scroll", on, { passive: true });
    return () => el.removeEventListener("scroll", on);
  }, []);
  return (
    <div className="h-full flex flex-col">
    <div ref={ref} className="flex-1 min-h-0 overflow-y-auto overscroll-contain" data-go-scroll>
      <div
        className={`sticky top-0 z-20 flex items-center gap-2 px-4 pt-[env(safe-area-inset-top)] bg-canvas border-b-[0.5px] transition-colors ${compact ? "border-slate-200" : "border-transparent"}`}
      >
        <div className="h-11 flex items-center gap-1 min-w-0 flex-1">
          {back}
          <span className={`text-go-body font-semibold text-ink-primary truncate transition-opacity duration-200 ${compact ? "opacity-100" : "opacity-0"}`}>{title}</span>
        </div>
        {right}
      </div>
      {/* the "+" button floats over lists: leave room so it never covers the last row */}
      <div className={`px-4 ${footer ? "pb-6" : "pb-24"}`}>
        {caption && <p className="text-go-caption font-medium text-ink-tertiary tabular-nums">{caption}</p>}
        <h1 className="text-go-display font-bold tracking-tight text-ink-primary">{title}</h1>
        {subtitle && <div className="mt-0.5 text-go-body text-ink-secondary">{subtitle}</div>}
        <div className="mt-4">{children}</div>
      </div>
    </div>
    {footer && <div className="shrink-0 border-t-[0.5px] border-slate-200 bg-white px-4 pt-3 pb-3">{footer}</div>}
    </div>
  );
}

/** A white inset surface: radius 12, a 0.5px hairline, no shadow. */
export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`bg-white rounded-xl border-[0.5px] border-slate-200 ${className}`}>{children}</div>;
}

export function Section({ title, count, id, children }: { title: string; count?: number; id?: string; children: ReactNode }) {
  return (
    <section className="mb-6 scroll-mt-14" id={id}>
      <h2 className="px-4 mb-1.5 text-go-sub font-medium text-ink-tertiary">
        {title}{count != null && <span className="ml-1.5 tabular-nums">{count}</span>}
      </h2>
      {children}
    </section>
  );
}

/** An inset grouped list: rows share one white surface, split by 0.5px hairlines. */
export function Group({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`bg-white rounded-xl border-[0.5px] border-slate-200 overflow-hidden divide-y-[0.5px] divide-slate-200 ${className}`}>{children}</div>;
}

/** A plain label/value row inside a Group. */
export function Row({ label, value, tone, onClick }: { label: ReactNode; value?: ReactNode; tone?: "ink" | "late" | "ok"; onClick?: () => void }) {
  const inner = (
    <>
      <span className="min-w-0 flex-1 truncate text-go-body text-ink-primary">{label}</span>
      {value != null && <span className={`shrink-0 text-go-body tabular-nums ${tone === "late" ? "text-rose-700" : tone === "ok" ? "text-emerald-700" : "text-ink-secondary"}`}>{value}</span>}
    </>
  );
  const cls = "w-full min-h-[2.75rem] flex items-center gap-3 px-4 py-2.5 text-left";
  return onClick ? <button type="button" onClick={onClick} className={`${cls} active:bg-slate-100`}>{inner}</button> : <div className={cls}>{inner}</div>;
}

/** A one-line, muted note in place of a big empty card. */
export function QuietRow({ children }: { children: ReactNode }) {
  return <p className="px-4 py-1 text-go-sub text-ink-tertiary">{children}</p>;
}

const FALLBACK = "#64748b";
/** Status shown as colour AND text — never colour alone. The tinted pill is for the Job header;
 *  lists use StatusDot. `pending` (not sent yet) shows a hollow dot rather than a pulse. */
export function StatusBadge({ meta, statusKey, pending }: { meta?: StatusMeta; statusKey: string; pending?: boolean }) {
  const color = meta?.color || FALLBACK;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border-[0.5px] px-2.5 py-1 text-go-sub font-semibold whitespace-nowrap"
      style={{ background: `${color}1a`, borderColor: `${color}59`, color }}
    >
      <Dot color={color} hollow={pending} />
      {meta?.name || humanKey(statusKey)}
    </span>
  );
}

/** A status as a small dot + name, in ink: for lists that mix statuses. */
export function StatusDot({ meta, statusKey }: { meta?: StatusMeta; statusKey: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-go-caption text-ink-secondary whitespace-nowrap">
      <Dot color={meta?.color || FALLBACK} />{meta?.name || humanKey(statusKey)}
    </span>
  );
}

const Dot = ({ color, hollow }: { color: string; hollow?: boolean }) => (
  <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={hollow ? { boxShadow: `inset 0 0 0 1px ${color}` } : { background: color }} />
);

export const humanKey = (k: string) => (k || "—").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function SkeletonRows({ n = 4 }: { n?: number }) {
  return (
    <div aria-hidden><Group>
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="h-16 px-4 flex flex-col justify-center gap-2">
          <div className="flex items-center justify-between gap-3">
            <div className="sk h-3.5 w-32" />
            <div className="sk h-3 w-12" />
          </div>
          <div className="sk h-3 w-2/3" />
        </div>
      ))}
    </Group></div>
  );
}

export function Empty({ icon, title, body, action }: { icon: ReactNode; title: string; body?: string; action?: ReactNode }) {
  return (
    <Card className="px-6 py-10 text-center">
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-slate-50 text-ink-tertiary">{icon}</div>
      <p className="text-go-body font-semibold text-ink-primary">{title}</p>
      {body && <p className="mt-1 text-go-body text-ink-secondary">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
    </Card>
  );
}

export function PrimaryButton({ children, onClick, disabled, type = "button", className = "" }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; type?: "button" | "submit"; className?: string;
}) {
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`min-h-[3.25rem] w-full rounded-xl bg-primary px-5 text-go-body font-semibold text-white active:bg-primary-container transition-colors disabled:opacity-50 inline-flex items-center justify-center gap-2 ${className}`}>
      {children}
    </button>
  );
}

export function GhostButton({ children, onClick, className = "" }: { children: ReactNode; onClick?: () => void; className?: string }) {
  return (
    <button type="button" onClick={onClick}
      className={`min-h-[2.75rem] rounded-xl border-[0.5px] border-slate-200 bg-white px-4 text-go-body font-semibold text-ink-primary active:bg-slate-50 inline-flex items-center justify-center gap-2 ${className}`}>
      {children}
    </button>
  );
}

/** Bottom sheet (a centred card on iPad). Slides up 260ms ease-out, no overshoot; reduced motion is honoured by index.css. */
export function Sheet({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; /** tablet: a wider dialog (signing, pickers) */ wide?: boolean }) {
  const [shown, setShown] = useState(open);
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    if (open) { setShown(true); requestAnimationFrame(() => requestAnimationFrame(() => setEntered(true))); }
    else { setEntered(false); const t = setTimeout(() => setShown(false), 260); return () => clearTimeout(t); }
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  if (!shown) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end md:items-center justify-center" role="dialog" aria-modal="true" aria-label={title}>
      <button aria-label="Close" onClick={onClose}
        className={`absolute inset-0 bg-[rgb(var(--scrim)/.45)] transition-opacity duration-200 ${entered ? "opacity-100" : "opacity-0"}`} />
      <div
        className={`relative w-full ${wide ? "md:max-w-2xl" : "md:max-w-md"} max-h-[85vh] flex flex-col bg-raised rounded-t-2xl md:rounded-2xl shadow-drawer border-[0.5px] border-slate-200 pb-[env(safe-area-inset-bottom)] md:pb-0 transition-all duration-[260ms] ease-[cubic-bezier(.32,.72,0,1)] ${entered ? "translate-y-0 opacity-100" : "translate-y-full md:translate-y-6 opacity-0"}`}
      >
        <div className="mx-auto mt-2 h-1.5 w-10 rounded-full bg-slate-200 md:hidden" aria-hidden />
        <div className="flex items-center justify-between px-5 pt-3 pb-2">
          <h2 className="text-go-body font-semibold text-ink-primary">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="-mr-2 h-11 w-11 inline-flex items-center justify-center rounded-full text-ink-tertiary active:bg-slate-100">
            <X size={20} />
          </button>
        </div>
        <div className="overflow-y-auto px-3 pb-4">{children}</div>
      </div>
    </div>
  );
}

export type ToastMsg = { title: string; body?: string; tone?: "ok" | "warn" | "error" };

export function Toast({ msg, onDone }: { msg: ToastMsg | null; onDone: () => void }) {
  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(onDone, msg.tone === "error" ? 6000 : 3200);
    return () => clearTimeout(t);
  }, [msg, onDone]);
  if (!msg) return null;
  const tone = msg.tone === "error" ? "border-rose-200 bg-rose-50 text-rose-800"
    : msg.tone === "warn" ? "border-amber-200 bg-amber-50 text-amber-800"
    : "border-emerald-200 bg-emerald-50 text-emerald-800";
  return (
    <div className="fixed inset-x-0 z-[60] flex justify-center px-4 pointer-events-none bottom-[calc(env(safe-area-inset-bottom)+10rem)]" role="status" aria-live="polite">
      <div className={`pointer-events-auto max-w-md w-full rounded-xl border-[0.5px] px-4 py-3 shadow-menu ${tone}`} {...clickable(onDone)}>
        <p className="font-semibold text-go-body">{msg.title}</p>
        {msg.body && <p className="mt-0.5 text-go-sub opacity-90">{msg.body}</p>}
      </div>
    </div>
  );
}
