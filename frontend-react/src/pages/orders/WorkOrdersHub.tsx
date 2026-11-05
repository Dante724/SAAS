import { askCancelReason, droppedStatus } from "../../lib/cancelReason";
import { fmtDay } from "../../lib/serverTime";
import { tint } from "../../lib/tint";
import PageSummary, { type SummaryStat } from "../../components/PageSummary";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { alertDialog, promptDialog } from "../../lib/dialog";
import { Order, deviceName, money } from "./types";
import WODrawer from "./WODrawer";
import { Avatar, dueText } from "./look";
import { JobLabelsButton } from "./JobLabels";
import CreateOrderDrawer from "./CreateOrderDrawer";
import Pager from "../../components/Pager";
import { autoPrintIfClosed } from "../../lib/autoPrint";
import { Gate, useMe } from "../../context/Me";
import FloatingMenu from "../../components/FloatingMenu";
import { loadFailed, showNotice } from "../../lib/notice";
import { clickable, clickableRow } from "../../lib/clickable";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Button, { IconButton, buttonClass } from "../../components/Button";
import Tabs from "../../components/Tabs";
import Card from "../../components/Card";
import Drawer from "../../components/Drawer";
import EmptyState, { ErrorState, TableEmpty } from "../../components/EmptyState";
import SkeletonRows from "../../components/SkeletonRows";
import { Field, Select } from "../../components/Field";
import { tableClass, theadRowClass, Th } from "../../components/Table";
import SearchBox from "./SearchBox";

type Kpis = { new?: number; urgent?: number; overdue?: number; receivables?: number };
type Status = { id: string; name?: string; key?: string; status_group?: string; color?: string };
type SavedView = { id: string; name: string; params: any; is_shared?: boolean; mine?: boolean };
type Facets = { status_counts: Record<string, number>; assignees: { id: string; name: string }[]; open_jobs: number };
type BoardCard = { id: string; order_number?: string; status?: string; priority?: number; total?: number; client_id?: string | null; client_name?: string | null; device?: string | null; assigned_to_id?: string | null; assigned_to_name?: string | null; scheduled_for?: string | null };
// Board cards are a slimmer shape than list rows; map them onto Order so one card renderer serves both.
const cardToOrder = (c: BoardCard): Order => ({
  id: c.id, order_number: c.order_number, status: c.status, priority: c.priority, total: c.total, is_estimate: false,
  client: c.client_name ? { id: c.client_id || undefined, name: c.client_name } : null,
  device: c.device ? { brand: c.device } : null,
  assigned_to: c.assigned_to_name ? { name: c.assigned_to_name } : null,
  scheduled_for: c.scheduled_for,
});
const WO_PAGE_SIZE = 50;

// Toggleable table columns (the checkbox column is always shown)
const COLS: { key: string; label: string }[] = [
  { key: "order_id", label: "Work order" }, { key: "client", label: "Customer" },
  { key: "device", label: "Device / Asset" }, { key: "status", label: "Status" },
  { key: "assignee", label: "Technician" }, { key: "scheduled", label: "Scheduled" },
  { key: "estimate", label: "Total" },
];
const COLS_KEY = "ros_wo_cols";

// status → Stitch pill (bg / dot / text)
const PILL: Record<string, [string, string]> = {
  new: ["bg-blue-100 text-blue-800 border-blue-200", "bg-blue-600"],
  diagnosed: ["bg-blue-100 text-blue-800 border-blue-200", "bg-blue-600"],
  waiting_parts: ["bg-amber-100 text-amber-800 border-amber-200", "bg-amber-600"],
  in_progress: ["bg-purple-100 text-purple-800 border-purple-200", "bg-purple-600"],
  in_repair: ["bg-purple-100 text-purple-800 border-purple-200", "bg-purple-600"],
  ready: ["bg-green-100 text-green-800 border-green-200", "bg-green-600"],
  done: ["bg-green-100 text-green-800 border-green-200", "bg-green-600"],
  delivered: ["bg-slate-100 text-slate-700 border-slate-200", "bg-slate-500"],
};
// The status's own name and colour from Settings › Statuses; the key is only a fallback.
// Used by the table, the board and the scheduler alike, so a status looks the same everywhere.
function statusTone(status?: string, st?: Status): { cls: string; style?: React.CSSProperties; dotCls: string; dotStyle?: React.CSSProperties } {
  if (st?.color) {
    return { cls: "text-ink-primary", style: { background: tint(st.color), boxShadow: `inset 0 0 0 1px ${tint(st.color, 25)}` }, dotCls: "", dotStyle: { background: st.color } };
  }
  const key = (status || "").toLowerCase().replace(/\s+/g, "_");
  const [cls, dot] = PILL[key] || ["bg-slate-100 text-slate-700 border-slate-200", "bg-slate-500"];
  return { cls: `border ${cls}`, dotCls: dot };
}
function pill(status?: string, st?: Status, small = false) {
  const label = st?.name || (status || "Not set").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const t = statusTone(status, st);
  return (
    <span className={`${small ? "px-1.5 py-0.5 gap-1" : "h-6 px-2.5 gap-1.5"} rounded-full text-2xs font-semibold inline-flex items-center whitespace-nowrap max-w-full truncate ${t.cls}`} style={t.style}>
      <span className={`${small ? "w-1 h-1" : "w-1.5 h-1.5"} rounded-full flex-shrink-0 ${t.dotCls}`} style={t.dotStyle} />{label}
    </span>
  );
}

export default function WorkOrdersHub() {
  const [orders, setOrders] = useState<Order[] | null>(null);   // the current table page, or the board's cards
  const [total, setTotal] = useState(0);                         // rows matching the filters (server count)
  const [facets, setFacets] = useState<Facets | null>(null);
  const [boardCounts, setBoardCounts] = useState<Record<string, number>>({});
  const [tick, setTick] = useState(0);
  const [flags, setFlags] = useState<Record<string, { stalled: boolean; predicted_ready_at: string | null; over_by_min: number | null }>>({});
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<Order | null>(null);
  const [create, setCreate] = useState<null | "order" | "estimate">(null);
  const [statuses, setStatuses] = useState<Status[]>([]);
  const [estStatuses, setEstStatuses] = useState<Status[]>([]);
  const [filter, setFilter] = useState<string | null>(null);   // status key
  // Multi-facet filter panel — sent to the server with status + text, so paging stays exact.
  // One open toolbar menu at a time (FloatingMenu: closes on outside click or Escape).
  const [menu, setMenu] = useState<null | "filter" | "cols" | "views">(null);
  const toggleMenu = (m: "filter" | "cols" | "views") => setMenu((cur) => (cur === m ? null : m));
  const closeMenu = () => setMenu(null);
  const [fAssignee, setFAssignee] = useState("");              // "" any · "__none" unassigned · else employee id
  const [fPriority, setFPriority] = useState("");              // "" any · "0"|"1"|"2" · "urgent" = 1 or 2
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [fScheduled, setFScheduled] = useState<"" | "yes" | "no">("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  // Archive and restore need both delete permissions (same rule as the server's bulk archive).
  const { can } = useMe();
  const canArchive = can("can_delete_orders") && can("can_delete_tickets");
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");   // debounced search text sent to the server
  useEffect(() => { const t = setTimeout(() => setDq(q.trim()), 300); return () => clearTimeout(t); }, [q]);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<"table" | "board" | "scheduler">("table");
  const [woPage, setWoPage] = useState(1);   // table-view pagination
  const [drag, setDrag] = useState<string | null>(null);
  // Scheduler view — legacy Orders "Scheduler" with Day / Week / Month / Techs (week timeline by technician).
  const [schedMode, setSchedMode] = useState<"day" | "week" | "month" | "techs">("week");
  const [anchor, setAnchor] = useState<Date>(() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; });
  const [sched, setSched] = useState<any[] | null>(null);
  const monday = (d: Date) => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
  const isoD = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  // Range fetched depends on the mode: day→that day, week/techs→that week, month→the whole 6-week grid.
  function schedRange(): [Date, Date] {
    if (schedMode === "day") return [anchor, anchor];
    if (schedMode === "month") {
      const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
      const gridStart = monday(first);
      const gridEnd = new Date(gridStart); gridEnd.setDate(gridEnd.getDate() + 41);
      return [gridStart, gridEnd];
    }
    const ws = monday(anchor); const we = new Date(ws); we.setDate(we.getDate() + 6);
    return [ws, we];
  }
  useEffect(() => {
    if (view !== "scheduler") return;
    const [from, to] = schedRange();
    setSched(null);
    api.get<any>(`/orders/scheduler/range?from_str=${isoD(from)}&to_str=${isoD(to)}`).then((r) => setSched(r.items || [])).catch(() => setSched([]));
  }, [view, schedMode, anchor]);
  const [cols, setCols] = useState<Set<string>>(() => {
    try { const s = localStorage.getItem(COLS_KEY); if (s) return new Set(JSON.parse(s)); } catch { /* ignore */ }
    return new Set(COLS.map((c) => c.key));
  });
  const [views, setViews] = useState<SavedView[]>([]);
  const showCol = (k: string) => cols.has(k);
  function toggleCol(k: string) {
    setCols((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); try { localStorage.setItem(COLS_KEY, JSON.stringify([...n])); } catch { /* ignore */ } return n; });
  }
  function loadViews() { api.get<{ items: SavedView[] }>("/orders/filters/saved").then((r) => setViews(r.items || [])).catch(() => setViews([])); }
  useEffect(() => { loadViews(); }, []);
  async function saveView() {
    const name = await promptDialog("Name this view:"); if (!name) return;
    try { await api.post("/orders/filters/saved", { name, params: { q, filter, view, cols: [...cols], fAssignee, fPriority, fScheduled }, is_shared: false }); loadViews(); }
    catch (e: any) { await alertDialog(e.message || "Couldn't save the view. Try again."); }
  }
  function applyView(v: SavedView) {
    const p = v.params || {};
    if (p.q != null) setQ(p.q); setFilter(p.filter ?? null);
    // Views saved before server paging stored the assignee's name; map it to the id.
    const a = p.fAssignee ?? "";
    setFAssignee(a && a !== "__none" ? (facets?.assignees.find((x) => x.id === a || x.name === a)?.id ?? a) : a); setFPriority(p.fPriority ?? ""); setFScheduled(p.fScheduled ?? "");
    if (p.view) setView(p.view);
    if (Array.isArray(p.cols)) { const n = new Set<string>(p.cols); setCols(n); try { localStorage.setItem(COLS_KEY, JSON.stringify([...n])); } catch { /* ignore */ } }
    setMenu(null);
  }
  async function deleteView(id: string, e: any) {
    e.stopPropagation();
    try { await api.del(`/orders/filters/saved/${id}`); loadViews(); } catch { /* ignore */ }
  }
  const colSpan = 1 + [...cols].length;
  async function moveTo(id: string, key: string) {
    const o = (orders || []).find((x) => x.id === id); if (!o || norm(o.status) === key) return;
    let reason: string | null = null;
    { const ds = droppedStatus(statuses, key); if (ds) { reason = await askCancelReason(ds.name); if (reason === null) return; } }
    setOrders((cur) => (cur || []).map((x) => x.id === id ? { ...x, status: key } : x)); // optimistic
    // A refused move (a comment or payment still needed…) says why instead of snapping back silently.
    try { const r = await api.patch<any>(`/orders/${id}/status`, { status: key, reason: reason || undefined }); void autoPrintIfClosed(id, r); }
    catch (e: any) { refetch(); alertDialog(`${e?.message || "The status didn't change."} Open the job to finish it there.`, `${o.order_number || "This job"} can't move yet`); }
  }

  // Filters go to the server: the table fetches one page plus facet counts; the board fetches
  // capped open-order columns. Neither loads the whole shop any more.
  function filterParams() {
    const p = new URLSearchParams();
    if (dq) p.set("search", dq);
    if (fAssignee) p.set("assigned_to_id", fAssignee);
    if (fPriority === "urgent") p.set("priority_min", "1"); else if (fPriority) p.set("priority", fPriority);
    if (fScheduled) p.set("scheduled", fScheduled);
    return p;
  }
  useEffect(() => {
    if (view === "scheduler") return;
    let live = true;
    const p = filterParams();
    if (view === "board") {
      p.set("per_column", "50");
      api.get<{ columns: { cards: BoardCard[] }[]; status_counts?: Record<string, number> }>(`/orders/board?${p}`)
        .then((r) => { if (!live) return; setOrders(r.columns.flatMap((c) => c.cards || []).map(cardToOrder)); setBoardCounts(r.status_counts || {}); setErr(null); })
        .catch((e) => live && setErr(String(e.message || e)));
    } else {
      if (filter) p.set("status", filter);
      p.set("skip", String((woPage - 1) * WO_PAGE_SIZE)); p.set("limit", String(WO_PAGE_SIZE)); p.set("facets", "true");
      api.get<{ items: Order[]; total: number } & Partial<Facets>>(`/orders?${p}`)
        .then((r) => {
          if (!live) return;
          setOrders(r.items || []); setTotal(r.total || 0); setErr(null);
          setFacets({ status_counts: r.status_counts || {}, assignees: r.assignees || [], open_jobs: r.open_jobs ?? 0 });
        })
        .catch((e) => live && setErr(String(e.message || e)));
    }
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, woPage, dq, filter, fAssignee, fPriority, fScheduled, tick]);
  // Stall badges only for the rows or cards on screen (the endpoint otherwise walks every open
  // order). The board can hold more cards than one request takes, so its ids go in chunks.
  const flagIds = view !== "scheduler" ? (orders || []).map((o) => o.id).join(",") : "";
  useEffect(() => {
    if (!flagIds) { setFlags({}); return; }
    let live = true;
    const ids = flagIds.split(","), chunks: string[][] = [];
    for (let i = 0; i < ids.length; i += 100) chunks.push(ids.slice(i, i + 100));
    Promise.all(chunks.map((c) => api.get<{ items: Record<string, { stalled: boolean; predicted_ready_at: string | null; over_by_min: number | null }> }>(`/orders/timing/flags?ids=${encodeURIComponent(c.join(","))}`)
      .then((r) => r.items || {}).catch(() => ({}))))
      .then((parts) => { if (live) setFlags(Object.assign({}, ...parts)); });
    return () => { live = false; };
  }, [flagIds]);
  const stalledTag = (id: string) => flags[id]?.stalled ? (
    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-2xs font-semibold bg-rose-100 text-rose-700 border border-rose-200" title={flags[id]?.over_by_min ? `About ${Math.round(flags[id]!.over_by_min!)} min over typical` : "Running long"}><M n="timer" c="text-2xs" />Stalled</span>
  ) : null;
  function refetch() {
    setTick((t) => t + 1);
    // Same numbers as the Home dashboard (one server-side definition of urgent/overdue).
    api.get<{ kpis?: Kpis }>("/reports/company-dashboard").then((r) => setKpis(r.kpis || {})).catch(() => setKpis(null));
  }
  useEffect(() => {
    refetch(); api.get<any>("/statuses").then((r) => setStatuses(r.items || r || [])).catch(loadFailed("statuses"));
    api.get<any>("/statuses?record_type=estimate").then((r) => setEstStatuses(r.items || r || [])).catch(loadFailed("statuses"));
  }, []);
  const stOf = (o: Order) => (o.is_estimate ? estStatuses : statuses).find((x) => norm(x.key || x.name) === norm(o.status))
    || [...statuses, ...estStatuses].find((x) => norm(x.key || x.name) === norm(o.status));
  // Deep-open from a scan (?open=<id>): open the WO drawer straight away, then clear the param.
  const [sp, setSp] = useSearchParams();
  const openParam = sp.get("open");
  useEffect(() => {
    if (openParam) { setOpen({ id: openParam } as any); setSp((p) => { p.delete("open"); return p; }, { replace: true }); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openParam]);
  useEffect(() => {
    // Deep-link filters from Home KPI cards (?status=new, ?priority=urgent). Applied once,
    // then cleared so the URL stays clean and the user can change filters freely.
    const st = sp.get("status"), pr = sp.get("priority");
    if (st) setFilter(st.toLowerCase().replace(/\s+/g, "_"));
    if (pr) setFPriority(pr);
    if (st || pr) setSp((p) => { p.delete("status"); p.delete("priority"); return p; }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const norm = (s?: string) => (s || "").toLowerCase().replace(/\s+/g, "_");
  const all = orders || [];
  // Rows are already filtered by the server; the board's status chip narrows its cards locally.
  const rows = view === "board" && filter ? all.filter((o) => norm(o.status) === filter) : all;
  const dash = (n?: number) => (kpis && n != null ? String(n) : "\u00a0");
  const kpiStats: SummaryStat[] = [
    { label: "Open jobs", value: facets ? String(facets.open_jobs) : "\u00a0", onClick: () => { setFilter(null); setFPriority(""); }, active: !filter && !fPriority },
    { label: "Waiting to start", value: dash(kpis?.new), onClick: () => setFilter(filter === "new" ? null : "new"), active: filter === "new" },
    { label: "Urgent", value: dash(kpis?.urgent), onClick: () => setFPriority(fPriority === "urgent" ? "" : "urgent"), active: fPriority === "urgent" },
    { label: "Late", value: dash(kpis?.overdue), tone: "bad", zero: !kpis?.overdue },
    { label: "To collect", value: kpis?.receivables != null ? money(kpis.receivables) : "\u00a0" },
  ];
  const assignees = facets?.assignees || [];
  const activeFacets = (fAssignee ? 1 : 0) + (fPriority ? 1 : 0) + (fScheduled ? 1 : 0);
  const statusCounts = view === "board" ? boardCounts : (facets?.status_counts || {});
  const countFor = (key: string) => Object.entries(statusCounts).reduce((n, [k, v]) => n + (norm(k) === key ? v : 0), 0);
  const countAll = Object.values(statusCounts).reduce((n, v) => n + v, 0);
  const filtersOn = !!(filter || activeFacets || q.trim());
  const clearFilters = () => { setFilter(null); setFAssignee(""); setFPriority(""); setFScheduled(""); setQ(""); };
  // The empty message never repeats "New work order": it's the first button in the toolbar.
  const emptyProps = filtersOn
    ? { icon: <M n="filter_list" c="text-lg" />, title: "No work orders match these filters", action: <Button onClick={clearFilters}>Clear filters</Button> }
    : { icon: <M n="handyman" c="text-lg" />, title: "No work orders yet", body: "New jobs and estimates show up here." };
  // Numbered pagination — table view only (board = kanban columns, scheduler = calendar).
  const woPages = Math.max(1, Math.ceil(total / WO_PAGE_SIZE));
  const pageRows = rows;
  // Back to page 1 when the filtered set or view changes; a selection never outlives the rows it was made on.
  useEffect(() => { setWoPage(1); setSel(new Set()); }, [q, filter, view, fAssignee, fPriority, fScheduled]);
  useEffect(() => { setSel(new Set()); }, [woPage]);

  function toggle(id: string) { setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }); }
  function toggleAll() { setSel((s) => s.size === rows.length && rows.length > 0 ? new Set() : new Set(rows.map((o) => o.id))); }
  async function bulkStatus(key: string) {
    if (!key || sel.size === 0) return;
    let reason: string | null = null;
    { const ds = droppedStatus(statuses, key); if (ds) { reason = await askCancelReason(ds.name, sel.size); if (reason === null) return; } }
    setBusy(true);
    // Single atomic call (was N per-row PATCHes). action=set_status.
    try { await api.post("/orders/bulk", { order_ids: [...sel], action: "set_status", value: key, reason: reason || undefined }); setSel(new Set()); refetch(); }
    catch { let failed = 0, why = ""; for (const id of sel) { await api.patch(`/orders/${id}/status`, { status: key, reason: reason || undefined }).catch((e: any) => { failed++; why ||= e?.message || ""; }); } if (failed) showNotice(failed === 1 && why ? why : `Couldn't change the status of ${failed} job${failed === 1 ? "" : "s"}.${why ? ` ${why}` : ""}`); setSel(new Set()); refetch(); }
    finally { setBusy(false); }
  }
  // 144-G: bulk archive (soft-delete, reversible). Orders carrying unrefunded payments are
  // skipped server-side and reported back.
  async function bulkArchive() {
    if (sel.size === 0) return;
    const reason = await promptDialog(`Archive ${sel.size} work order(s)? They move to the archive and can be restored. Reason:`, { defaultValue: "" });
    if (reason === null) return;
    setBusy(true);
    try {
      const r = await api.post<any>("/orders/bulk", { order_ids: [...sel], action: "archive", reason, refund: true });
      setSel(new Set()); refetch();
      if (r.skipped?.length) await alertDialog(`${r.message}. Skipped: ${r.skipped.map((s: any) => s.reason).join("; ")}`);
    } catch (e: any) { await alertDialog(e.message || "Couldn't archive the work orders. Try again."); }
    finally { setBusy(false); }
  }
  // Archived orders overlay (backend: GET /orders/archived).
  const [archived, setArchived] = useState<any[] | null | "loading">(null);
  function openArchived() { setArchived("loading"); api.get<{ items: any[] }>("/orders/archived").then((r) => setArchived(r.items || [])).catch(() => setArchived([])); }
  async function restoreOne(id: string) {
    try { await api.post(`/orders/${id}/restore`, {}); openArchived(); refetch(); }
    catch (e: any) { await alertDialog(e.message || "Couldn't restore the work order. Try again."); }
  }

  const chipTabs = [{ id: "__all", k: null as string | null, name: "All", n: countAll }, ...statuses.map((s) => { const k = s.key || norm(s.name); return { id: s.id, k, name: s.name || k, n: countFor(k) }; })];
  const onScheduler = view === "scheduler";

  return (
    <Shell title="Work orders">
      <PageLayout>
        {/* Toolbar: actions on the left (main one first), the view switch, then filters and search on
            the right. Every control stays in place on every view; what doesn't apply is greyed out. */}
        <Toolbar
          actions={<>
            <Gate perm="can_create_tickets"><Button variant="primary" onClick={() => setCreate("order")} icon={<M n="add" c="text-sm" />}>New work order</Button></Gate>
            <Gate perm="can_create_estimates"><Button onClick={() => setCreate("estimate")} icon={<M n="request_quote" c="text-sm" />}>New estimate</Button></Gate>
            <Button data-wo-menu="views" aria-expanded={menu === "views"} aria-label="Saved views" title="Saved views" onClick={() => toggleMenu("views")} icon={<M n="bookmark" c="text-sm" />}><span className="hidden 2xl:inline">Saved views</span></Button>
            <Button onClick={openArchived} aria-label="Archived" title="Archived" icon={<M n="inventory" c="text-sm" />}><span className="hidden 2xl:inline">Archived</span></Button>
          </>}
          view={<Tabs look="segmented" label="View" value={view} onChange={setView} tabs={[
            { id: "table", label: "Table", icon: <M n="table_rows" c="text-xs" /> },
            { id: "board", label: "Board", icon: <M n="view_kanban" c="text-xs" /> },
            { id: "scheduler", label: "Scheduler", icon: <M n="calendar_month" c="text-xs" /> },
          ]} />}
          filters={<>
            <Button data-wo-menu="filter" aria-expanded={menu === "filter"} aria-label="Filter" title="Filter" onClick={() => toggleMenu("filter")} className={activeFacets ? "!bg-primary/10 !text-primary !border-primary/30" : ""} icon={<M n="filter_list" c="text-sm" />}>
              <span className="hidden xl:inline">Filter</span>{activeFacets ? <span className="ml-0.5 px-1.5 rounded-full bg-primary text-white text-2xs font-semibold tabular-nums">{activeFacets}</span> : null}</Button>
            {/* Always rendered, so Search and Filter never slide when the view changes; greyed out off the table. */}
            <Button data-wo-menu="cols" aria-expanded={menu === "cols"} aria-label="Columns" title={view === "table" ? "Columns" : "Columns apply to the table view"} disabled={view !== "table"} onClick={() => toggleMenu("cols")} icon={<M n="view_column" c="text-sm" />}><span className="hidden xl:inline">Columns</span></Button>
          </>}
          search={<SearchBox value={q} onChange={setQ} label="Search work orders" placeholder="Search work orders…" className="sm:w-56" />}
        />
          {menu === "views" && (
            <FloatingMenu anchorSel='[data-wo-menu="views"]' onClose={closeMenu} width={240}>
              <button onClick={() => { closeMenu(); saveView(); }} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 focus:bg-slate-50 outline-none text-primary text-xs font-semibold"><M n="add" c="text-sm" />Save current view</button>
              <div className="border-t border-slate-100 my-1" />
              {views.length === 0 && <EmptyState size="inline" className="px-2 !py-2" title="No saved views yet." />}
              {views.map((v) => (
                <div key={v.id} className="group flex items-center gap-1 rounded-lg hover:bg-slate-50 focus-within:bg-slate-50">
                  <button onClick={() => applyView(v)} className="flex-1 min-w-0 flex items-center gap-2 px-2 py-1.5 text-left text-xs font-medium text-ink-primary outline-none">
                    <M n="bookmark_added" c="text-sm text-ink-tertiary" /><span className="truncate">{v.name}</span>
                  </button>
                  {v.mine && <button aria-label={`Delete view ${v.name}`} title="Delete view" onClick={(e) => deleteView(v.id, e)} className="hit44 mr-1 w-6 h-6 grid place-items-center rounded-md opacity-0 group-hover:opacity-100 focus:opacity-100 focus-visible:ring-2 focus-visible:ring-primary/40 outline-none text-ink-tertiary hover:text-rose-600"><M n="delete" c="text-xs" /></button>}
                </div>
              ))}
            </FloatingMenu>
          )}
          {menu === "filter" && (
            <FloatingMenu anchorSel='[data-wo-menu="filter"]' onClose={closeMenu} width={256}>
              <div className="p-1.5 space-y-3">
                <div className="flex items-center justify-between"><span className="text-xs font-semibold text-ink-tertiary">Filter work orders</span>{activeFacets ? <Button variant="link" onClick={() => { setFAssignee(""); setFPriority(""); setFScheduled(""); }}>Clear</Button> : null}</div>
                <Field label="Technician" htmlFor="wo-f-tech">
                  <Select id="wo-f-tech" value={fAssignee} onChange={(e) => setFAssignee(e.target.value)}><option value="">Any</option><option value="__none">Unassigned</option>{assignees.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select></Field>
                {fAssignee && fAssignee !== "__none" && (
                  <JobLabelsButton pick={{ assignedToId: fAssignee }} label="Print QR labels for all their open jobs" className="text-xs font-semibold text-primary hover:underline text-left" />
                )}
                <Field label="Priority" htmlFor="wo-f-prio">
                  <Select id="wo-f-prio" value={fPriority} onChange={(e) => setFPriority(e.target.value)}><option value="">Any</option><option value="0">Normal</option><option value="1">High</option><option value="2">Urgent</option><option value="urgent">High or urgent</option></Select></Field>
                <Field label="Scheduling" htmlFor="wo-f-sched" hint={onScheduler ? "The scheduler only shows scheduled jobs." : undefined}>
                  <Select id="wo-f-sched" value={fScheduled} disabled={onScheduler} onChange={(e) => setFScheduled(e.target.value as "" | "yes" | "no")}><option value="">Any</option><option value="yes">Scheduled</option><option value="no">Unscheduled</option></Select></Field>
              </div>
            </FloatingMenu>
          )}
          {menu === "cols" && view === "table" && (
            <FloatingMenu anchorSel='[data-wo-menu="cols"]' onClose={closeMenu} width={208}>
              <div className="px-2 py-1 text-xs font-semibold text-ink-tertiary">Visible columns</div>
              {COLS.map((c) => (
                <label key={c.key} className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 cursor-pointer text-xs font-medium text-ink-primary">
                  <input type="checkbox" checked={showCol(c.key)} onChange={() => toggleCol(c.key)} className="w-4 h-4 accent-primary" />{c.label}
                </label>
              ))}
            </FloatingMenu>
          )}

        <PageSummary stats={kpiStats} />

        {/* Status chips. The scheduler shows every scheduled job in its date range, so there they stay
            in place but greyed out: the list below never jumps up or down when the view changes. */}
        <Tabs look="chips" label="Status" value={chipTabs.find((c) => c.k === filter)?.id ?? "__all"} onChange={(id) => setFilter(chipTabs.find((c) => c.id === id)?.k ?? null)}
          tabs={chipTabs.map((c) => ({ id: c.id, label: <>{c.name}{" "}</>, count: c.n, disabled: onScheduler }))} />

        {/* Bulk action bar */}
        {sel.size > 0 && (
          <div className="bg-white text-ink-primary rounded-xl border border-slate-200/90 px-4 py-2.5 flex flex-wrap items-center gap-3 text-xs font-semibold" role="region" aria-label="Selected work orders">
            <span className="tabular-nums">{sel.size} selected</span>
            <div className="h-4 w-px bg-slate-200" />
            <label className="flex items-center gap-2"><span className="text-ink-secondary font-medium">Set status</span>
            <Select disabled={busy} onChange={(e) => { bulkStatus(e.target.value); e.target.value = ""; }} className="!w-40 !h-8 text-xs">
              <option value="">Choose…</option>
              {statuses.map((s) => <option key={s.id} value={s.key || norm(s.name)}>{s.name}</option>)}
            </Select></label>
            <div className="h-4 w-px bg-slate-200" />
            <JobLabelsButton pick={{ orderIds: [...sel] }} label="Print QR labels" className={buttonClass("secondary", "sm")} />
            {canArchive && <Button size="sm" disabled={busy} onClick={bulkArchive} icon={<M n="archive" c="text-sm" />}>Archive</Button>}
            <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setSel(new Set())}>Clear selection</Button>
          </div>
        )}

        {/* Table */}
        {view === "table" && (
        <Card padded={false}>
          {/* Phones: one card per order instead of the wide table. */}
          <ul className="sm:hidden divide-y divide-slate-100" aria-label="Work orders">
            {err && <li><ErrorState title="The work orders didn't load" body={err} onRetry={refetch} /></li>}
            {!orders && !err && <li aria-hidden className="p-4 space-y-3"><span className="sk block h-4 w-2/3 rounded" /><span className="sk block h-4 w-1/2 rounded" /><span className="sk block h-4 w-3/5 rounded" /></li>}
            {orders && rows.length === 0 && <li><EmptyState {...emptyProps} /></li>}
            {pageRows.map((o) => (
              <li key={o.id}>
                <button onClick={() => setOpen(o)} className={`w-full text-left px-4 py-3 flex flex-col gap-1 hover:bg-slate-50/80 ${sel.has(o.id) ? "bg-primary/5" : ""}`}>
                  <span className="flex items-center gap-2">
                    <span className="font-medium tabular-nums text-primary text-xs whitespace-nowrap">{o.order_number || o.id}</span>
                    {!!(o.client_msg_unread) && <span role="img" className="w-1.5 h-1.5 rounded-full bg-primary" aria-label="Unread customer message" />}
                    <span className="ml-auto">{pill(o.status, stOf(o))}</span>
                  </span>
                  <span className="flex items-baseline gap-2">
                    <span className="text-sm font-semibold text-ink-primary truncate">{o.client?.name || "Walk-in"}</span>
                    <span className="ml-auto tabular-nums font-bold text-ink-primary text-xs">{money(o.total)}</span>
                  </span>
                  <span className="text-2xs text-ink-secondary truncate">{[deviceName(o.device), o.assigned_to?.name].filter((x) => x && x !== "—").join(" · ")}</span>
                </button>
              </li>
            ))}
          </ul>
          <table className={`hidden sm:table ${tableClass} xl:table-fixed`}>
            <thead>
              <tr className={theadRowClass}>
                <Th className="w-10 xl:w-12"><input className="w-4 h-4 rounded text-primary border-slate-300" type="checkbox" aria-label="Select all work orders" checked={rows.length > 0 && sel.size === rows.length} onChange={toggleAll} /></Th>
                {showCol("order_id") && <Th className="xl:w-28">Work order</Th>}
                {showCol("client") && <Th>Customer</Th>}
                {showCol("device") && <Th>Device / Asset</Th>}
                {showCol("status") && <Th className="xl:w-36">Status</Th>}
                {showCol("assignee") && <Th className="xl:w-36">Technician</Th>}
                {showCol("scheduled") && <Th className="xl:w-40">Scheduled</Th>}
                {showCol("estimate") && <Th num className="xl:w-28">Total</Th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
              {err && <tr><td colSpan={colSpan} className="p-0"><ErrorState title="The work orders didn't load" body={err} onRetry={refetch} /></td></tr>}
              {!orders && !err && <SkeletonRows cols={colSpan} />}
              {orders && rows.length === 0 && <TableEmpty cols={colSpan} {...emptyProps} />}
              {pageRows.map((o) => (
                <tr key={o.id} {...clickableRow(() => setOpen(o))} className={`hover:bg-slate-50/80 cursor-pointer transition-colors group ${sel.has(o.id) ? "bg-primary/5" : ""}`}>
                  <td className="py-3.5 px-4" onClick={(e) => e.stopPropagation()}><input className="w-4 h-4 rounded text-primary border-slate-300" type="checkbox" aria-label={`Select ${o.order_number || o.id}`} checked={sel.has(o.id)} onChange={() => toggle(o.id)} /></td>
                  {showCol("order_id") && <td className="py-3.5 px-4 font-medium tabular-nums text-primary whitespace-nowrap"><span className="inline-flex items-center gap-1.5">{o.order_number || o.id}{(o.priority || 0) >= 1 && <span role="img" aria-label="Urgent" title="Urgent"><M n="flag" c="text-sm text-rose-600" /></span>}{!!(o.client_msg_unread && o.client_msg_unread > 0) && <span title={`${o.client_msg_unread} unread customer message(s)`} className="inline-flex items-center gap-0.5 text-2xs font-semibold bg-primary text-white px-1.5 py-0.5 rounded-full"><M n="forum" c="text-2xs" />{o.client_msg_unread}</span>}{(o as any).checklists_pending && <span role="img" aria-label="Checklist incomplete" title="Checklist incomplete" className="w-2 h-2 rounded-full bg-amber-500" />}</span></td>}
                  {showCol("client") && <td className="py-3.5 px-4"><span className="flex items-center gap-2.5"><Avatar name={o.client?.name} size={30} /><span className="min-w-0"><span className="block font-semibold text-sm text-ink-primary truncate">{o.client?.name || "Walk-in"}</span>{o.client?.phone && <span className="text-2xs font-normal text-ink-tertiary block">{o.client.phone}</span>}</span></span></td>}
                  {showCol("device") && <td className="py-3.5 px-4 text-ink-primary font-semibold">{deviceName(o.device) === "—" ? "" : deviceName(o.device)}</td>}
                  {showCol("status") && <td className="py-3.5 px-4"><div className="flex items-center gap-1.5">{pill(o.status, stOf(o))}{stalledTag(o.id)}</div></td>}
                  {showCol("assignee") && <td className="py-3.5 px-4">{o.assigned_to?.name ? <span className="flex items-center gap-2"><Avatar name={o.assigned_to.name} size={22} />{o.assigned_to.name}</span> : ""}</td>}
                  {showCol("scheduled") && <td className="py-3.5 px-4 whitespace-nowrap">{o.scheduled_for ? fmtDay(o.scheduled_for) : ""}{(() => { const late = dueText(o.due_at, !["closed", "dropped"].includes(stOf(o)?.status_group || "")); return late?.[1] ? <span className="block text-2xs font-semibold text-rose-600">{late[0]}</span> : null; })()}</td>}
                  {showCol("estimate") && <td className="py-3.5 px-4 text-right tabular-nums whitespace-nowrap font-bold text-ink-primary">{money(o.total)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          {orders && rows.length > 0 && <Pager total={total} page={Math.min(woPage, woPages)} pageSize={WO_PAGE_SIZE} onPage={setWoPage} noun="work orders" />}
        </Card>
        )}

        {/* Board (kanban, drag to change status) */}
        {view === "board" && (
          <div className="flex gap-3 overflow-x-auto pb-2">   {/* columns share the width; scrolls only when they can't fit at 220px */}
            {err && <div className="flex-1"><ErrorState title="The board didn't load" body={err} onRetry={refetch} /></div>}
            {statuses.map((s) => {
              const key = s.key || norm(s.name);
              const cards = rows.filter((o) => norm(o.status) === key);
              const n = countFor(key);   // true count; the board only carries the newest open cards
              return (
                <div key={s.id}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => { if (drag) { moveTo(drag, key); setDrag(null); } }}
                  className="flex-1 min-w-[220px] bg-slate-50/70 rounded-2xl border border-slate-200/70 flex flex-col max-h-[calc(100vh-320px)]">
                  <div className="px-3 py-2.5 flex items-center justify-between border-b border-slate-200/70">
                    {pill(key, s, true)}
                    <span className="text-2xs font-semibold text-ink-tertiary tabular-nums">{n}</span>
                  </div>
                  <div className="p-2 space-y-2 overflow-y-auto">
                    {cards.length === 0 && <div className="text-2xs text-ink-tertiary px-2 py-4 text-center">Drop here</div>}
                    {cards.map((o) => (
                      <div key={o.id} draggable onDragStart={() => setDrag(o.id)} onDragEnd={() => setDrag(null)} {...clickable(() => setOpen(o))}
                        className={`bg-white rounded-xl border border-slate-200/80 shadow-clay-sm p-3 cursor-grab active:cursor-grabbing hover:border-primary/40 transition-colors ${drag === o.id ? "opacity-50" : ""}`}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="inline-flex items-center gap-1 font-medium tabular-nums text-primary text-xs">{o.order_number || o.id}{(o.priority || 0) >= 1 && <span role="img" aria-label="Urgent" title="Urgent"><M n="flag" c="text-sm text-rose-600" /></span>}</span>
                          <span className="text-2xs text-ink-tertiary">{o.scheduled_for ? fmtDay(o.scheduled_for) : ""}</span>
                        </div>
                        {flags[o.id]?.stalled && <div className="mb-1">{stalledTag(o.id)}</div>}
                        <div className="flex items-center gap-2"><Avatar name={o.client?.name} size={22} /><span className="text-xs font-semibold text-ink-primary truncate">{o.client?.name || "Walk-in"}</span></div>
                        <div className="text-2xs text-ink-secondary truncate">{deviceName(o.device) === "—" ? "" : deviceName(o.device)}</div>
                        <div className="mt-1.5 flex items-center justify-between">
                          <span className="flex items-center gap-1.5 text-2xs text-ink-tertiary">{o.assigned_to?.name && <Avatar name={o.assigned_to.name} size={18} />}{o.assigned_to?.name || "Unassigned"}</span>
                          <span className="tabular-nums font-semibold text-ink-primary text-xs">{money(o.total)}</span>
                        </div>
                      </div>
                    ))}
                    {cards.length > 0 && cards.length < n && <div className="text-2xs text-ink-tertiary px-2 py-1 text-center">Newest {cards.length} of {n}. The table shows them all.</div>}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {view === "scheduler" && (() => {
          // /scheduler/range takes only dates, so search and the Filter menu narrow the range here.
          const needle = dq.toLowerCase();
          const list = (sched || []).filter((o) =>
            (!needle || `${o.order_number || ""} ${o.client_name || ""}`.toLowerCase().includes(needle))
            && (!fAssignee || (fAssignee === "__none" ? !o.assigned_to_id : o.assigned_to_id === fAssignee))
            && (!fPriority || (fPriority === "urgent" ? (o.priority || 0) >= 1 : String(o.priority || 0) === fPriority)));
          const sameDay = (iso: string, d: Date) => { const x = new Date(iso); return x.getFullYear() === d.getFullYear() && x.getMonth() === d.getMonth() && x.getDate() === d.getDate(); };
          const hhmm = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
          const today = new Date(); today.setHours(0, 0, 0, 0);
          const step = (n: number) => setAnchor((a) => { const d = new Date(a); if (schedMode === "day") d.setDate(d.getDate() + n); else if (schedMode === "month") d.setMonth(d.getMonth() + n); else d.setDate(d.getDate() + n * 7); return d; });
          const dayItems = (d: Date) => list.filter((o) => o.scheduled_for && sameDay(o.scheduled_for, d)).sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for));
          const openCard = (o: any) => setOpen(all.find((x) => x.id === o.id) || o);
          // compact order card
          const SchedCard = (o: any, showTime = true) => {
            return (
              <button key={o.id} onClick={() => openCard(o)} className="w-full text-left bg-white rounded-lg border border-slate-200/80 shadow-clay-sm p-2 hover:border-primary/40 transition-colors">
                <div className="flex items-center justify-between gap-1">
                  <span className="font-medium tabular-nums text-primary text-2xs truncate">{o.order_number || o.id?.slice(0, 6)}</span>
                  {showTime && <span className="text-2xs font-semibold text-ink-tertiary flex-shrink-0">{hhmm(o.scheduled_for)}</span>}
                </div>
                <div className="text-2xs font-semibold text-ink-primary truncate">{o.client_name || "Walk-in"}</div>
                <div className="mt-1 flex items-center justify-between gap-1">
                  {pill(o.status, stOf(o), true)}
                  {o.assigned_to_name && <span className="text-2xs text-ink-tertiary truncate">{o.assigned_to_name}</span>}
                </div>
              </button>
            );
          };
          const [rFrom] = schedRange();
          const label = schedMode === "day" ? anchor.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", year: "numeric" })
            : schedMode === "month" ? anchor.toLocaleDateString([], { month: "long", year: "numeric" })
              : `${monday(anchor).toLocaleDateString([], { day: "numeric", month: "short" })} – ${(() => { const e = new Date(monday(anchor)); e.setDate(e.getDate() + 6); return e.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }); })()}`;
          const weekDays = Array.from({ length: 7 }, (_, i) => { const d = new Date(monday(anchor)); d.setDate(d.getDate() + i); return d; });
          const MODES: [typeof schedMode, string][] = [["day", "Day"], ["week", "Week"], ["month", "Month"], ["techs", "Technicians"]];
          return (
            <div>
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <Tabs look="segmented" label="Scheduler range" value={schedMode} onChange={setSchedMode} tabs={MODES.map(([m, l]) => ({ id: m, label: l }))} />
                <IconButton variant="secondary" size="sm" label="Previous" onClick={() => step(-1)} icon={<M n="chevron_left" c="text-base" />} />
                <Button size="sm" onClick={() => { const d = new Date(); d.setHours(0, 0, 0, 0); setAnchor(d); }}>Today</Button>
                <IconButton variant="secondary" size="sm" label="Next" onClick={() => step(1)} icon={<M n="chevron_right" c="text-base" />} />
                <span className="text-sm font-semibold text-ink-primary ml-1">{label}</span>
                {sched === null && <span className="sk inline-block h-3 w-16 rounded ml-2" aria-hidden />}
                <span className="ml-auto text-2xs text-ink-tertiary tabular-nums">{sched === null ? "" : `${list.length} scheduled`}</span>
              </div>

              {/* DAY */}
              {schedMode === "day" && (
                <div className="max-w-2xl space-y-1.5">
                  {sched !== null && dayItems(anchor).length === 0 && <EmptyState icon={<M n="calendar_today" c="text-lg" />} title="Nothing scheduled this day" />}
                  {dayItems(anchor).map((o) => (
                    <div key={o.id} className="flex items-stretch gap-2">
                      <div className="w-16 flex-shrink-0 text-right text-2xs tabular-nums font-semibold text-ink-tertiary pt-2">{hhmm(o.scheduled_for)}</div>
                      <div className="flex-1">{SchedCard(o, false)}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* WEEK */}
              {schedMode === "week" && (
                <div className="grid grid-cols-7 gap-2">
                  {weekDays.map((d) => {
                    const items = dayItems(d); const isToday = d.getTime() === today.getTime();
                    return (
                      <div key={d.toISOString()} className={`rounded-2xl border flex flex-col min-h-[200px] max-h-[calc(100vh-340px)] ${isToday ? "border-slate-200/70 ring-1 ring-primary/30 bg-primary/[0.03]" : "border-slate-200/70 bg-slate-50/60"}`}>
                        <div className={`px-2.5 py-2 border-b text-center border-slate-200/70`}>
                          <div className="text-2xs font-bold text-ink-tertiary">{d.toLocaleDateString([], { weekday: "short" })}</div>
                          <div className={`text-sm font-bold ${isToday ? "text-primary" : "text-ink-primary"}`}>{d.getDate()}</div>
                        </div>
                        <div className="p-1.5 space-y-1.5 overflow-y-auto">
                                                    {items.map((o) => SchedCard(o))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* MONTH */}
              {schedMode === "month" && (
                <div>
                  <div className="grid grid-cols-7 gap-1 mb-1">
                    {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((w) => <div key={w} className="text-2xs font-bold text-ink-tertiary text-center py-1">{w}</div>)}
                  </div>
                  <div className="grid grid-cols-7 gap-1">
                    {Array.from({ length: 42 }, (_, i) => { const d = new Date(rFrom); d.setDate(d.getDate() + i); return d; }).map((d) => {
                      const items = dayItems(d); const inMonth = d.getMonth() === anchor.getMonth(); const isToday = d.getTime() === today.getTime();
                      return (
                        <div key={d.toISOString()} className={`rounded-lg border p-1 min-h-[92px] ${isToday ? "border-slate-200/70 ring-1 ring-primary/30 bg-primary/[0.03]" : inMonth ? "border-slate-200/70 bg-white" : "border-slate-100 bg-slate-50/40"}`}>
                          <div className={`text-2xs font-bold mb-1 ${isToday ? "text-primary" : inMonth ? "text-ink-secondary" : "text-ink-tertiary"}`}>{d.getDate()}</div>
                          <div className="space-y-0.5">
                            {items.slice(0, 3).map((o) => {
                              const t = statusTone(o.status, stOf(o));
                              return <button key={o.id} onClick={() => openCard(o)} style={t.style} className={`w-full text-left truncate rounded px-1 py-0.5 text-2xs font-semibold ${t.cls}`}>{hhmm(o.scheduled_for)} {o.order_number || o.client_name || ""}</button>;
                            })}
                            {items.length > 3 && <div className="text-2xs text-ink-tertiary px-1">+{items.length - 3} more</div>}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* TECHS — week timeline by technician (rows) × days (cols) */}
              {schedMode === "techs" && (() => {
                const techMap = new Map<string, string>();
                list.forEach((o) => { const id = o.assigned_to_id || "__un"; techMap.set(id, o.assigned_to_name || "Unassigned"); });
                const techs = [...techMap.entries()];
                if (techs.length === 0) return sched === null ? null : <EmptyState icon={<M n="calendar_today" c="text-lg" />} title="No scheduled work orders this week" />;
                return (
                  <div className="overflow-x-auto">
                    <div className="min-w-[900px]">
                      <div className="grid" style={{ gridTemplateColumns: `160px repeat(7, 1fr)` }}>
                        <div className="px-2 py-2 text-2xs font-bold text-ink-tertiary">Technician</div>
                        {weekDays.map((d) => { const isToday = d.getTime() === today.getTime(); return <div key={d.toISOString()} className={`px-2 py-2 text-center border-l border-slate-200/60 ${isToday ? "bg-primary/[0.03]" : ""}`}><div className="text-2xs font-bold text-ink-tertiary">{d.toLocaleDateString([], { weekday: "short" })}</div><div className={`text-xs font-bold ${isToday ? "text-primary" : "text-ink-primary"}`}>{d.getDate()}</div></div>; })}
                      </div>
                      {techs.map(([tid, tname]) => (
                        <div key={tid} className="grid border-t border-slate-200/60" style={{ gridTemplateColumns: `160px repeat(7, 1fr)` }}>
                          <div className="px-2 py-2 text-xs font-bold text-ink-primary flex items-center gap-1.5"><span className="w-6 h-6 rounded-full bg-primary grid place-items-center text-white text-2xs font-bold flex-shrink-0">{(tname || "?").slice(0, 2).toUpperCase()}</span><span className="truncate">{tname}</span></div>
                          {weekDays.map((d) => {
                            const items = dayItems(d).filter((o) => (o.assigned_to_id || "__un") === tid);
                            const isToday = d.getTime() === today.getTime();
                            return <div key={d.toISOString()} className={`px-1 py-1 border-l border-slate-200/60 space-y-1 min-h-[54px] ${isToday ? "bg-primary/[0.03]" : ""}`}>{items.map((o) => SchedCard(o))}</div>;
                          })}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}
            </div>
          );
        })()}
      </PageLayout>

      {open && <WODrawer key={open.id} order={open} onClose={() => setOpen(null)} onChanged={refetch} />}
      {create && <CreateOrderDrawer isEstimate={create === "estimate"} onClose={() => setCreate(null)} onCreated={refetch} />}
      {archived !== null && (
        <Drawer label="Archived work orders" title="Archived work orders" onClose={() => setArchived(null)}>
          {archived === "loading" && <div aria-hidden className="space-y-2"><span className="sk block h-10 rounded-xl" /><span className="sk block h-10 rounded-xl" /></div>}
          {Array.isArray(archived) && archived.length === 0 && <EmptyState icon={<M n="inventory" c="text-lg" />} title="No archived work orders" />}
          {Array.isArray(archived) && archived.length > 0 && (
            <Card padded={false} className="divide-y divide-slate-100 text-xs">
              {archived.map((o) => (
                <div key={o.id} className="px-4 py-2.5 flex items-center gap-3">
                  <span className="font-medium tabular-nums text-ink-primary">{o.order_number || o.id?.slice(0, 8)}</span>
                  <span className="flex-1 text-ink-secondary truncate">{[o.client, o.archive_reason].filter(Boolean).join(" · ")}</span>
                  <span className="text-ink-tertiary capitalize">{stOf(o)?.name || (o.status || "").replace(/_/g, " ")}</span>
                  {canArchive && <Button size="sm" onClick={() => restoreOne(o.id)} icon={<M n="restore" c="text-sm" />}>Restore</Button>}
                </div>
              ))}
            </Card>
          )}
        </Drawer>
      )}
    </Shell>
  );
}
