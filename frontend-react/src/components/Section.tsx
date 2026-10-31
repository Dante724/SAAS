import type React from "react";
import HexTile from "./HexTile";

// The one card used for a section inside a drawer or page: white, one border, one shadow, one
// header size. Actions sit on the left, under the title, main action first.
export const sectionCard = "rounded-2xl border border-slate-200/90 bg-white shadow-clay-sm";

export default function Section({ title, icon, meta, description, actions, children, flush, className = "", label }: {
  title: React.ReactNode;
  icon?: string;
  /** Short extra fact beside the title, e.g. a count. */
  meta?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children?: React.ReactNode;
  /** Children run edge to edge (tables, lists) under a divided header. */
  flush?: boolean;
  className?: string;
  /** Accessible name, when the section should be a named region. */
  label?: string;
}) {
  const head = (
    <>
      <div className="flex items-center gap-2 min-w-0">
        {icon && <HexTile icon={icon} size={26} />}
        <h4 className="text-sm font-semibold text-ink-primary">{title}</h4>
        {meta != null && meta !== false && <span className="text-xs text-ink-tertiary tabular-nums">{meta}</span>}
      </div>
      {description && <p className="mt-0.5 text-xs text-ink-tertiary">{description}</p>}
      {actions && <div className="mt-2.5 flex flex-wrap items-center gap-2">{actions}</div>}
    </>
  );
  return (
    <section aria-label={label}
      className={`${sectionCard} ${flush ? "overflow-hidden" : "p-4"} ${className}`}>
      {flush
        ? <div className={`px-4 py-3 ${children ? "border-b border-slate-100" : ""}`}>{head}</div>
        : <div className={children ? "mb-3" : ""}>{head}</div>}
      {children}
    </section>
  );
}
