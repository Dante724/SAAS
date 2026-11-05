import type { ReactNode } from "react";

// The body of every page, directly inside <Shell>. It owns the page's one scroll area and the one
// page margin (24px on desktop, 16px on phones via index.css), so the first line of every page
// starts at the same spot. Modes:
//   scroll: a normal page that scrolls (default)
//   split:  side-by-side panes that each scroll on their own (Chats, Cashboxes); no page margin
//   fill:   a page that fills the screen without scrolling (a board, a calendar)
// `narrow` keeps a reading-width column, left-aligned like every other page (never centred).
export default function PageLayout({ children, mode = "scroll", narrow = false, className = "" }: {
  children: ReactNode;
  mode?: "scroll" | "split" | "fill";
  narrow?: boolean;
  className?: string;
}) {
  if (mode === "split") return <div className={`flex-1 flex flex-col md:flex-row min-h-0 overflow-y-auto md:overflow-hidden ${className}`}>{children}</div>;
  const inner = `px-6 py-5 space-y-4 ${narrow ? "max-w-4xl" : ""} ${className}`;
  if (mode === "fill") return <div className={`flex-1 flex flex-col min-h-0 ${inner}`}>{children}</div>;
  return <div className="flex-1 overflow-y-auto"><div className={inner}>{children}</div></div>;
}

// The row of controls at the top of a page or tab: actions on the left (main action first), then a
// view switch, then filters and search pinned to the right. Every slot keeps its place: a control
// that doesn't apply on the current tab is rendered disabled, not removed, so nothing slides.
export function Toolbar({ actions, view, filters, search, className = "" }: {
  actions?: ReactNode;
  view?: ReactNode;
  filters?: ReactNode;
  search?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-wrap items-center gap-2 min-h-9 ${className}`}>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      {view && <div className="flex items-center">{view}</div>}
      {(filters || search) && <div className="ml-auto flex flex-wrap items-center gap-2 max-sm:w-full">{filters}{search}</div>}
    </div>
  );
}

// The heading at the top of a tab panel or section. Every tab of a page uses one, or none does,
// so the first line of each tab starts at the same height.
export function PanelHeader({ title, sub, actions, className = "" }: {
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`space-y-3 ${className}`}>
      <div>
        <h2 className="text-sm font-semibold text-ink-primary">{title}</h2>
        {sub && <p className="text-xs text-ink-tertiary mt-0.5">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
