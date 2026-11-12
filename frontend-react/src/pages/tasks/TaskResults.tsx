import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { M } from "../../components/Shell";
import Button from "../../components/Button";
import Pager from "../../components/Pager";
import EmptyState, { ErrorState } from "../../components/EmptyState";
import { Badge } from "../../components/Card";
import { Avatar } from "../orders/look";
import { api } from "../../api/client";
import { fmtShop, toShopWall } from "../../lib/shopTime";
import { type Task, applyFilters, normStatus, normPriority, PRIORITY_LABEL, isAuto, dueClock, fmtClock, AUTO_LABEL, AUTO_HINT } from "./taskModel";

// One flat list of tasks from GET /tasks: what a search finds across the whole shop (open, done
// and cancelled, so history is one search away), and the stand-in for My day on a server that
// doesn't have it yet. Open tasks come first (the server sorts), each row opens the task panel.

type ListReply = { items?: Task[]; total?: number };
const PAGE = 50;

export default function TaskResults({ q, whose, meId, tz, onOpen, onClear, flash, onChanged, onPaged, reloadKey, note }: {
  q: string;                         // the search ("" = every task for `whose`)
  whose: string;                     // used only without a search: "me", "all" or an employee id (the
                                     // list API can't ask for unassigned tasks: Up for grabs comes from My day)
  meId?: string;
  tz: string | null;
  onOpen: (t: Task) => void;
  onClear?: () => void;              // shown as "Clear search" while there is a search
  flash: (msg: string) => void;
  onChanged: () => void;             // a row was ticked: the day behind it changed too
  onPaged?: (paged: boolean) => void; // whether the server pages (the newer API, which also soft-deletes)
  reloadKey: number;
  note?: string;                     // a line above the list (why the list is showing)
}) {
  const [items, setItems] = useState<Task[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);   // only the paged (newer) API says
  const [failed, setFailed] = useState(false);
  const [page, setPage] = useState(1);
  const [closedTotal, setClosedTotal] = useState<number | null>(null);   // done + cancelled matches, from the server
  const req = useRef(0);
  // A search covers the whole shop (whatever the person menu says); the plain list follows it.
  const scope = q ? "all" : whose;
  useEffect(() => { setPage(1); }, [q, scope]);

  const load = () => {
    const id = ++req.current;
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (scope === "me") { p.set("mine", "true"); p.set("assignee", "me"); }
    else if (scope !== "all") p.set("assignee", scope);
    p.set("limit", String(PAGE));                     // the older API ignores these and sends everything
    p.set("offset", String((page - 1) * PAGE));
    setFailed(false);
    api.get<ListReply>(`/tasks?${p.toString()}`).then((r) => {
      if (id !== req.current) return;
      setItems(r.items || []);
      setTotal(typeof r.total === "number" ? r.total : null);
      onPaged?.(typeof r.total === "number");
    }).catch(() => { if (id === req.current) { setItems((cur) => cur ?? []); setFailed(true); } });
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [q, scope, page, reloadKey]);
  // "N from history" counts every match, not one page: ask the server for the closed ones' total
  // (one row). Without an answer the line simply doesn't say it.
  useEffect(() => {
    setClosedTotal(null);
    if (!q) return;
    let live = true;
    api.get<ListReply>(`/tasks?${new URLSearchParams({ q, status: "done,cancelled", limit: "1", offset: "0" }).toString()}`)
      .then((r) => { if (live && typeof r.total === "number") setClosedTotal(r.total); }).catch(() => undefined);
    return () => { live = false; };
  }, [q, reloadKey]);

  // The paged API filtered on the server, which matches more than this screen can (a job's client,
  // say): its page is shown as sent, so the rows and the total agree. Only the older API, which
  // sends the whole list, is narrowed here.
  const paged = total !== null;
  const narrowed = paged ? (items || [])
    : applyFilters(items || [], { me: meId, scope: scope === "me" ? "mine" : "everyone", status: "all", q, assignee: scope === "me" || scope === "all" ? "" : scope });
  const count = paged ? (total as number) : narrowed.length;
  const shown = paged ? narrowed : narrowed.slice((page - 1) * PAGE, page * PAGE);
  const closed = (t: Task) => !!t.completed_at || ["done", "cancelled"].includes(normStatus(t.status));
  // The older API sent every match, so counting here is the full answer; the paged one says it above.
  const history = paged ? closedTotal : narrowed.filter(closed).length;

  async function toggle(t: Task) {
    const done = !closed(t);
    try {
      await api.patch(`/tasks/${t.id}`, { status: done ? "done" : "todo" });
      flash(done ? `Done: ${t.title}` : `Reopened: ${t.title}`);
      load(); onChanged();
    } catch { flash("Couldn't save the change. Try again."); }
  }

  return (
    <div className="space-y-3">
      {(q || note) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-ink-secondary" role="status">
          {q ? (
            <span>{items === null ? "Searching every task…" : <><b className="font-semibold text-ink-primary tabular-nums">{count}</b> {count === 1 ? "task matches" : "tasks match"} “{q}” across the shop{history ? `, ${history} from history` : ""}</>}</span>
          ) : <span>{note}</span>}
          {q && onClear && <Button size="sm" onClick={onClear} icon={<M n="close" c="text-sm" />}>Clear search</Button>}
        </div>
      )}
      <section aria-label={q ? "Search results" : "Tasks"} className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
        {items === null ? <ListSkeleton />
          : failed && !items.length ? <ErrorState title="Couldn't load the tasks" onRetry={load} />
          : shown.length === 0 ? (q
            ? <EmptyState icon={<M n="search" c="text-lg" />} title={`No tasks match "${q}"`} body="Try a work order number, a client's name or another word from the task." action={onClear ? <Button onClick={onClear}>Clear search</Button> : undefined} />
            : <EmptyState icon={<M n="task_alt" c="text-lg" />} title="No tasks here yet" body="Add one above when something comes up." />)
          : <ul className="divide-y divide-slate-100">{shown.map((t) => <ResultRow key={t.id} t={t} tz={tz} done={closed(t)} onOpen={() => onOpen(t)} onToggle={() => toggle(t)} />)}</ul>}
        {items !== null && count > PAGE && <Pager total={count} page={page} pageSize={PAGE} onPage={setPage} noun="tasks" />}
      </section>
    </div>
  );
}

function ResultRow({ t, tz, done, onOpen, onToggle }: { t: Task; tz: string | null; done: boolean; onOpen: () => void; onToggle: () => void }) {
  const status = normStatus(t.status);
  const cancelled = status === "cancelled" && !t.completed_at;
  const names = (t.assignee_names && t.assignee_names.length) ? t.assignee_names : t.assignee_name ? [t.assignee_name] : [];
  const prio = normPriority(t.priority);
  const day = (iso?: string | null) => (iso && tz ? fmtShop(iso, tz, { day: "numeric", month: "short" }) : "");
  // A due time on the shop's clock; a date-only task (end of the shop day) shows just the date.
  const clock = t.due_at && tz ? dueClock(toShopWall(t.due_at, tz)) : null;
  const due = t.due_at && tz ? `${day(t.due_at)}${clock ? `, ${fmtClock(clock)}` : ""}` : "";
  return (
    <li className="group relative flex items-center gap-3 px-4 py-3 transition-colors hover:bg-slate-50/80">
      {cancelled ? <span className="w-5 h-5 flex-shrink-0" aria-hidden="true" /> : (
        <button type="button" onClick={onToggle} aria-pressed={done} aria-label={done ? `Mark "${t.title}" not done` : `Mark "${t.title}" done`}
          className={`relative z-10 hit44 w-5 h-5 rounded-full border-[1.5px] grid place-items-center flex-shrink-0 transition-colors ${done ? "bg-emerald-500 border-emerald-500 text-white" : "border-slate-300 bg-white hover:border-primary"}`}>
          {done && <M n="check" c="text-xs" />}
        </button>
      )}
      <div className="min-w-0 flex-1">
        <button type="button" onClick={onOpen}
          className={`block max-w-full line-clamp-2 break-words text-left text-sm font-medium rounded after:absolute after:inset-0 after:content-[''] focus:outline-none focus-visible:after:ring-2 focus-visible:after:ring-inset focus-visible:after:ring-primary/40 ${done || cancelled ? "text-ink-tertiary line-through decoration-slate-300" : "text-ink-primary"}`}>
          {t.title}
        </button>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-2xs text-ink-tertiary">
          {t.order_id ? (
            <Link to={`/orders?open=${encodeURIComponent(t.order_id)}`}
              className="relative z-10 inline-flex items-center gap-1 h-5 px-1.5 rounded-md bg-slate-100 text-ink-secondary font-semibold hover:bg-blue-50 hover:text-primary max-w-[240px]">
              <M n="handyman" c="text-2xs flex-shrink-0" /><span className="sr-only">Open work order </span><span className="tabular-nums whitespace-nowrap">{t.order_number || "Work order"}</span>
              {t.client_name && <span className="font-medium truncate">· {t.client_name}</span>}
            </Link>
          ) : t.client_name ? <span className="truncate">{t.client_name}</span> : null}
          {t.completed_at ? <Badge tone="success" size="sm">Done {day(t.completed_at)}</Badge>
            : cancelled ? <Badge tone="neutral" size="sm">Cancelled</Badge>
            : status === "in_progress" ? <Badge tone="info" size="sm">In progress</Badge> : null}
          {prio > 0 && !done && !cancelled && <Badge tone={prio === 2 ? "danger" : "warning"} size="sm">{PRIORITY_LABEL[prio]}</Badge>}
          {isAuto(t) && <span title={AUTO_HINT}><Badge tone="neutral" size="sm" className="gap-0.5"><M n="bolt" c="text-2xs" />{AUTO_LABEL}<span className="sr-only">: {AUTO_HINT}</span></Badge></span>}
        </div>
      </div>
      {names.length > 0 ? (
        <span className="hidden sm:flex gap-1 flex-shrink-0" title={names.join(", ")} aria-label={`Assigned to ${names.join(", ")}`} role="img">
          {names.slice(0, 3).map((n, i) => <Avatar key={i} name={n} size={24} />)}
          {names.length > 3 && <span className="rounded-full w-6 h-6 bg-slate-100 text-2xs font-semibold text-ink-secondary grid place-items-center">+{names.length - 3}</span>}
        </span>
      ) : !done && !cancelled ? <Badge tone="warning" size="sm" className="flex-shrink-0">Up for grabs</Badge> : null}
      {!done && !cancelled && due && <span className={`min-w-[72px] text-right text-xs tabular-nums whitespace-nowrap flex-shrink-0 ${t.overdue ? "text-amber-700 font-medium" : "text-ink-tertiary"}`}>
        <span className="sr-only">{t.overdue ? "Was due " : "Due "}</span>{due}</span>}
    </li>
  );
}

function ListSkeleton() {
  return (
    <div role="status" aria-label="Loading tasks">
      <span className="sr-only">Loading…</span>
      {[58, 44, 66, 38].map((w, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3.5 border-t first:border-t-0 border-slate-100" aria-hidden="true">
          <span className="sk block w-5 h-5 rounded-full flex-shrink-0" />
          <span className="flex-1"><span className="sk block h-3 rounded" style={{ width: `${w}%` }} /><span className="sk block h-2.5 rounded mt-2" style={{ width: "20%" }} /></span>
        </div>
      ))}
    </div>
  );
}
