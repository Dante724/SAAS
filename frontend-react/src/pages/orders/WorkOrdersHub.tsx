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
import { JobLabelsButton } from "./JobLabels";
import CreateOrderDrawer from "./CreateOrderDrawer";
import Pager from "../../components/Pager";
import { Gate } from "../../context/Me";

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
  { key: "order_id", label: "Order ID" }, { key: "client", label: "Client" },
  { key: "device", label: "Device / Asset" }, { key: "status", label: "Status" },
  { key: "assignee", label: "Assignee" }, { key: "scheduled", label: "Scheduled" },
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
  const label = st?.name || (status || "—").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const t = statusTone(status, st);
  return (
    <span className={`${small ? "px-1.5 py-0.5 gap-1" : "px-2.5 py-1 gap-1.5"} rounded-full text-[11px] font-semibold inline-flex items-center whitespace-nowrap max-w-full truncate ${t.cls}`} style={t.style}>
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
  const [fltOpen, setFltOpen] = useState(false);
  const [fAssignee, setFAssignee] = useState("");              // "" any · "__none" unassigned · else employee id
  const [fPriority, setFPriority] = useState("");              // "" any · "0"|"1"|"2" · "urgent" = 1 or 2
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [fScheduled, setFScheduled] = useState<"" | "yes" | "no">("");
  const [sel, setSel] = useState<Set<string>>(new Set());
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
  const [colsOpen, setColsOpen] = useState(false);
  const [viewsOpen, setViewsOpen] = useState(false);
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
    catch (e: any) { await alertDialog(e.message || "Could not save view"); }
  }
  function applyView(v: SavedView) {
    const p = v.params || {};
    if (p.q != null) setQ(p.q); setFilter(p.filter ?? null);
    // Views saved before server paging stored the assignee's name; map it to the id.
    const a = p.fAssignee ?? "";
    setFAssignee(a && a !== "__none" ? (facets?.assignees.find((x) => x.id === a || x.name === a)?.id ?? a) : a); setFPriority(p.fPriority ?? ""); setFScheduled(p.fScheduled ?? "");
    if (p.view) setView(p.view);
    if (Array.isArray(p.cols)) { const n = new Set<string>(p.cols); setCols(n); try { localStorage.setItem(COLS_KEY, JSON.stringify([...n])); } catch { /* ignore */ } }
    setViewsOpen(false);
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
    try { await api.patch(`/orders/${id}/status`, { status: key, reason: reason || undefined }); } catch { refetch(); }
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
  // Stall badges only for the rows on screen (the endpoint otherwise walks every open order).
  const flagIds = view === "table" ? (orders || []).map((o) => o.id).join(",") : "";
  useEffect(() => {
    if (!flagIds) { setFlags({}); return; }
    api.get<{ items: Record<string, { stalled: boolean; predicted_ready_at: string | null; over_by_min: number | null }> }>(`/orders/timing/flags?ids=${encodeURIComponent(flagIds)}`)
      .then((r) => setFlags(r.items || {})).catch(() => setFlags({}));
  }, [flagIds]);
  function refetch() {
    setTick((t) => t + 1);
    // Same numbers as the Home dashboard (one server-side definition of urgent/overdue).
    api.get<{ kpis?: Kpis }>("/reports/company-dashboard").then((r) => setKpis(r.kpis || {})).catch(() => setKpis(null));
  }
  useEffect(() => {
    refetch(); api.get<any>("/statuses").then((r) => setStatuses(r.items || r || [])).catch(() => {});
    api.get<any>("/statuses?record_type=estimate").then((r) => setEstStatuses(r.items || r || [])).catch(() => {});
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
  const dash = (n?: number) => (kpis && n != null ? String(n) : "—");
  const kpiStats: SummaryStat[] = [
    { label: "Open jobs", value: facets ? String(facets.open_jobs) : "—", onClick: () => { setFilter(null); setFPriority(""); }, active: !filter && !fPriority },
    { label: "Waiting to start", value: dash(kpis?.new), onClick: () => setFilter(filter === "new" ? null : "new"), active: filter === "new" },
    { label: "Urgent", value: dash(kpis?.urgent), onClick: () => setFPriority(fPriority === "urgent" ? "" : "urgent"), active: fPriority === "urgent" },
    { label: "Late", value: dash(kpis?.overdue), tone: "bad", zero: !kpis?.overdue },
    { label: "To collect", value: kpis?.receivables != null ? money(kpis.receivables) : "—" },
  ];
  const assignees = facets?.assignees || [];
  const activeFacets = (fAssignee ? 1 : 0) + (fPriority ? 1 : 0) + (fScheduled ? 1 : 0);
  const statusCounts = view === "board" ? boardCounts : (facets?.status_counts || {});
  const countFor = (key: string) => Object.entries(statusCounts).reduce((n, [k, v]) => n + (norm(k) === key ? v : 0), 0);
  const countAll = Object.values(statusCounts).reduce((n, v) => n + v, 0);
  const filtersOn = !!(filter || activeFacets || q.trim());
  const clearFilters = () => { setFilter(null); setFAssignee(""); setFPriority(""); setFScheduled(""); setQ(""); };
  const emptyState = filtersOn ? (
    <div className="text-center text-xs text-ink-tertiary space-y-3">
      <p>No work orders match these filters.</p>
      <button onClick={clearFilters} className="h-9 px-3.5 rounded-xl bg-white border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50">Clear filters</button>
    </div>
  ) : (
    <div className="text-center text-xs text-ink-tertiary space-y-3">
      <p>No work orders yet.</p>
      <Gate perm="can_create_tickets"><button onClick={() => setCreate("order")} className="h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm hover:brightness-110 inline-flex items-center gap-1.5"><M n="add" c="text-sm" />New order</button></Gate>
    </div>
  );
  // Numbered pagination — table view only (board = kanban columns, scheduler = calendar).
  const woPages = Math.max(1, Math.ceil(total / WO_PAGE_SIZE));
  const pageRows = rows;
  useEffect(() => { setWoPage(1); }, [q, filter, view, fAssignee, fPriority, fScheduled]);  // reset to page 1 when the filtered set or view changes

  function toggle(id: string) { setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }); }
  function toggleAll() { setSel((s) => s.size === rows.length && rows.length > 0 ? new Set() : new Set(rows.map((o) => o.id))); }
  async function bulkStatus(key: string) {
    if (!key || sel.size === 0) return;
    let reason: string | null = null;
    { const ds = droppedStatus(statuses, key); if (ds) { reason = await askCancelReason(ds.name, sel.size); if (reason === null) return; } }
    setBusy(true);
    // Single atomic call (was N per-row PATCHes). action=set_status.
    try { await api.post("/orders/bulk", { order_ids: [...sel], action: "set_status", value: key, reason: reason || undefined }); setSel(new Set()); refetch(); }
    catch { for (const id of sel) { await api.patch(`/orders/${id}/status`, { status: key, reason: reason || undefined }).catch(() => {}); } setSel(new Set()); refetch(); }
    finally { setBusy(false); }
  }
  // 144-G: bulk archive (soft-delete, reversible). Orders carrying unrefunded payments are
  // skipped server-side and reported back.
  async function bulkArchive() {
    if (sel.size === 0) return;
    const reason = await promptDialog(`Archive ${sel.size} work order(s)? They move to the archive and can be restored. Reason:`, { defaultValue: "Bulk delete" });
    if (reason === null) return;
    setBusy(true);
    try {
      const r = await api.post<any>("/orders/bulk", { order_ids: [...sel], action: "archive", reason, refund: true });
      setSel(new Set()); refetch();
      if (r.skipped?.length) await alertDialog(`${r.message}. Skipped: ${r.skipped.map((s: any) => s.reason).join("; ")}`);
    } catch (e: any) { await alertDialog(e.message || "Could not archive"); }
    finally { setBusy(false); }
  }
  // Archived orders overlay (backend: GET /orders/archived).
  const [archived, setArchived] = useState<any[] | null | "loading">(null);
  function openArchived() { setArchived("loading"); api.get<{ items: any[] }>("/orders/archived").then((r) => setArchived(r.items || [])).catch(() => setArchived([])); }
  async function restoreOne(id: string) {
    try { await api.post(`/orders/${id}/restore`, {}); openArchived(); refetch(); }
    catch (e: any) { await alertDialog(e.message || "Could not restore"); }
  }

  return (
    <Shell title="Work orders">
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        <PageSummary stats={kpiStats} />

        {/* Toolbar */}
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4 space-y-3.5">
          <div className="flex flex-wrap items-center gap-2">
            <Gate perm="can_create_tickets"><button onClick={() => setCreate("order")} className="h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5 transition-all"><M n="add" c="text-sm" />New order</button></Gate>
            <Gate perm="can_create_estimates"><button onClick={() => setCreate("estimate")} className="h-9 px-3 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100 border border-slate-200 bg-white flex items-center gap-1.5 whitespace-nowrap"><M n="request_quote" c="text-sm" />New estimate</button></Gate>
            <div className="relative w-full sm:w-64"><M n="search" c="absolute left-3 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" /><input value={q} onChange={(e) => setQ(e.target.value)} className="glass-input w-full rounded-xl pl-9 pr-3 py-2 text-sm" aria-label="Filter rows" placeholder="Search work orders…" /></div>
            <div className="relative">
              <button onClick={() => { setFltOpen((o) => !o); setColsOpen(false); setViewsOpen(false); }} className={`h-9 px-3 rounded-xl text-xs font-semibold flex items-center gap-1.5 border ${activeFacets ? "bg-primary/10 text-primary border-primary/30" : "bg-white text-ink-secondary hover:bg-slate-100 border-slate-200"}`}><M n="filter_list" c="text-sm" />Filter{activeFacets ? <span className="ml-0.5 px-1.5 rounded-full bg-primary text-white text-[11px] font-bold">{activeFacets}</span> : null}</button>
              {fltOpen && (
                <div className="absolute left-0 top-10 z-30 w-64 bg-white border border-slate-200 rounded-xl shadow-drawer p-3 space-y-3" onMouseLeave={() => setFltOpen(false)}>
                  <div className="flex items-center justify-between"><span className="text-[11px] font-bold text-ink-tertiary ">Filter work orders</span>{activeFacets ? <button onClick={() => { setFAssignee(""); setFPriority(""); setFScheduled(""); }} className="text-[11px] font-semibold text-primary">Clear</button> : null}</div>
                  <label className="block space-y-1"><span className="text-[11px] font-semibold text-ink-secondary">Assignee</span>
                    <select value={fAssignee} onChange={(e) => setFAssignee(e.target.value)} className="glass-input w-full rounded-xl px-3 py-2 text-sm"><option value="">Any</option><option value="__none">Unassigned</option>{assignees.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
                  {fAssignee && fAssignee !== "__none" && (
                    <JobLabelsButton pick={{ assignedToId: fAssignee }} label="Print QR labels for all their open jobs" className="text-[11px] font-semibold text-primary hover:underline text-left" />
                  )}
                  <label className="block space-y-1"><span className="text-[11px] font-semibold text-ink-secondary">Priority</span>
                    <select value={fPriority} onChange={(e) => setFPriority(e.target.value)} className="glass-input w-full rounded-xl px-3 py-2 text-sm"><option value="">Any</option><option value="0">Normal</option><option value="1">High</option><option value="2">Urgent</option><option value="urgent">High or urgent</option></select></label>
                  <label className="block space-y-1"><span className="text-[11px] font-semibold text-ink-secondary">Scheduling</span>
                    <select value={fScheduled} onChange={(e) => setFScheduled(e.target.value as "" | "yes" | "no")} className="glass-input w-full rounded-xl px-3 py-2 text-sm"><option value="">Any</option><option value="yes">Scheduled</option><option value="no">Unscheduled</option></select></label>
                </div>
              )}
            </div>
            <div className="h-9 flex items-center bg-slate-100 p-0.5 rounded-xl border border-slate-200/70 text-xs">
              <button onClick={() => setView("table")} className={`h-full px-2.5 rounded-lg font-semibold flex items-center gap-1 ${view === "table" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="table_rows" c="text-xs" />Table</button>
              <button onClick={() => setView("board")} className={`h-full px-2.5 rounded-lg font-semibold flex items-center gap-1 ${view === "board" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="view_kanban" c="text-xs" />Board</button>
              <button onClick={() => setView("scheduler")} className={`h-full px-2.5 rounded-lg font-semibold flex items-center gap-1 ${view === "scheduler" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="calendar_month" c="text-xs" />Scheduler</button>
            </div>
            <div className="relative">
              <button onClick={() => { setColsOpen((o) => !o); setViewsOpen(false); }} className="h-9 px-3 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100 border border-slate-200 bg-white flex items-center gap-1.5"><M n="view_column" c="text-sm" />Columns</button>
              {colsOpen && (
                <div className="absolute left-0 top-10 z-30 w-52 bg-white border border-slate-200 rounded-xl shadow-drawer p-2" onMouseLeave={() => setColsOpen(false)}>
                  <div className="px-2 py-1 text-[11px] font-bold text-ink-tertiary ">Visible columns</div>
                  {COLS.map((c) => (
                    <label key={c.key} className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 cursor-pointer text-xs font-medium text-ink-primary">
                      <input type="checkbox" checked={showCol(c.key)} onChange={() => toggleCol(c.key)} className="accent-primary" />{c.label}
                    </label>
                  ))}
                </div>
              )}
            </div>
            <div className="relative">
              <button onClick={() => { setViewsOpen((o) => !o); setColsOpen(false); }} className="h-9 px-3 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100 border border-slate-200 bg-white flex items-center gap-1.5"><M n="bookmark" c="text-sm" />Saved views</button>
              {viewsOpen && (
                <div className="absolute left-0 top-10 z-30 w-60 bg-white border border-slate-200 rounded-xl shadow-drawer p-2" onMouseLeave={() => setViewsOpen(false)}>
                  <button onClick={saveView} className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-blue-50/60 text-primary text-xs font-semibold"><M n="add" c="text-sm" />Save current view</button>
                  <div className="border-t border-slate-100 my-1" />
                  {views.length === 0 && <div className="px-2 py-2 text-[11px] text-ink-tertiary">No saved views yet.</div>}
                  {views.map((v) => (
                    <div key={v.id} onClick={() => applyView(v)} className="group flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 cursor-pointer text-xs font-medium text-ink-primary">
                      <M n="bookmark_added" c="text-sm text-ink-tertiary" /><span className="flex-1 truncate">{v.name}</span>
                      {v.mine && <button aria-label="Delete" onClick={(e) => deleteView(v.id, e)} className="opacity-0 group-hover:opacity-100 text-ink-tertiary hover:text-rose-600"><M n="delete" c="text-xs" /></button>}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <button onClick={openArchived} className="h-9 px-3 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100 border border-slate-200 bg-white flex items-center gap-1.5"><M n="inventory" c="text-sm" />Archived</button>
          </div>
          <div className="flex flex-wrap items-center gap-2 pt-3.5 text-xs border-t border-slate-100">
            <button onClick={() => setFilter(null)} className={!filter ? "px-3.5 py-1.5 rounded-full font-semibold bg-primary text-white text-xs shadow-clay-sm" : "px-3.5 py-1.5 rounded-full font-medium bg-slate-50 text-ink-secondary ring-1 ring-inset ring-slate-200 hover:bg-white hover:text-ink-primary hover:ring-slate-300 text-xs transition-colors"}>All ({countAll})</button>
            {statuses.map((s) => { const k = s.key || norm(s.name); const n = countFor(k); return (
              <button key={s.id} onClick={() => setFilter(k)} className={filter === k ? "px-3.5 py-1.5 rounded-full font-semibold bg-primary text-white text-xs shadow-clay-sm" : "px-3.5 py-1.5 rounded-full font-medium bg-slate-50 text-ink-secondary ring-1 ring-inset ring-slate-200 hover:bg-white hover:text-ink-primary hover:ring-slate-300 text-xs transition-colors"}>{s.name} ({n})</button>
            ); })}
          </div>
        </div>

        {/* Bulk action bar */}
        {sel.size > 0 && (
          <div className="bg-primary/95 text-white rounded-xl px-4 py-2.5 flex items-center gap-3 text-xs font-semibold shadow-clay-card">
            <span>{sel.size} selected</span>
            <div className="h-4 w-px bg-white/30" />
            <span className="opacity-90">Set status:</span>
            <select disabled={busy} onChange={(e) => { bulkStatus(e.target.value); e.target.value = ""; }} className="bg-white/15 border border-white/25 rounded-lg px-2 py-1 text-white text-xs focus:outline-none">
              <option value="">Choose…</option>
              {statuses.map((s) => <option key={s.id} value={s.key || norm(s.name)} className="text-ink-primary">{s.name}</option>)}
            </select>
            <div className="h-4 w-px bg-white/30" />
            <JobLabelsButton pick={{ orderIds: [...sel] }} label="Print QR labels" className="px-2.5 py-1 rounded-lg bg-white/15 hover:bg-white/25" />
            <Gate perm="can_delete_orders"><Gate perm="can_delete_tickets"><button disabled={busy} onClick={bulkArchive} className="px-2.5 py-1 rounded-lg bg-white/15 hover:bg-white/25 flex items-center gap-1"><M n="archive" c="text-[13px]" />Archive</button></Gate></Gate>
            <button onClick={() => setSel(new Set())} className="ml-auto px-2.5 py-1 rounded-lg bg-white/15 hover:bg-white/25">Clear</button>
          </div>
        )}

        {/* Table */}
        {view === "table" && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          {/* Phones: one card per order instead of the wide table. */}
          <ul className="sm:hidden divide-y divide-slate-100" aria-label="Work orders">
            {err && <li className="p-4 text-xs text-status-overdue-ink">Error: {err}</li>}
            {!orders && !err && <li className="p-4 text-xs text-ink-tertiary">Loading orders…</li>}
            {orders && rows.length === 0 && <li className="p-6">{emptyState}</li>}
            {pageRows.map((o) => (
              <li key={o.id}>
                <button onClick={() => setOpen(o)} className={`w-full text-left px-4 py-3 flex flex-col gap-1 hover:bg-slate-50/80 ${sel.has(o.id) ? "bg-primary/5" : ""}`}>
                  <span className="flex items-center gap-2">
                    <span className="font-mono font-bold text-primary text-xs whitespace-nowrap">{o.order_number || o.id}</span>
                    {!!(o.client_msg_unread) && <span role="img" className="w-1.5 h-1.5 rounded-full bg-primary" aria-label="Unread client message" />}
                    <span className="ml-auto">{pill(o.status, stOf(o))}</span>
                  </span>
                  <span className="flex items-baseline gap-2">
                    <span className="text-[13px] font-semibold text-ink-primary truncate">{o.client?.name || "Walk-in"}</span>
                    <span className="ml-auto tabular-nums font-bold text-ink-primary text-xs">{money(o.total)}</span>
                  </span>
                  <span className="text-[11px] text-ink-secondary truncate">{deviceName(o.device)}{o.assigned_to?.name ? ` · ${o.assigned_to.name}` : ""}</span>
                </button>
              </li>
            ))}
          </ul>
          <table className="hidden sm:table w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-slate-50/60 border-b border-slate-200/80 text-ink-tertiary text-[11px] font-semibold ">
                <th className="py-3 px-4 w-10"><input className="rounded text-primary border-slate-300" type="checkbox" aria-label="Select all work orders" checked={rows.length > 0 && sel.size === rows.length} onChange={toggleAll} /></th>
                {showCol("order_id") && <th className="py-3 px-4">Order ID</th>}
                {showCol("client") && <th className="py-3 px-4">Client</th>}
                {showCol("device") && <th className="py-3 px-4">Device / Asset</th>}
                {showCol("status") && <th className="py-3 px-4">Status</th>}
                {showCol("assignee") && <th className="py-3 px-4">Assignee</th>}
                {showCol("scheduled") && <th className="py-3 px-4">Scheduled</th>}
                {showCol("estimate") && <th className="py-3 px-4 text-right">Total</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
              {err && <tr><td colSpan={colSpan} className="py-6 px-4 text-status-overdue-ink">Error: {err}</td></tr>}
              {!orders && !err && <tr><td colSpan={colSpan} className="py-6 px-4 text-ink-tertiary">Loading orders…</td></tr>}
              {orders && rows.length === 0 && <tr><td colSpan={colSpan} className="py-10 px-4">{emptyState}</td></tr>}
              {pageRows.map((o) => (
                <tr key={o.id} onClick={() => setOpen(o)} className={`hover:bg-slate-50/80 cursor-pointer transition-colors group ${sel.has(o.id) ? "bg-primary/5" : ""}`}>
                  <td className="py-3.5 px-4" onClick={(e) => e.stopPropagation()}><input className="rounded text-primary border-slate-300" type="checkbox" aria-label={`Select ${o.order_number || o.id}`} checked={sel.has(o.id)} onChange={() => toggle(o.id)} /></td>
                  {showCol("order_id") && <td className="py-3.5 px-4 font-mono font-bold text-primary whitespace-nowrap"><span className="inline-flex items-center gap-1.5">{o.order_number || o.id}{!!(o.client_msg_unread && o.client_msg_unread > 0) && <span title={`${o.client_msg_unread} unread customer message(s)`} className="inline-flex items-center gap-0.5 text-[11px] font-bold bg-primary text-white px-1.5 py-0.5 rounded-full"><M n="forum" c="text-[11px]" />{o.client_msg_unread}</span>}{(o as any).checklists_pending && <span role="img" aria-label="Checklist incomplete" title="Checklist incomplete" className="w-2 h-2 rounded-full bg-amber-500" />}</span></td>}
                  {showCol("client") && <td className="py-3.5 px-4"><span className="font-semibold text-[13px] text-ink-primary">{o.client?.name || "Walk-in"}</span><span className="text-[11px] font-normal text-ink-tertiary block">{o.client?.phone || ""}</span></td>}
                  {showCol("device") && <td className="py-3.5 px-4 text-ink-primary font-semibold">{deviceName(o.device)}</td>}
                  {showCol("status") && <td className="py-3.5 px-4"><div className="flex items-center gap-1.5">{pill(o.status, stOf(o))}{flags[o.id]?.stalled && (
                    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[11px] font-bold bg-rose-100 text-rose-700 border border-rose-200" title={flags[o.id]?.over_by_min ? `~${Math.round(flags[o.id]!.over_by_min!)} min over typical` : "Running long"}><M n="timer" c="text-[11px]" />Stalled</span>
                  )}</div></td>}
                  {showCol("assignee") && <td className="py-3.5 px-4">{o.assigned_to?.name || "—"}</td>}
                  {showCol("scheduled") && <td className="py-3.5 px-4">{o.scheduled_for ? fmtDay(o.scheduled_for) : "—"}</td>}
                  {showCol("estimate") && <td className="py-3.5 px-4 text-right tabular-nums font-bold text-ink-primary">{money(o.total)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          {orders && rows.length > 0 && <Pager total={total} page={Math.min(woPage, woPages)} pageSize={WO_PAGE_SIZE} onPage={setWoPage} noun="work orders" />}
        </div>
        )}

        {/* Board (kanban, drag to change status) */}
        {view === "board" && (
          <div className="flex gap-3 overflow-x-auto pb-2">
            {statuses.map((s) => {
              const key = s.key || norm(s.name);
              const cards = rows.filter((o) => norm(o.status) === key);
              const n = countFor(key);   // true count; the board only carries the newest open cards
              return (
                <div key={s.id}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => { if (drag) { moveTo(drag, key); setDrag(null); } }}
                  className="w-64 flex-shrink-0 bg-slate-50/70 rounded-2xl border border-slate-200/70 flex flex-col max-h-[calc(100vh-320px)]">
                  <div className="px-3 py-2.5 flex items-center justify-between border-b border-slate-200/70">
                    {pill(key, s, true)}
                    <span className="text-[11px] font-bold text-ink-tertiary">{n}</span>
                  </div>
                  <div className="p-2 space-y-2 overflow-y-auto">
                    {cards.length === 0 && <div className="text-[11px] text-ink-tertiary px-2 py-4 text-center">Drop here</div>}
                    {cards.map((o) => (
                      <div key={o.id} draggable onDragStart={() => setDrag(o.id)} onDragEnd={() => setDrag(null)} onClick={() => setOpen(o)}
                        className={`bg-white rounded-xl border border-slate-200/80 shadow-clay-sm p-3 cursor-grab active:cursor-grabbing hover:border-primary/40 transition-colors ${drag === o.id ? "opacity-50" : ""}`}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="font-mono font-bold text-primary text-xs">{o.order_number || o.id}</span>
                          <span className="text-[11px] text-ink-tertiary">{fmtDay(o.scheduled_for)}</span>
                        </div>
                        <div className="text-xs font-bold text-ink-primary truncate">{o.client?.name || "Walk-in"}</div>
                        <div className="text-[11px] text-ink-secondary truncate">{deviceName(o.device)}</div>
                        <div className="mt-1.5 flex items-center justify-between">
                          <span className="text-[11px] text-ink-tertiary">{o.assigned_to?.name || "Unassigned"}</span>
                          <span className="tabular-nums font-bold text-ink-primary text-xs">{money(o.total)}</span>
                        </div>
                      </div>
                    ))}
                    {cards.length > 0 && cards.length < n && <div className="text-[11px] text-ink-tertiary px-2 py-1 text-center">Newest {cards.length} of {n}. The table shows them all.</div>}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {view === "scheduler" && (() => {
          const list = sched || [];
          const sameDay = (iso: string, d: Date) => { const x = new Date(iso); return x.getFullYear() === d.getFullYear() && x.getMonth() === d.getMonth() && x.getDate() === d.getDate(); };
          const hhmm = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
          const today = new Date(); today.setHours(0, 0, 0, 0);
          const step = (n: number) => setAnchor((a) => { const d = new Date(a); if (schedMode === "day") d.setDate(d.getDate() + n); else if (schedMode === "month") d.setMonth(d.getMonth() + n); else d.setDate(d.getDate() + n * 7); return d; });
          const dayItems = (d: Date) => list.filter((o) => o.scheduled_for && sameDay(o.scheduled_for, d)).sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for));
          const openCard = (o: any) => setOpen(all.find((x) => x.id === o.id) || o);
          // compact order card
          const Card = (o: any, showTime = true) => {
            return (
              <button key={o.id} onClick={() => openCard(o)} className="w-full text-left bg-white rounded-lg border border-slate-200/80 shadow-clay-sm p-2 hover:border-primary/40 transition-colors">
                <div className="flex items-center justify-between gap-1">
                  <span className="font-mono font-bold text-primary text-[11px] truncate">{o.order_number || o.id?.slice(0, 6)}</span>
                  {showTime && <span className="text-[11px] font-semibold text-ink-tertiary flex-shrink-0">{hhmm(o.scheduled_for)}</span>}
                </div>
                <div className="text-[11px] font-bold text-ink-primary truncate">{o.client_name || "Walk-in"}</div>
                <div className="mt-1 flex items-center justify-between gap-1">
                  {pill(o.status, stOf(o), true)}
                  {o.assigned_to_name && <span className="text-[11px] text-ink-tertiary truncate">{o.assigned_to_name}</span>}
                </div>
              </button>
            );
          };
          const [rFrom] = schedRange();
          const label = schedMode === "day" ? anchor.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", year: "numeric" })
            : schedMode === "month" ? anchor.toLocaleDateString([], { month: "long", year: "numeric" })
              : `${monday(anchor).toLocaleDateString([], { day: "numeric", month: "short" })} – ${(() => { const e = new Date(monday(anchor)); e.setDate(e.getDate() + 6); return e.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }); })()}`;
          const weekDays = Array.from({ length: 7 }, (_, i) => { const d = new Date(monday(anchor)); d.setDate(d.getDate() + i); return d; });
          const MODES: [typeof schedMode, string][] = [["day", "Day"], ["week", "Week"], ["month", "Month"], ["techs", "Techs"]];
          return (
            <div>
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <div className="flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200/70 text-xs">
                  {MODES.map(([m, l]) => <button key={m} onClick={() => setSchedMode(m)} className={`px-2.5 py-1 rounded-md font-semibold ${schedMode === m ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}>{l}</button>)}
                </div>
                <button aria-label="Previous" onClick={() => step(-1)} className="w-8 h-8 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 flex items-center justify-center text-ink-secondary"><M n="chevron_left" c="text-base" /></button>
                <button onClick={() => { const d = new Date(); d.setHours(0, 0, 0, 0); setAnchor(d); }} className="px-3 h-8 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-xs font-semibold text-ink-secondary">Today</button>
                <button aria-label="Next" onClick={() => step(1)} className="w-8 h-8 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 flex items-center justify-center text-ink-secondary"><M n="chevron_right" c="text-base" /></button>
                <span className="text-sm font-bold text-ink-primary ml-1">{label}</span>
                {sched === null && <span className="text-xs text-ink-tertiary ml-2">Loading…</span>}
                <span className="ml-auto text-[11px] text-ink-tertiary">{list.length} scheduled</span>
              </div>

              {/* DAY */}
              {schedMode === "day" && (
                <div className="max-w-2xl space-y-1.5">
                  {dayItems(anchor).length === 0 && <div className="text-xs text-ink-tertiary py-8 text-center">Nothing scheduled this day.</div>}
                  {dayItems(anchor).map((o) => (
                    <div key={o.id} className="flex items-stretch gap-2">
                      <div className="w-16 flex-shrink-0 text-right text-[11px] tabular-nums font-semibold text-ink-tertiary pt-2">{hhmm(o.scheduled_for)}</div>
                      <div className="flex-1">{Card(o, false)}</div>
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
                      <div key={d.toISOString()} className={`rounded-2xl border flex flex-col min-h-[200px] max-h-[calc(100vh-340px)] ${isToday ? "border-primary/40 bg-primary/5" : "border-slate-200/70 bg-slate-50/60"}`}>
                        <div className={`px-2.5 py-2 border-b text-center ${isToday ? "border-primary/30" : "border-slate-200/70"}`}>
                          <div className="text-[11px] font-bold text-ink-tertiary">{d.toLocaleDateString([], { weekday: "short" })}</div>
                          <div className={`text-sm font-bold ${isToday ? "text-primary" : "text-ink-primary"}`}>{d.getDate()}</div>
                        </div>
                        <div className="p-1.5 space-y-1.5 overflow-y-auto">
                          {items.length === 0 && <div className="text-[11px] text-ink-tertiary text-center py-3">—</div>}
                          {items.map((o) => Card(o))}
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
                    {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((w) => <div key={w} className="text-[11px] font-bold text-ink-tertiary text-center py-1">{w}</div>)}
                  </div>
                  <div className="grid grid-cols-7 gap-1">
                    {Array.from({ length: 42 }, (_, i) => { const d = new Date(rFrom); d.setDate(d.getDate() + i); return d; }).map((d) => {
                      const items = dayItems(d); const inMonth = d.getMonth() === anchor.getMonth(); const isToday = d.getTime() === today.getTime();
                      return (
                        <div key={d.toISOString()} className={`rounded-lg border p-1 min-h-[92px] ${isToday ? "border-primary/40 bg-primary/5" : inMonth ? "border-slate-200/70 bg-white" : "border-slate-100 bg-slate-50/40"}`}>
                          <div className={`text-[11px] font-bold mb-1 ${isToday ? "text-primary" : inMonth ? "text-ink-secondary" : "text-ink-tertiary"}`}>{d.getDate()}</div>
                          <div className="space-y-0.5">
                            {items.slice(0, 3).map((o) => {
                              const t = statusTone(o.status, stOf(o));
                              return <button key={o.id} onClick={() => openCard(o)} style={t.style} className={`w-full text-left truncate rounded px-1 py-0.5 text-[11px] font-semibold ${t.cls}`}>{hhmm(o.scheduled_for)} {o.order_number || o.client_name || ""}</button>;
                            })}
                            {items.length > 3 && <div className="text-[11px] text-ink-tertiary px-1">+{items.length - 3} more</div>}
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
                if (techs.length === 0) return <div className="text-xs text-ink-tertiary py-8 text-center">No scheduled work orders this week.</div>;
                return (
                  <div className="overflow-x-auto">
                    <div className="min-w-[900px]">
                      <div className="grid" style={{ gridTemplateColumns: `160px repeat(7, 1fr)` }}>
                        <div className="px-2 py-2 text-[11px] font-bold text-ink-tertiary">Technician</div>
                        {weekDays.map((d) => { const isToday = d.getTime() === today.getTime(); return <div key={d.toISOString()} className={`px-2 py-2 text-center border-l border-slate-200/60 ${isToday ? "bg-primary/5" : ""}`}><div className="text-[11px] font-bold text-ink-tertiary">{d.toLocaleDateString([], { weekday: "short" })}</div><div className={`text-xs font-bold ${isToday ? "text-primary" : "text-ink-primary"}`}>{d.getDate()}</div></div>; })}
                      </div>
                      {techs.map(([tid, tname]) => (
                        <div key={tid} className="grid border-t border-slate-200/60" style={{ gridTemplateColumns: `160px repeat(7, 1fr)` }}>
                          <div className="px-2 py-2 text-xs font-bold text-ink-primary flex items-center gap-1.5"><span className="w-6 h-6 rounded-full bg-primary grid place-items-center text-white text-[11px] font-bold flex-shrink-0">{(tname || "?").slice(0, 2).toUpperCase()}</span><span className="truncate">{tname}</span></div>
                          {weekDays.map((d) => {
                            const items = dayItems(d).filter((o) => (o.assigned_to_id || "__un") === tid);
                            const isToday = d.getTime() === today.getTime();
                            return <div key={d.toISOString()} className={`px-1 py-1 border-l border-slate-200/60 space-y-1 min-h-[54px] ${isToday ? "bg-primary/5" : ""}`}>{items.map((o) => Card(o))}</div>;
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
      </div>

      {open && <WODrawer order={open} onClose={() => setOpen(null)} onChanged={refetch} />}
      {create && <CreateOrderDrawer isEstimate={create === "estimate"} onClose={() => setCreate(null)} onCreated={refetch} />}
      {archived !== null && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
          <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={() => setArchived(null)} />
          <div className="relative h-full w-full max-w-[560px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
            <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
            <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
              <h3 className="text-base font-bold text-ink-primary">Archived work orders</h3>
              <button aria-label="Close" onClick={() => setArchived(null)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 text-xs">
              {archived === "loading" && <p className="text-ink-tertiary p-4">Loading…</p>}
              {Array.isArray(archived) && archived.length === 0 && <p className="text-ink-tertiary p-4">No archived work orders.</p>}
              {Array.isArray(archived) && archived.length > 0 && (
                <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden divide-y divide-slate-100">
                  {archived.map((o) => (
                    <div key={o.id} className="px-4 py-2.5 flex items-center gap-3">
                      <span className="font-bold text-ink-primary font-mono">{o.order_number || o.id?.slice(0, 8)}</span>
                      <span className="flex-1 text-ink-secondary truncate">{o.client || "—"}{o.archive_reason ? ` · ${o.archive_reason}` : ""}</span>
                      <span className="text-ink-tertiary capitalize">{stOf(o)?.name || (o.status || "").replace(/_/g, " ")}</span>
                      <button onClick={() => restoreOne(o.id)} className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold text-primary border border-primary/30 hover:bg-primary/10"><M n="restore" c="text-[13px]" />Restore</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </Shell>
  );
}
