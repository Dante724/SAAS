import { askCancelReason, droppedStatus } from "../../lib/cancelReason";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { alertDialog, promptDialog } from "../../lib/dialog";
import { Order, deviceName, money } from "./types";
import { Avatar, deviceIcon, dueText, friendlyDate } from "./look";
import WODrawer from "./WODrawer";
import CreateOrderDrawer from "./CreateOrderDrawer";
import Pager from "../../components/Pager";
import { Gate } from "../../context/Me";

type Kpis = { new?: number; urgent?: number; overdue?: number; receivables?: number };
type Status = { id: string; name?: string; key?: string; status_group?: string; color?: string; sort_order?: number };
type SavedView = { id: string; name: string; params: any; is_shared?: boolean; mine?: boolean };

// Toggleable table columns (checkbox + action columns are always shown)
const COLS: { key: string; label: string }[] = [
  { key: "order_id", label: "Job" }, { key: "client", label: "Client" },
  { key: "device", label: "Item" }, { key: "status", label: "Status" },
  { key: "assignee", label: "Assignee" }, { key: "scheduled", label: "Booked for" },
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
// The shop's own status colours (Settings › Statuses); a soft tint of it behind the name.
function StatusPill({ st, raw }: { st?: Status; raw?: string }) {
  const c = st?.color || "#94a3b8";
  const label = st?.name || (raw || "—").replace(/_/g, " ").replace(/\b\w/g, (x) => x.toUpperCase());
  return (
    <span className="px-2.5 py-1 rounded-full text-[11px] font-semibold inline-flex items-center gap-1.5 text-ink-primary whitespace-nowrap"
      style={{ background: `${c}1f`, boxShadow: `inset 0 0 0 1px ${c}40` }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: c }} />{label}
    </span>
  );
}

export default function WorkOrdersHub() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [flags, setFlags] = useState<Record<string, { stalled: boolean; predicted_ready_at: string | null; over_by_min: number | null }>>({});
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<Order | null>(null);
  const [create, setCreate] = useState<null | "order" | "estimate">(null);
  const [statuses, setStatuses] = useState<Status[]>([]);
  const [estStatuses, setEstStatuses] = useState<Status[]>([]);
  const [fOverdue, setFOverdue] = useState(false);
  const [filter, setFilter] = useState<string | null>(null);   // status key
  // Multi-facet filter panel (client-side over the loaded set, same as status + text).
  const [fltOpen, setFltOpen] = useState(false);
  const [fAssignee, setFAssignee] = useState("");              // "" any · "__none" unassigned · else name
  const [fPriority, setFPriority] = useState("");              // "" any · "0"|"1"|"2" · "urgent" = 1 or 2
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [fScheduled, setFScheduled] = useState<"" | "yes" | "no">("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
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
    try { await api.post("/orders/filters/saved", { name, params: { q, filter, view, cols: [...cols], fAssignee, fPriority, fScheduled, fOverdue }, is_shared: false }); loadViews(); }
    catch (e: any) { await alertDialog(e.message || "Could not save view"); }
  }
  function applyView(v: SavedView) {
    const p = v.params || {};
    if (p.q != null) setQ(p.q); setFilter(p.filter ?? null);
    setFAssignee(p.fAssignee ?? ""); setFPriority(p.fPriority ?? ""); setFScheduled(p.fScheduled ?? ""); setFOverdue(!!p.fOverdue);
    if (p.view) setView(p.view);
    if (Array.isArray(p.cols)) { const n = new Set<string>(p.cols); setCols(n); try { localStorage.setItem(COLS_KEY, JSON.stringify([...n])); } catch { /* ignore */ } }
    setViewsOpen(false);
  }
  async function deleteView(id: string, e: any) {
    e.stopPropagation();
    try { await api.del(`/orders/filters/saved/${id}`); loadViews(); } catch { /* ignore */ }
  }
  const colSpan = 2 + [...cols].length;
  async function moveTo(id: string, key: string) {
    const o = (orders || []).find((x) => x.id === id); if (!o || norm(o.status) === key) return;
    let reason: string | null = null;
    { const ds = droppedStatus(statuses, key); if (ds) { reason = await askCancelReason(ds.name); if (reason === null) return; } }
    setOrders((cur) => (cur || []).map((x) => x.id === id ? { ...x, status: key } : x)); // optimistic
    try { await api.patch(`/orders/${id}/status`, { status: key, reason: reason || undefined }); } catch { refetch(); }
  }

  function refetch() {
    api.get<{ items: Order[] }>("/orders?limit=1000").then((r) => setOrders(r.items || [])).catch((e) => setErr(String(e.message || e)));
    api.get<{ items: Record<string, { stalled: boolean; predicted_ready_at: string | null; over_by_min: number | null }> }>("/orders/timing/flags")
      .then((r) => setFlags(r.items || {})).catch(() => setFlags({}));
    // Same numbers as the Home dashboard (one server-side definition of urgent/overdue).
    api.get<{ kpis?: Kpis }>("/reports/company-dashboard").then((r) => setKpis(r.kpis || {})).catch(() => setKpis(null));
  }
  useEffect(() => {
    refetch(); api.get<any>("/statuses").then((r) => setStatuses(r.items || r || [])).catch(() => {});
    api.get<any>("/statuses?record_type=estimate").then((r) => setEstStatuses(r.items || r || [])).catch(() => {});
  }, []);
  // Deep-open from a scan (?open=<id>): open the WO drawer straight away, then clear the param.
  const [sp, setSp] = useSearchParams();
  useEffect(() => {
    const id = sp.get("open");
    if (id) { setOpen({ id } as any); setSp((p) => { p.delete("open"); return p; }, { replace: true }); }
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
  const closedKeys = new Set(statuses.filter((st) => ["closed", "dropped"].includes(st.status_group || "")).map((st) => norm(st.key || st.name)));
  const isOpenJob = (o: Order) => !closedKeys.has(norm(o.status));
  function isLate(o: Order) { return !!o.due_at && isOpenJob(o) && new Date(o.due_at).getTime() < Date.now(); }
  const stOf = (o: Order) => (o.is_estimate ? estStatuses : statuses).find((st) => norm(st.key || st.name) === norm(o.status))
    || [...statuses, ...estStatuses].find((st) => norm(st.key || st.name) === norm(o.status));
  const rows = all.filter((o) => (!filter || norm(o.status) === filter) &&
    (!q || [o.order_number, o.client?.name, deviceName(o.device)].some((v) => (v || "").toLowerCase().includes(q.toLowerCase()))) &&
    (!fAssignee || (fAssignee === "__none" ? !o.assigned_to?.name : o.assigned_to?.name === fAssignee)) &&
    (!fPriority || (fPriority === "urgent" ? (o.priority ?? 0) >= 1 : String(o.priority ?? 0) === fPriority)) &&
    (!fScheduled || (fScheduled === "yes" ? !!o.scheduled_for : !o.scheduled_for)) &&
    (!fOverdue || isLate(o)));
  const incomplete = all.filter((o) => !o.is_estimate && !closedKeys.has(norm(o.status))).length;
  const dash = (n?: number) => (kpis && n != null ? String(n) : "—");
  const assignees = [...new Set(all.map((o) => o.assigned_to?.name).filter(Boolean) as string[])].sort();
  const activeFacets = (fAssignee ? 1 : 0) + (fPriority ? 1 : 0) + (fScheduled ? 1 : 0) + (fOverdue ? 1 : 0);
  const countFor = (key: string) => all.filter((o) => norm(o.status) === key).length;
  // Numbered pagination — table view only (board = kanban columns, scheduler = calendar).
  const WO_PAGE_SIZE = 50;
  const woPages = Math.max(1, Math.ceil(rows.length / WO_PAGE_SIZE));
  const pageRows = rows.slice((Math.min(woPage, woPages) - 1) * WO_PAGE_SIZE, Math.min(woPage, woPages) * WO_PAGE_SIZE);
  useEffect(() => { setWoPage(1); }, [q, filter, view, fAssignee, fPriority, fScheduled, fOverdue]);  // reset to page 1 when the filtered set or view changes

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
        {/* Workshop now: open jobs, the four numbers that need you, and a status bar to filter by. */}
        {(() => {
          const openJobs = all.filter((o) => !o.is_estimate && isOpenJob(o));
          const flow = statuses.filter((st) => !["closed", "dropped"].includes(st.status_group || ""))
            .map((st) => ({ st, k: norm(st.key || st.name), n: countFor(norm(st.key || st.name)) })).filter((x) => x.n > 0);
          const total = flow.reduce((a, x) => a + x.n, 0);
          const dueToday = openJobs.filter((o) => { const d = dueText(o.due_at, true); return d && !d[1] && d[0] === "Due today"; }).length;
          const late = all.filter(isLate).length;
          const stats: { l: string; v: string; icon: string; tone: string; on: boolean; go?: () => void }[] = [
            { l: "New", v: dash(kpis?.new), icon: "auto_awesome", tone: "text-indigo-600 bg-indigo-50", on: filter === "new", go: () => setFilter(filter === "new" ? null : "new") },
            { l: "Urgent", v: dash(kpis?.urgent), icon: "bolt", tone: "text-amber-600 bg-amber-50", on: fPriority === "urgent", go: () => setFPriority(fPriority === "urgent" ? "" : "urgent") },
            { l: "Late", v: kpis ? dash(kpis?.overdue) : String(late), icon: "schedule", tone: (kpis?.overdue || late) ? "text-rose-600 bg-rose-50" : "text-slate-500 bg-slate-100", on: fOverdue, go: () => setFOverdue(!fOverdue) },
            { l: "To collect", v: kpis?.receivables != null ? money(kpis.receivables) : "—", icon: "payments", tone: "text-emerald-600 bg-emerald-50", on: false },
          ];
          return (
            <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-5">
              <div className="flex flex-wrap items-end gap-x-8 gap-y-4">
                <div>
                  <div className="text-xs font-medium text-ink-tertiary">Open jobs</div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-4xl font-semibold tracking-tight text-ink-primary tabular-nums">{orders ? incomplete : "—"}</span>
                    <span className="text-xs text-ink-tertiary">{dueToday ? `${dueToday} due today` : "in the workshop"}{late ? <> · <span className="text-rose-600 font-medium">{late} late</span></> : null}</span>
                  </div>
                </div>
                <div className="ml-auto grid grid-cols-2 sm:grid-cols-4 gap-2 w-full sm:w-auto">
                  {stats.map((k) => (
                    <button key={k.l} type="button" disabled={!k.go} onClick={k.go} aria-pressed={k.go ? k.on : undefined}
                      className={`min-w-[120px] text-left rounded-xl px-3 py-2.5 border transition-colors disabled:cursor-default ${k.on ? "border-primary/40 bg-primary/5 ring-2 ring-primary/10" : "border-slate-200/80 hover:border-slate-300 enabled:hover:bg-slate-50"}`}>
                      <span className="flex items-center gap-2">
                        <span className={`w-7 h-7 rounded-lg grid place-items-center ${k.tone}`}><M n={k.icon} c="text-[15px]" /></span>
                        <span className="text-[11px] font-medium text-ink-tertiary">{k.l}</span>
                      </span>
                      <span className="block mt-1 text-lg font-semibold text-ink-primary tabular-nums">{k.v}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-5">
                <div className="flex h-3 rounded-full overflow-hidden bg-slate-100 gap-0.5">
                  {flow.map(({ st, k, n }) => (
                    <button key={k} type="button" onClick={() => setFilter(filter === k ? null : k)} title={`${st.name}: ${n}`} aria-label={`Show ${st.name} (${n})`}
                      className={`h-full transition-opacity ${filter && filter !== k ? "opacity-25" : ""}`} style={{ width: `${(n / Math.max(1, total)) * 100}%`, background: st.color || "#94a3b8" }} />
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-1.5 mt-3">
                  <button onClick={() => setFilter(null)} aria-pressed={!filter}
                    className={`px-3 py-1 rounded-full text-[11px] font-semibold transition-colors ${!filter ? "bg-ink-primary text-white" : "text-ink-secondary hover:bg-slate-100"}`}>All {all.length}</button>
                  {statuses.map((st) => { const k = st.key || norm(st.name); const n = countFor(k); const on = filter === k; return (
                    <button key={st.id} onClick={() => setFilter(on ? null : k)} aria-pressed={on}
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors ${on ? "text-ink-primary" : n ? "text-ink-secondary hover:bg-slate-100" : "text-ink-tertiary/70 hover:bg-slate-50"}`}
                      style={on ? { background: `${st.color || "#94a3b8"}22`, boxShadow: `inset 0 0 0 1px ${st.color || "#94a3b8"}55` } : undefined}>
                      <span className="w-2 h-2 rounded-full" style={{ background: st.color || "#94a3b8", opacity: n || on ? 1 : 0.4 }} />{st.name}<span className="tabular-nums text-ink-tertiary">{n}</span>
                    </button>
                  ); })}
                </div>
              </div>
            </section>
          );
        })()}

        {/* Toolbar */}
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Gate perm="can_create_tickets"><button onClick={() => setCreate("order")} className="px-4 py-2 rounded-xl bg-gradient-to-r from-primary to-primary-container text-white text-xs font-semibold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5 transition-all"><M n="add" c="text-sm font-bold" />Order</button></Gate>
              <Gate perm="can_create_estimates"><button onClick={() => setCreate("estimate")} className="px-3.5 py-2 rounded-xl bg-slate-100 hover:bg-slate-200/80 text-ink-secondary hover:text-ink-primary text-xs font-semibold border border-slate-200/70 transition-all flex items-center gap-1.5"><M n="request_quote" c="text-sm" />+ Estimate</button></Gate>
              <div className="h-5 w-[1px] bg-slate-200 mx-1" />
              <div className="relative">
                <button onClick={() => { setFltOpen((o) => !o); setColsOpen(false); setViewsOpen(false); }} className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1 border ${activeFacets ? "bg-primary/10 text-primary border-primary/30" : "text-ink-secondary hover:bg-slate-100 border-slate-200"}`}><M n="filter_list" c="text-xs" />Filter{activeFacets ? <span className="ml-0.5 px-1.5 rounded-full bg-primary text-white text-[10px] font-bold">{activeFacets}</span> : null}</button>
                {fltOpen && (
                  <div className="absolute left-0 top-9 z-30 w-64 bg-white border border-slate-200 rounded-xl shadow-drawer p-3 space-y-3" onMouseLeave={() => setFltOpen(false)}>
                    <div className="flex items-center justify-between"><span className="text-[10px] font-bold text-ink-tertiary uppercase tracking-wider">Filter work orders</span>{activeFacets ? <button onClick={() => { setFAssignee(""); setFPriority(""); setFScheduled(""); setFOverdue(false); }} className="text-[11px] font-semibold text-primary">Clear</button> : null}</div>
                    <label className="block space-y-1"><span className="text-[11px] font-semibold text-ink-secondary">Assignee</span>
                      <select value={fAssignee} onChange={(e) => setFAssignee(e.target.value)} className="w-full text-xs px-2 py-1.5 rounded-lg border border-slate-200 bg-white font-medium"><option value="">Any</option><option value="__none">Unassigned</option>{assignees.map((a) => <option key={a} value={a}>{a}</option>)}</select></label>
                    <label className="block space-y-1"><span className="text-[11px] font-semibold text-ink-secondary">Priority</span>
                      <select value={fPriority} onChange={(e) => setFPriority(e.target.value)} className="w-full text-xs px-2 py-1.5 rounded-lg border border-slate-200 bg-white font-medium"><option value="">Any</option><option value="0">Normal</option><option value="1">High</option><option value="2">Urgent</option><option value="urgent">High or urgent</option></select></label>
                    <label className="block space-y-1"><span className="text-[11px] font-semibold text-ink-secondary">Scheduling</span>
                      <select value={fScheduled} onChange={(e) => setFScheduled(e.target.value as "" | "yes" | "no")} className="w-full text-xs px-2 py-1.5 rounded-lg border border-slate-200 bg-white font-medium"><option value="">Any</option><option value="yes">Scheduled</option><option value="no">Unscheduled</option></select></label>
                    <label className="flex items-center gap-2 text-[11px] font-semibold text-ink-secondary cursor-pointer"><input type="checkbox" checked={fOverdue} onChange={(e) => setFOverdue(e.target.checked)} className="accent-primary" />Only late jobs</label>
                  </div>
                )}
              </div>
              <button onClick={openArchived} className="px-3 py-1.5 rounded-lg text-xs font-medium text-ink-secondary hover:bg-slate-100 flex items-center gap-1 border border-slate-200"><M n="inventory" c="text-xs" />Archived</button>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200/70 text-xs">
                <button onClick={() => setView("table")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1 ${view === "table" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="table_rows" c="text-xs" />Table</button>
                <button onClick={() => setView("board")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1 ${view === "board" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="view_kanban" c="text-xs" />Board</button>
                <button onClick={() => setView("scheduler")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1 ${view === "scheduler" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="calendar_month" c="text-xs" />Scheduler</button>
              </div>
              <div className="relative"><M n="search" c="absolute left-2.5 top-2 text-ink-tertiary text-sm" /><input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find a job" className="w-56 text-xs pl-8 pr-3 py-2 rounded-xl border border-slate-200 bg-slate-50 focus:bg-white focus:border-primary/50 focus:outline-none" placeholder="Find a job, customer or device" /></div>
              <div className="relative">
                <button onClick={() => { setColsOpen((o) => !o); setViewsOpen(false); }} className="px-3 py-1.5 rounded-lg text-xs font-medium text-ink-secondary hover:bg-slate-100 border border-slate-200 flex items-center gap-1"><M n="view_column" c="text-xs" />Columns</button>
                {colsOpen && (
                  <div className="absolute right-0 top-9 z-30 w-52 bg-white border border-slate-200 rounded-xl shadow-drawer p-2" onMouseLeave={() => setColsOpen(false)}>
                    <div className="px-2 py-1 text-[10px] font-bold text-ink-tertiary uppercase tracking-wider">Visible columns</div>
                    {COLS.map((c) => (
                      <label key={c.key} className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 cursor-pointer text-xs font-medium text-ink-primary">
                        <input type="checkbox" checked={showCol(c.key)} onChange={() => toggleCol(c.key)} className="accent-primary" />{c.label}
                      </label>
                    ))}
                  </div>
                )}
              </div>
              <div className="relative">
                <button onClick={() => { setViewsOpen((o) => !o); setColsOpen(false); }} className="px-3 py-1.5 rounded-lg text-xs font-medium text-ink-secondary hover:bg-slate-100 border border-slate-200 flex items-center gap-1"><M n="bookmark" c="text-xs" />Saved views</button>
                {viewsOpen && (
                  <div className="absolute right-0 top-9 z-30 w-60 bg-white border border-slate-200 rounded-xl shadow-drawer p-2" onMouseLeave={() => setViewsOpen(false)}>
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
            </div>
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
            <button disabled={busy} onClick={bulkArchive} className="px-2.5 py-1 rounded-lg bg-white/15 hover:bg-white/25 flex items-center gap-1"><M n="archive" c="text-[13px]" />Archive</button>
            <button onClick={() => setSel(new Set())} className="ml-auto px-2.5 py-1 rounded-lg bg-white/15 hover:bg-white/25">Clear</button>
          </div>
        )}

        {/* Table */}
        {view === "table" && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="border-b border-slate-100 text-ink-tertiary text-[11px] font-medium">
                <th className="py-3 px-4 w-10"><input className="rounded text-primary border-slate-300" type="checkbox" checked={rows.length > 0 && sel.size === rows.length} onChange={toggleAll} /></th>
                {showCol("order_id") && <th className="py-3 px-4">Job</th>}
                {showCol("client") && <th className="py-3 px-4">Client</th>}
                {showCol("device") && <th className="py-3 px-4">Item</th>}
                {showCol("status") && <th className="py-3 px-4">Status</th>}
                {showCol("assignee") && <th className="py-3 px-4">Assignee</th>}
                {showCol("scheduled") && <th className="py-3 px-4">Booked for</th>}
                {showCol("estimate") && <th className="py-3 px-4 text-right">Total</th>}
                <th className="py-3 px-4 w-12"><span className="sr-only">Open</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
              {err && <tr><td colSpan={colSpan} className="py-6 px-4 text-status-overdue-ink">Error: {err}</td></tr>}
              {!orders && !err && <tr><td colSpan={colSpan} className="py-6 px-4 text-ink-tertiary">Loading orders…</td></tr>}
              {orders && rows.length === 0 && <tr><td colSpan={colSpan} className="py-6 px-4 text-ink-tertiary">No work orders match.</td></tr>}
              {pageRows.map((o) => (
                <tr key={o.id} onClick={() => setOpen(o)} className={`hover:bg-slate-50/80 cursor-pointer transition-colors group ${sel.has(o.id) ? "bg-primary/5" : ""}`}>
                  <td className="py-3.5 px-4 relative" onClick={(e) => e.stopPropagation()}>{(o.priority ?? 0) >= 1 && <span title={(o.priority ?? 0) >= 2 ? "Urgent" : "High priority"} className={`absolute left-0 top-2 bottom-2 w-1 rounded-r-full ${(o.priority ?? 0) >= 2 ? "bg-rose-500" : "bg-amber-400"}`} />}<input className="rounded text-primary border-slate-300" type="checkbox" checked={sel.has(o.id)} onChange={() => toggle(o.id)} /></td>
                  {showCol("order_id") && <td className="py-3.5 px-4 font-mono text-[11px] font-semibold text-ink-secondary group-hover:text-primary"><span className="inline-flex items-center gap-1.5">{o.order_number || o.id}{!!(o.client_msg_unread && o.client_msg_unread > 0) && <span title={`${o.client_msg_unread} unread customer message(s)`} className="inline-flex items-center gap-0.5 text-[9px] font-bold bg-primary text-white px-1.5 py-0.5 rounded-full"><M n="forum" c="text-[10px]" />{o.client_msg_unread}</span>}{(o as any).checklists_pending && <span role="img" aria-label="Checklist incomplete" title="Checklist incomplete" className="w-2 h-2 rounded-full bg-amber-500" />}</span></td>}
                  {showCol("client") && <td className="py-3.5 px-4"><span className="flex items-center gap-2.5 min-w-0"><Avatar name={o.client?.name || "Walk-in"} size={30} /><span className="min-w-0"><span className="block text-[13px] font-semibold text-ink-primary truncate">{o.client?.name || "Walk-in"}</span><span className="block text-[11px] font-normal text-ink-tertiary truncate">{o.client?.phone || ""}</span></span></span></td>}
                  {showCol("device") && <td className="py-3.5 px-4">{o.device ? <span className="flex items-center gap-2 text-[13px] text-ink-primary"><span className="w-7 h-7 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n={deviceIcon(o.device)} c="text-[15px]" /></span><span className="truncate">{deviceName(o.device)}</span></span> : <span className="text-ink-tertiary">{o.is_estimate ? "Estimate" : "—"}</span>}</td>}
                  {showCol("status") && <td className="py-3 px-4"><div className="flex items-center gap-1.5"><StatusPill st={stOf(o)} raw={o.status} />{flags[o.id]?.stalled && (
                    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-rose-100 text-rose-700 border border-rose-200" title={flags[o.id]?.over_by_min ? `~${Math.round(flags[o.id]!.over_by_min!)} min over typical` : "Running long"}><M n="schedule" c="text-[11px]" />Stalled</span>
                  )}</div></td>}
                  {showCol("assignee") && <td className="py-3.5 px-4">{o.assigned_to?.name ? <span className="flex items-center gap-2"><Avatar name={o.assigned_to.name} size={24} /><span className="truncate">{o.assigned_to.name}</span></span> : <span className="text-ink-tertiary">Unassigned</span>}</td>}
                  {showCol("scheduled") && <td className="py-3.5 px-4">{(() => { const due = dueText(o.due_at, isOpenJob(o)); return (<>
                    <span className="block text-ink-primary">{friendlyDate(o.scheduled_for) || <span className="text-ink-tertiary">Not booked</span>}</span>
                    {due && <span className={`block text-[11px] ${due[1] ? "text-rose-600 font-semibold" : "text-ink-tertiary"}`}>{due[0]}</span>}
                  </>); })()}</td>}
                  {showCol("estimate") && <td className="py-3.5 px-4 text-right text-[13px] font-semibold text-ink-primary tabular-nums">{money(o.total)}</td>}
                  <td className="py-3.5 px-4 text-center"><span className="inline-grid place-items-center w-7 h-7 rounded-lg text-ink-tertiary group-hover:text-primary group-hover:bg-primary/10 transition-colors"><M n="chevron_right" c="text-sm" /></span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {orders && rows.length > 0 && <Pager total={rows.length} page={Math.min(woPage, woPages)} pageSize={WO_PAGE_SIZE} onPage={setWoPage} noun="work orders" />}
        </div>
        )}

        {/* Board (kanban, drag to change status) */}
        {view === "board" && (
          <div className="flex gap-3 overflow-x-auto pb-2">
            {statuses.map((s) => {
              const key = s.key || norm(s.name);
              const cards = rows.filter((o) => norm(o.status) === key);
              const c = s.color || "#94a3b8";
              return (
                <div key={s.id}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => { if (drag) { moveTo(drag, key); setDrag(null); } }}
                  className="w-64 flex-shrink-0 bg-slate-50/70 rounded-2xl border border-slate-200/70 flex flex-col max-h-[calc(100vh-320px)]">
                  <div className="px-3 py-2.5 flex items-center justify-between border-b border-slate-200/70">
                    <span className="inline-flex items-center gap-2 text-xs font-semibold text-ink-primary"><span className="w-2.5 h-2.5 rounded-full" style={{ background: c, boxShadow: `0 0 0 3px ${c}26` }} />{s.name}</span>
                    <span className="min-w-[22px] text-center px-1.5 py-0.5 rounded-full bg-white border border-slate-200 text-[11px] font-semibold text-ink-secondary tabular-nums">{cards.length}</span>
                  </div>
                  <div className="p-2 space-y-2 overflow-y-auto">
                    {cards.length === 0 && <div className="text-[11px] text-ink-tertiary px-2 py-4 text-center">Drop here</div>}
                    {cards.map((o) => (
                      <div key={o.id} draggable onDragStart={() => setDrag(o.id)} onDragEnd={() => setDrag(null)} onClick={() => setOpen(o)}
                        style={{ borderTopColor: c }}
                        className={`bg-white rounded-xl border border-slate-200/80 border-t-2 shadow-clay-sm p-3 cursor-grab active:cursor-grabbing hover:shadow-clay-card hover:-translate-y-px transition-all ${drag === o.id ? "opacity-50" : ""}`}>
                        <div className="flex items-center justify-between mb-2">
                          <span className="font-mono text-[11px] font-semibold text-ink-tertiary">{o.order_number || o.id}</span>
                          {(o.priority ?? 0) >= 1 && <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-md ${(o.priority ?? 0) >= 2 ? "bg-rose-50 text-rose-600" : "bg-amber-50 text-amber-700"}`}>{(o.priority ?? 0) >= 2 ? "Urgent" : "High"}</span>}
                        </div>
                        <div className="flex items-center gap-2"><Avatar name={o.client?.name || "Walk-in"} size={26} /><div className="min-w-0"><div className="text-[13px] font-semibold text-ink-primary truncate">{o.client?.name || "Walk-in"}</div>
                          <div className="text-[11px] text-ink-secondary truncate flex items-center gap-1"><M n={deviceIcon(o.device)} c="text-[12px] text-ink-tertiary" />{deviceName(o.device)}</div></div></div>
                        <div className="mt-2.5 pt-2 border-t border-slate-100 flex items-center justify-between gap-2">
                          <span className="flex items-center gap-1.5 min-w-0 text-[11px] text-ink-tertiary">{o.assigned_to?.name ? <><Avatar name={o.assigned_to.name} size={18} /><span className="truncate">{o.assigned_to.name.split(" ")[0]}</span></> : "Unassigned"}</span>
                          {(() => { const due = dueText(o.due_at, isOpenJob(o)); return due ? <span className={`text-[10px] ${due[1] ? "text-rose-600 font-semibold" : "text-ink-tertiary"}`}>{due[0]}</span> : <span className="text-[10px] text-ink-tertiary">{friendlyDate(o.scheduled_for) || ""}</span>; })()}
                          <span className="text-xs font-semibold text-ink-primary tabular-nums">{money(o.total)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {view === "scheduler" && (() => {
          const list = sched || [];
          const sameDay = (iso: string, d: Date) => { const x = new Date(iso); return x.getFullYear() === d.getFullYear() && x.getMonth() === d.getMonth() && x.getDate() === d.getDate(); };
          const hhmm = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
          const today = new Date(); today.setHours(0, 0, 0, 0);
          const step = (n: number) => setAnchor((a) => { const d = new Date(a); if (schedMode === "day") d.setDate(d.getDate() + n); else if (schedMode === "month") d.setMonth(d.getMonth() + n); else d.setDate(d.getDate() + n * 7); return d; });
          const dayItems = (d: Date) => list.filter((o) => o.scheduled_for && sameDay(o.scheduled_for, d)).sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for));
          const openCard = (o: any) => setOpen(all.find((x) => x.id === o.id) || o);
          // compact order card
          const Card = (o: any, showTime = true) => {
            const key = norm(o.status); const [cls, dot] = PILL[key] || ["bg-slate-100 text-slate-700", "bg-slate-500"];
            return (
              <button key={o.id} onClick={() => openCard(o)} className="w-full text-left bg-white rounded-lg border border-slate-200/80 shadow-clay-sm p-2 hover:border-primary/40 transition-colors">
                <div className="flex items-center justify-between gap-1">
                  <span className="font-mono font-bold text-primary text-[11px] truncate">{o.order_number || o.id?.slice(0, 6)}</span>
                  {showTime && <span className="text-[10px] font-semibold text-ink-tertiary flex-shrink-0">{hhmm(o.scheduled_for)}</span>}
                </div>
                <div className="text-[11px] font-bold text-ink-primary truncate">{o.client_name || "Walk-in"}</div>
                <div className="mt-1 flex items-center justify-between gap-1">
                  <span className={`px-1.5 py-0.5 rounded-full text-[9px] font-semibold inline-flex items-center gap-1 ${cls}`}><span className={`w-1 h-1 rounded-full ${dot}`} />{(o.status || "").replace(/_/g, " ")}</span>
                  {o.assigned_to_name && <span className="text-[9px] text-ink-tertiary truncate">{o.assigned_to_name}</span>}
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
                      <div className="w-16 flex-shrink-0 text-right text-[11px] font-mono font-semibold text-ink-tertiary pt-2">{hhmm(o.scheduled_for)}</div>
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
                          <div className="text-[10px] font-bold uppercase tracking-wider text-ink-tertiary">{d.toLocaleDateString([], { weekday: "short" })}</div>
                          <div className={`text-sm font-black ${isToday ? "text-primary" : "text-ink-primary"}`}>{d.getDate()}</div>
                        </div>
                        <div className="p-1.5 space-y-1.5 overflow-y-auto">
                          {items.length === 0 && <div className="text-[10px] text-ink-tertiary text-center py-3">—</div>}
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
                    {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((w) => <div key={w} className="text-[10px] font-bold uppercase tracking-wider text-ink-tertiary text-center py-1">{w}</div>)}
                  </div>
                  <div className="grid grid-cols-7 gap-1">
                    {Array.from({ length: 42 }, (_, i) => { const d = new Date(rFrom); d.setDate(d.getDate() + i); return d; }).map((d) => {
                      const items = dayItems(d); const inMonth = d.getMonth() === anchor.getMonth(); const isToday = d.getTime() === today.getTime();
                      return (
                        <div key={d.toISOString()} className={`rounded-lg border p-1 min-h-[92px] ${isToday ? "border-primary/40 bg-primary/5" : inMonth ? "border-slate-200/70 bg-white" : "border-slate-100 bg-slate-50/40"}`}>
                          <div className={`text-[10px] font-bold mb-1 ${isToday ? "text-primary" : inMonth ? "text-ink-secondary" : "text-ink-tertiary"}`}>{d.getDate()}</div>
                          <div className="space-y-0.5">
                            {items.slice(0, 3).map((o) => {
                              const key = norm(o.status); const [cls] = PILL[key] || ["bg-slate-100 text-slate-700"];
                              return <button key={o.id} onClick={() => openCard(o)} className={`w-full text-left truncate rounded px-1 py-0.5 text-[9px] font-semibold ${cls}`}>{hhmm(o.scheduled_for)} {o.order_number || o.client_name || ""}</button>;
                            })}
                            {items.length > 3 && <div className="text-[9px] text-ink-tertiary px-1">+{items.length - 3} more</div>}
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
                        <div className="px-2 py-2 text-[10px] font-bold uppercase tracking-wider text-ink-tertiary">Technician</div>
                        {weekDays.map((d) => { const isToday = d.getTime() === today.getTime(); return <div key={d.toISOString()} className={`px-2 py-2 text-center border-l border-slate-200/60 ${isToday ? "bg-primary/5" : ""}`}><div className="text-[10px] font-bold uppercase tracking-wider text-ink-tertiary">{d.toLocaleDateString([], { weekday: "short" })}</div><div className={`text-xs font-black ${isToday ? "text-primary" : "text-ink-primary"}`}>{d.getDate()}</div></div>; })}
                      </div>
                      {techs.map(([tid, tname]) => (
                        <div key={tid} className="grid border-t border-slate-200/60" style={{ gridTemplateColumns: `160px repeat(7, 1fr)` }}>
                          <div className="px-2 py-2 text-xs font-bold text-ink-primary flex items-center gap-1.5"><span className="w-6 h-6 rounded-full bg-gradient-to-tr from-primary to-indigo-600 grid place-items-center text-white text-[9px] font-extrabold flex-shrink-0">{(tname || "?").slice(0, 2).toUpperCase()}</span><span className="truncate">{tname}</span></div>
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
                      <span className="text-ink-tertiary capitalize">{(o.status || "").replace(/_/g, " ")}</span>
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
