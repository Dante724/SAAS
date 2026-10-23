// Bottom tab bar: Today · Tasks · Scan (centre button) · Clock · More.
// Solid white with a hairline on top; sits above the home indicator (safe-area inset), every
// target at least 44pt. It is 3.75rem tall plus the inset; GoApp ends content above it.
import { NavLink } from "react-router-dom";
import { CalendarCheck, Clock3, Ellipsis, ListTodo, ScanLine, type LucideIcon } from "lucide-react";

type Tab = { to: string; label: string; Icon: LucideIcon };
const LEFT: Tab[] = [{ to: "/today", label: "Today", Icon: CalendarCheck }, { to: "/tasks", label: "Tasks", Icon: ListTodo }];
const RIGHT: Tab[] = [{ to: "/clock", label: "Clock", Icon: Clock3 }, { to: "/more", label: "More", Icon: Ellipsis }];

function Item({ to, label, Icon, badge }: Tab & { badge?: number }) {
  return (
    <NavLink to={to}
      className={({ isActive }) => `relative flex-1 min-h-[3rem] flex flex-col items-center justify-center gap-0.5 rounded-xl text-go-caption font-medium ${isActive ? "text-primary" : "text-ink-tertiary"}`}>
      {({ isActive }) => (
        <>
          <Icon size={24} strokeWidth={isActive ? 2.25 : 1.75} />
          <span>{label}</span>
          {!!badge && (
            <span className="absolute top-0.5 left-1/2 ml-2 min-w-[1.125rem] h-[1.125rem] px-1 rounded-full bg-rose-600 text-white text-go-caption font-semibold leading-[1.125rem] text-center tabular-nums">
              {badge > 9 ? "9+" : badge}
            </span>
          )}
        </>
      )}
    </NavLink>
  );
}

export default function TabBar({ moreBadge, tasksBadge }: { moreBadge?: number; tasksBadge?: number }) {
  return (
    <nav aria-label="Main"
      className="fixed inset-x-0 bottom-0 z-40 border-t-[0.5px] border-slate-200 bg-white pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]">
      <div className="mx-auto flex h-[3.75rem] max-w-xl items-center gap-0.5 px-2">
        {LEFT.map((t) => <Item key={t.to} {...t} badge={t.to === "/tasks" ? tasksBadge : undefined} />)}
        <div className="flex-1 flex justify-center">
          <NavLink to="/scan" aria-label="Scan"
            className={({ isActive }) => `flex h-12 w-14 items-center justify-center rounded-xl text-white transition-colors active:bg-primary-container ${isActive ? "bg-primary-container" : "bg-primary"}`}>
            <ScanLine size={26} strokeWidth={2} />
          </NavLink>
        </div>
        {RIGHT.map((t) => <Item key={t.to} {...t} badge={t.to === "/more" ? moreBadge : undefined} />)}
      </div>
    </nav>
  );
}
