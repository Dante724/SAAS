// Bottom tab bar: Today · Scan (raised centre button) · Clock · More.
// Sits above the home indicator (safe-area inset); every target is at least 44pt.
import { NavLink } from "react-router-dom";
import { CalendarCheck, Clock3, Ellipsis, ScanLine, type LucideIcon } from "lucide-react";

type Tab = { to: string; label: string; Icon: LucideIcon };
const LEFT: Tab = { to: "/today", label: "Today", Icon: CalendarCheck };
const RIGHT: Tab[] = [{ to: "/clock", label: "Clock", Icon: Clock3 }, { to: "/more", label: "More", Icon: Ellipsis }];

function Item({ to, label, Icon, badge }: Tab & { badge?: number }) {
  return (
    <NavLink to={to}
      className={({ isActive }) => `relative flex-1 min-h-[3rem] flex flex-col items-center justify-center gap-0.5 rounded-xl text-[0.6875rem] font-medium ${isActive ? "text-primary" : "text-ink-tertiary"}`}>
      {({ isActive }) => (
        <>
          <Icon size={24} strokeWidth={isActive ? 2.25 : 1.75} />
          <span>{label}</span>
          {!!badge && (
            <span className="absolute top-0.5 left-1/2 ml-2 min-w-[1.125rem] h-[1.125rem] px-1 rounded-full bg-rose-500 text-white text-[0.625rem] font-bold leading-[1.125rem] text-center tabular-nums">
              {badge > 9 ? "9+" : badge}
            </span>
          )}
        </>
      )}
    </NavLink>
  );
}

export default function TabBar({ moreBadge }: { moreBadge?: number }) {
  return (
    <nav aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200/70 bg-white/85 backdrop-blur-xl pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]">
      <div className="mx-auto flex max-w-xl items-end gap-1 px-3 pt-1.5 pb-1">
        <Item {...LEFT} />
        <div className="flex-1 flex justify-center">
          <NavLink to="/scan" aria-label="Scan"
            className={({ isActive }) => `-mt-7 flex h-16 w-16 items-center justify-center rounded-[1.375rem] text-white shadow-[0_10px_24px_-6px_rgba(0,89,186,.55),inset_0_1px_0_rgba(255,255,255,.35)] ring-4 ring-white active:scale-95 transition-transform ${isActive ? "bg-primary-container" : "bg-primary"}`}>
            <ScanLine size={30} strokeWidth={2} />
          </NavLink>
        </div>
        {RIGHT.map((t) => <Item key={t.to} {...t} badge={t.to === "/more" ? moreBadge : undefined} />)}
      </div>
    </nav>
  );
}
