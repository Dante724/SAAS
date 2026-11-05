import { useEffect, useRef, type ReactNode } from "react";
import { IconButton } from "./Button";
import { X } from "lucide-react";

// The app's one side panel: one backdrop, one header (title, line under it, close), a body that
// scrolls, and a footer whose main button comes first, on the left. Three widths:
// sm 440 (a short form), md 560 (default), lg 840 (a record with tabs). Escape closes it.
const WIDTH = { sm: 440, md: 560, lg: 840 } as const;

export default function Drawer({ title, sub, label, onClose, children, footer, tabs, size = "md", z = 50 }: {
  title: ReactNode;
  sub?: ReactNode;
  label: string;          // accessible name, e.g. "Edit client"
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;     // main button first
  tabs?: ReactNode;       // optional <Tabs> row under the header, outside the scrolling body
  size?: keyof typeof WIDTH;
  z?: number;             // lets a drawer open on top of another
}) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Only the top-most panel or dialog closes, so Escape in a drawer opened from another drawer
    // (a client from a job) closes just that one.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const open = document.querySelectorAll('[aria-modal="true"]');
      if (open.length && open[open.length - 1] !== root.current) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div ref={root} className="fixed inset-0 flex justify-end" style={{ zIndex: z }} role="dialog" aria-modal="true" aria-label={label}>
      <div className="absolute inset-0 bg-ink-primary/40" aria-hidden="true" onClick={onClose} />
      <div className="relative max-w-full bg-white h-full shadow-clay-drawer flex flex-col" style={{ width: WIDTH[size] }}>
        <div className={`flex items-start justify-between gap-3 px-5 py-4 ${tabs ? "pb-1" : "border-b border-slate-200"}`}>
          <div className="min-w-0">
            <h2 className="text-title font-semibold text-ink-primary truncate">{title}</h2>
            {sub && <div className="text-xs text-ink-tertiary mt-0.5">{sub}</div>}
          </div>
          <IconButton label="Close" onClick={onClose} icon={<X size={18} aria-hidden />} />
        </div>
        {tabs && <div className="px-5 flex-shrink-0">{tabs}</div>}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">{children}</div>
        {footer && <div className="px-5 py-4 border-t border-slate-200 flex flex-wrap items-center gap-2">{footer}</div>}
      </div>
    </div>
  );
}
