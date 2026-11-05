// The one toolbar every Reports tab renders (owner decision D3). Actions (Run, Show, Export…) sit
// on the left. On the right, a tab's own filters come first and the controls every tab shares —
// Location, From, To — come last, in fixed-width slots, so they stay pinned to the right edge and
// never move between tabs. A shared control that doesn't apply on a tab is greyed, not removed.
// On a phone the shared controls lead the filter row (the tab's extras follow), and a tab with no
// action keeps the action row's height, so nothing moves there either.
import type { ReactNode } from "react";
import { Toolbar } from "../../components/PageLayout";
import DateField from "../../components/DateField";
import { fieldClass } from "../../components/Field";

export default function ReportsToolbar({ actions, extras, locs, locId = "", onLoc, from, to, onFrom, onTo }: {
  actions?: ReactNode;
  extras?: ReactNode;                       // this tab's own filters, placed before the shared ones
  locs: { id: string; name: string }[];
  locId?: string;
  onLoc?: (id: string) => void;             // omitted: Location shows greyed
  from: string; to: string;
  onFrom?: (v: string) => void;             // omitted (either): the dates show greyed
  onTo?: (v: string) => void;
}) {
  const locOff = !onLoc || locs.length < 2;
  const dateOff = !onFrom || !onTo;
  return (
    <Toolbar
      actions={<>{actions}<span aria-hidden="true" className="h-9 w-0" /></>}
      filters={<>
        {extras && <div className="flex flex-wrap items-center gap-2 max-sm:order-last">{extras}</div>}
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Location" value={locOff ? "" : locId} onChange={(e) => onLoc?.(e.target.value)} disabled={locOff}
            className={fieldClass("pr-8 w-44 shrink-0 disabled:cursor-not-allowed")}>
            <option value="">All locations</option>
            {locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          <span className="flex items-center gap-1.5 whitespace-nowrap text-xs">
            <DateField mode="date" label="From" value={from} max={to} onChange={(v) => onFrom?.(v)} disabled={dateOff} className="w-36" />
            <span className="w-3 text-center text-ink-tertiary">to</span>
            <DateField mode="date" label="To" value={to} min={from} onChange={(v) => onTo?.(v)} disabled={dateOff} className="w-36" />
          </span>
        </div>
      </>}
    />
  );
}
