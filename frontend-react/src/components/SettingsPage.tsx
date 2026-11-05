import type { ReactNode } from "react";

// The frame of every settings section: one title in one place, one line under it, actions on the
// left under the title (main first), and one reading width, so switching sections never moves
// the title or changes the column width. Cards inside are <Section>; saving uses <SaveFooter>.
export default function SettingsPage({ title, sub, actions, children, wide = false }: {
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  wide?: boolean;          // tables that need the room (statuses, team); still left-aligned
}) {
  return (
    <div className={`${wide ? "max-w-5xl" : "max-w-3xl"} space-y-4`}>
      <div>
        <h2 className="text-base font-semibold text-ink-primary">{title}</h2>
        {sub && <p className="text-xs text-ink-tertiary mt-0.5">{sub}</p>}
        {actions && <div className="mt-3 flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  );
}
