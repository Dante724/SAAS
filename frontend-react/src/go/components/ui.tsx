// Small building blocks for Tekly Go, in the web app's "Liquid Clay Studio" look: theme-token
// surfaces (bg-canvas / bg-white = card), clay shadows, ink-* text, 16px card radius, Geist.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import type { StatusMeta } from "../lib/useGo";

/** A screen with an iOS-style large title that collapses into a compact bar when scrolled. */
export function Page({ title, subtitle, right, back, children }: {
  title: string; subtitle?: ReactNode; right?: ReactNode; back?: ReactNode; children: ReactNode;
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
    <div ref={ref} className="h-full overflow-y-auto overscroll-contain" data-go-scroll>
      <div
        className={`sticky top-0 z-20 flex items-center gap-2 px-4 pt-[env(safe-area-inset-top)] transition-colors ${compact ? "bg-canvas/85 backdrop-blur-xl border-b border-slate-200/70" : "bg-canvas border-b border-transparent"}`}
      >
        <div className="h-11 flex items-center gap-1 min-w-0 flex-1">
          {back}
          <span className={`text-[17px] font-semibold text-ink-primary truncate transition-opacity duration-200 ${compact ? "opacity-100" : "opacity-0"}`}>{title}</span>
        </div>
        {right}
      </div>
      <div className="px-4 pb-[calc(env(safe-area-inset-bottom)+6.5rem)] md:pb-[calc(env(safe-area-inset-bottom)+5.5rem)]">
        <h1 className="text-[2rem] leading-tight font-bold tracking-tight text-ink-primary">{title}</h1>
        {subtitle && <div className="mt-0.5 text-[0.9375rem] text-ink-secondary">{subtitle}</div>}
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`bg-white rounded-2xl border border-slate-100 shadow-clay-card ${className}`}>{children}</div>;
}

export function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="mb-6">
      <h2 className="px-1 mb-2 text-[0.8125rem] font-semibold text-ink-tertiary">
        {title}{count != null && <span className="ml-1.5 tabular-nums">{count}</span>}
      </h2>
      {children}
    </section>
  );
}

const FALLBACK = "#64748b";
/** Status shown as colour AND text — never colour alone. */
export function StatusBadge({ meta, statusKey, pending }: { meta?: StatusMeta; statusKey: string; pending?: boolean }) {
  const color = meta?.color || FALLBACK;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.8125rem] font-semibold leading-none whitespace-nowrap"
      style={{ background: `${color}1f`, borderColor: `${color}59`, color }}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${pending ? "animate-pulse" : ""}`} style={{ background: color }} />
      {meta?.name || humanKey(statusKey)}
    </span>
  );
}

export const humanKey = (k: string) => (k || "—").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function SkeletonRows({ n = 4 }: { n?: number }) {
  return (
    <div className="space-y-2.5" aria-hidden>
      {Array.from({ length: n }).map((_, i) => (
        <Card key={i} className="p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="sk h-4 w-24" />
            <div className="sk h-5 w-20 rounded-full" />
          </div>
          <div className="sk mt-3 h-3.5 w-2/3" />
          <div className="sk mt-2 h-3 w-1/2" />
        </Card>
      ))}
    </div>
  );
}

export function Empty({ icon, title, body, action }: { icon: ReactNode; title: string; body?: string; action?: ReactNode }) {
  return (
    <Card className="px-6 py-10 text-center">
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-50 text-ink-tertiary">{icon}</div>
      <p className="font-semibold text-ink-primary">{title}</p>
      {body && <p className="mt-1 text-[0.9375rem] text-ink-secondary">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
    </Card>
  );
}

export function PrimaryButton({ children, onClick, disabled, type = "button", className = "" }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; type?: "button" | "submit"; className?: string;
}) {
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`min-h-[3.25rem] w-full rounded-2xl bg-primary px-5 text-[1.0625rem] font-semibold text-white shadow-clay-sm active:scale-[.98] transition-transform disabled:opacity-50 inline-flex items-center justify-center gap-2 ${className}`}>
      {children}
    </button>
  );
}

export function GhostButton({ children, onClick, className = "" }: { children: ReactNode; onClick?: () => void; className?: string }) {
  return (
    <button type="button" onClick={onClick}
      className={`min-h-[2.75rem] rounded-2xl border border-slate-200 bg-white px-4 text-[0.9375rem] font-semibold text-ink-primary active:bg-slate-50 inline-flex items-center justify-center gap-2 ${className}`}>
      {children}
    </button>
  );
}

/** Bottom sheet (a centred card on iPad). Slides up 220ms ease-out; reduced motion is honoured by index.css. */
export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const [shown, setShown] = useState(open);
  const [entered, setEntered] = useState(false);
  useEffect(() => {
    if (open) { setShown(true); requestAnimationFrame(() => requestAnimationFrame(() => setEntered(true))); }
    else { setEntered(false); const t = setTimeout(() => setShown(false), 220); return () => clearTimeout(t); }
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
        className={`relative w-full md:max-w-md max-h-[85vh] flex flex-col bg-raised rounded-t-[1.75rem] md:rounded-[1.75rem] shadow-clay-drawer border border-slate-100 pb-[env(safe-area-inset-bottom)] md:pb-0 transition-all duration-[220ms] ease-[cubic-bezier(.2,.9,.3,1.15)] ${entered ? "translate-y-0 opacity-100" : "translate-y-full md:translate-y-6 opacity-0"}`}
      >
        <div className="mx-auto mt-2 h-1.5 w-10 rounded-full bg-slate-200 md:hidden" aria-hidden />
        <div className="flex items-center justify-between px-5 pt-3 pb-2">
          <h2 className="text-[1.0625rem] font-semibold text-ink-primary">{title}</h2>
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
      <div className={`pointer-events-auto max-w-md w-full rounded-2xl border px-4 py-3 shadow-menu ${tone}`} onClick={onDone}>
        <p className="font-semibold text-[0.9375rem]">{msg.title}</p>
        {msg.body && <p className="mt-0.5 text-[0.875rem] opacity-90">{msg.body}</p>}
      </div>
    </div>
  );
}
