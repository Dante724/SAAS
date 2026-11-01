import SkeletonRows from "../../components/SkeletonRows";
import PageSummary, { type SummaryStat } from "../../components/PageSummary";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useMe } from "../../context/Me";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { AgentDraftsCard, MorningBriefCard, WatchdogPanel } from "../../components/sense/Sense";
import { Product, Service, Bundle, money } from "../inventory/types";
import CategoryManager from "../../components/CategoryManager";
import Pager from "../../components/Pager";
import { useServerPage } from "../../hooks/useServerPage";
import { inputCls } from "../invoices/billing";
import ImportDrawer from "../../components/ImportDrawer";
import { clickable, clickableRow } from "../../lib/clickable";

const CO_PAGE = 50;

// Catalog toolbar look: a filter that is set reads as active in the primary tint.
const filterCls = (on: boolean) => `h-9 text-xs font-semibold rounded-xl border px-3 cursor-pointer ${on ? "bg-primary/10 text-primary border-primary/30" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`;
const primaryBtn = "h-9 bg-primary text-white text-xs font-semibold px-3.5 rounded-xl hover:bg-primary-container flex items-center gap-1.5";
const ghostBtn = "h-9 bg-white border border-slate-200 text-ink-secondary text-xs font-semibold px-3.5 rounded-xl hover:bg-slate-50 flex items-center gap-1.5";
const thCls = "border-b border-slate-200 bg-slate-50/60 text-xs font-semibold text-ink-tertiary";

export type RowMenuItem = { label: string; icon: string; onClick: () => void; danger?: boolean };
/* The "⋮" at the end of a row. The row itself opens the record; this holds the extra actions.
   The menu is fixed to the viewport so a scrolling table never clips it. */
export function RowMenu({ items, label = "More actions" }: { items: RowMenuItem[]; label?: string }) {
  const [at, setAt] = useState<{ top?: number; bottom?: number; right: number } | null>(null);
  useEffect(() => {
    if (!at) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setAt(null); };
    const close = () => setAt(null);
    window.addEventListener("keydown", k); window.addEventListener("resize", close);
    return () => { window.removeEventListener("keydown", k); window.removeEventListener("resize", close); };
  }, [at]);
  return (
    <span onClick={(e) => e.stopPropagation()} className="inline-flex">
      <button type="button" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={!!at}
        onClick={(e) => {
          if (at) { setAt(null); return; }
          const r = e.currentTarget.getBoundingClientRect();
          const right = Math.max(8, window.innerWidth - r.right);
          setAt(r.bottom + 48 * items.length + 24 > window.innerHeight ? { bottom: window.innerHeight - r.top + 4, right } : { top: r.bottom + 4, right });
        }}
        className={`w-8 h-8 -my-1.5 inline-grid place-items-center rounded-lg text-ink-tertiary hover:text-ink-primary hover:bg-slate-100 ${at ? "bg-slate-100 text-ink-primary" : ""}`}>
        <M n="more_vert" c="text-base" />
      </button>
      {at && createPortal(
        <div onClick={(e) => e.stopPropagation()}>
          <div className="fixed inset-0 z-40" aria-hidden="true" onClick={() => setAt(null)} />
          <div role="menu" style={{ top: at.top, bottom: at.bottom, right: at.right }} className="fixed z-50 w-48 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 text-left">
            {items.map((it) => (
              <button key={it.label} role="menuitem" type="button" onClick={() => { setAt(null); it.onClick(); }}
                className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm hover:bg-slate-50 ${it.danger ? "text-rose-600" : "text-ink-primary"}`}>
                <M n={it.icon} c={`text-base ${it.danger ? "" : "text-ink-tertiary"}`} />{it.label}
              </button>
            ))}
          </div>
        </div>, document.body)}
    </span>
  );
}

type Dash = {
  today?: { work_orders?: number; revenue?: number; refunds?: number };
  kpis?: {
    new?: number; urgent?: number; overdue?: number; receivables?: number;
    ar?: number; ap?: number; total_money?: number; cash?: number; cashless?: number;
  };
  status_mix?: { group: string; count: number }[];
  monthly?: {
    work_orders_created?: number; work_orders_won?: number; revenue?: number;
    conversion?: number; avg_work_order?: number;
  };
};

type Entry = {
  id: string; employee_name?: string; order_number?: string | null; category?: string;
  started_at?: string | null; ended_at?: string | null; duration_min?: number; running?: boolean;
};
type RosterRow = {
  employee_name?: string; on_shift?: boolean; elapsed_sec?: number; order_number?: string | null;
};

// Home = the company overview landing. Catalog (services/products/job kits) moved to
// its own section (/catalog); this page is no longer a hub-of-everything.
// Employee hours moved to Timesheets; Home is purely the overview now.
const hm = (min?: number) => {
  const m = Math.round(min ?? 0);
  return `${Math.floor(m / 60)}h ${m % 60}m`;
};

export default function CompanyHub() {
  // Role-redirect: if this person's role has a landing page other than Home, send
  // them there. Owners (no role / no home_route) stay on Home. Loop-safe: only
  // redirects to a non-"/" route, and only once per mount.
  const { me } = useMe();
  const navigate = useNavigate();
  const [redirected, setRedirected] = useState(false);
  useEffect(() => {
    const dest = me?.home_route;
    if (!redirected && dest && dest !== "/") { setRedirected(true); navigate(dest, { replace: true }); }
  }, [me, redirected, navigate]);

  return (
    <Shell title="Home">
      <div className="flex-1 overflow-y-auto">
        <div className="p-6">
          <Dashboard />
        </div>
      </div>
    </Shell>
  );
}

/* ── Dashboard tab ─────────────────────────────────────────────── */
type FeedItem = { key: string; kind: string; icon: string; count: number; title: string; to: string };
// The action verb per signal — taps straight to where you complete it.
const FEED_ACTIONS: Record<string, { label: string; icon: string }> = {
  overdue: { label: "Reassign", icon: "group_add" },
  messages: { label: "Reply", icon: "open_in_new" },
  ready: { label: "Notify", icon: "notifications" },
  urgent: { label: "Review", icon: "chevron_right" },
  unpaid: { label: "Remind", icon: "open_in_new" },
  lowstock: { label: "Reorder", icon: "point_of_sale" },
  tasks: { label: "Open", icon: "open_in_new" },
  inquiries: { label: "Triage", icon: "open_in_new" },
};
function Dashboard() {
  const navigate = useNavigate();
  const [d, setD] = useState<Dash | null>(null);
  const [err, setErr] = useState(false);
  const [feed, setFeed] = useState<{ items: FeedItem[]; ok: boolean } | null>(null);
  useEffect(() => {
    api.get<Dash>("/reports/company-dashboard").then(setD).catch(() => setErr(true));
    api.get<{ items: FeedItem[]; ok: boolean }>("/reports/home-feed").then(setFeed).catch(() => setFeed({ items: [], ok: true }));
  }, []);
  const [feedOpen, setFeedOpen] = useState<boolean | null>(() => {
    try { const v = localStorage.getItem("ros_feed_open"); return v === null ? null : v === "1"; } catch { return null; }
  });
  const [feedShowAll, setFeedShowAll] = useState(false);
  const toggleFeed = () => setFeedOpen((o) => { const n = !(o ?? true); try { localStorage.setItem("ros_feed_open", n ? "1" : "0"); } catch { /* ignore */ } return n; });
  const FEED_CAP = 5;
  const feedTone: Record<string, string> = {
    danger: "bg-rose-50 text-rose-600 border-rose-100",
    warning: "bg-amber-50 text-amber-600 border-amber-100",
    success: "bg-emerald-50 text-emerald-600 border-emerald-100",
    info: "bg-blue-50 text-primary border-blue-100",
  };
  const k = d?.kpis ?? {}, t = d?.today ?? {}, m = d?.monthly ?? {};
  const isZero = (n?: number) => !n;
  // A role that can't see money gets null for it from the server; those tiles are left out.
  const has = (v: unknown) => v !== null && v !== undefined;
  // While the dashboard loads, every tile (and the money tiles' hint line) is laid out with a
  // skeleton value so nothing moves when the numbers arrive (layout shift was 0.15–0.47).
  const loading = !d && !err;
  const sk = (w = "w-14") => <span className={`sk inline-block h-7 ${w} align-middle`} aria-hidden />;
  const val = (v: string) => (loading ? sk() : v);
  const stats: SummaryStat[] = [
    { label: "New jobs today", value: val(String(t.work_orders ?? 0)), onClick: () => navigate("/orders") },
    ...(loading || has(t.revenue) ? [{ label: "Paid today", value: val(money(t.revenue)), hint: t.refunds ? `${money(t.refunds)} refunded` : undefined, onClick: () => navigate("/finance") }] : []),
    { label: "Waiting to start", value: val(String(k.new ?? 0)), onClick: () => navigate("/orders?status=new") },
    { label: "Urgent", value: val(String(k.urgent ?? 0)), onClick: () => navigate("/orders?priority=urgent") },
    { label: "Late", value: val(String(k.overdue ?? 0)), tone: "bad", zero: isZero(k.overdue), onClick: () => navigate("/orders") },
    ...(loading || has(k.receivables) ? [{ label: "To collect", value: val(money(k.receivables)), onClick: () => navigate("/invoices") }] : []),
    ...(loading || has(k.total_money) ? [{ label: "In your accounts", value: val(money(k.total_money)),
      hint: loading ? <span className="sk inline-block h-3 w-24 align-middle" aria-hidden /> : `${money(k.cash)} cash · ${money(k.cashless)} bank`, onClick: () => navigate("/finance") }] : []),
  ];
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-ink-primary">Today</h2>
        <p className="text-sm text-ink-tertiary">{new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}</p>
      </div>

      <MorningBriefCard onOpen={(to) => navigate(to)} />
      <WatchdogPanel />
      <AgentDraftsCard />

      {/* Needs you — the action-first feed. Collapsible, but the count always shows
          so nothing waiting is ever hidden. Defaults open when there are items. */}
      {!feed && (
        <div className="rounded-2xl bg-white border border-slate-200/80 shadow-clay-sm p-4" aria-busy="true">
          <div className="flex items-center gap-2 py-0.5">
            <M n={feedOpen === false ? "chevron_right" : "expand_more"} c="text-ink-tertiary text-base" />
            <h3 className="text-sm font-semibold text-ink-primary">Needs you</h3>
          </div>
          {feedOpen !== false && (
            <div className="space-y-1.5 mt-2.5" aria-hidden>
              {[0, 1, 2].map((i) => <div key={i} className="h-[46px] rounded-xl border border-slate-200/70 px-3 flex items-center gap-3"><span className="sk w-7 h-7" /><span className="sk h-3.5 w-48 max-w-[50%]" /></div>)}
            </div>
          )}
        </div>
      )}
      {feed && (() => {
        const n = feed.items.length;
        const expanded = (feedOpen ?? n > 0);   // null = auto: open iff there are items
        const shown = feedShowAll ? feed.items : feed.items.slice(0, FEED_CAP);
        return (
          <div className="rounded-2xl bg-white border border-slate-200/80 shadow-clay-sm p-4">
            <button onClick={toggleFeed} aria-expanded={expanded}
              className="w-full flex items-center gap-2 py-0.5 text-left focus:outline-none">
              <M n={expanded ? "expand_more" : "chevron_right"} c="text-ink-tertiary text-base" />
              <h3 className="text-sm font-semibold text-ink-primary">Needs you</h3>
              {n > 0 && <span className="text-[11px] font-bold bg-rose-100 text-rose-600 rounded-full px-1.5 py-0.5 leading-none">{n}</span>}
              {feed.ok && <span className="text-[11px] font-semibold text-emerald-600 flex items-center gap-1"><M n="check_circle" c="text-xs" />All caught up</span>}
            </button>
            {expanded && (
              feed.ok ? (
                <div className="flex items-center gap-2 text-sm text-ink-tertiary py-2 mt-1"><M n="check_circle" c="text-base text-emerald-500" />Nothing needs attention right now.</div>
              ) : (
                <div className="space-y-1.5 mt-2.5">
                  {shown.map((it) => {
                    const a = FEED_ACTIONS[it.key] || { label: "Review", icon: "chevron_right" };
                    return (
                      <div key={it.key}
                        className="w-full flex items-center gap-3 rounded-xl border border-slate-200/70 hover:border-primary/50 hover:bg-blue-50/30 px-3 py-2 transition-all">
                        <span className={`w-7 h-7 rounded-lg border flex items-center justify-center shrink-0 ${feedTone[it.kind] || feedTone.info}`}><M n={it.icon} c="text-sm" /></span>
                        <button onClick={() => navigate(it.to)} className="flex-1 py-1 text-sm font-medium text-ink-primary text-left focus:outline-none">{it.title}</button>
                        <button onClick={() => navigate(it.to)}
                          className={`shrink-0 inline-flex items-center gap-1 text-xs font-semibold rounded-full border px-2.5 py-1 ${feedTone[it.kind] || feedTone.info} hover:brightness-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40`}>
                          <M n={a.icon} c="text-sm" />{a.label}
                        </button>
                      </div>
                    );
                  })}
                  {n > FEED_CAP && (
                    <button onClick={() => setFeedShowAll((v) => !v)} className="text-[11px] font-semibold text-primary hover:underline px-1 pt-1">
                      {feedShowAll ? "Show less" : `Show ${n - FEED_CAP} more`}
                    </button>
                  )}
                </div>
              )
            )}
          </div>
        );
      })()}

      {err && <div className="rounded-xl border border-amber-200 bg-amber-50 text-amber-700 text-sm px-4 py-3">Couldn't load dashboard metrics right now.</div>}
      <PageSummary stats={stats} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-2xl bg-white border border-slate-200/80 shadow-clay-sm p-5">
          <h3 className="text-sm font-bold text-ink-primary mb-3">This month</h3>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <Metric l="Jobs in" v={String(m.work_orders_created ?? 0)} loading={loading} />
            <Metric l="Jobs finished" v={String(m.work_orders_won ?? 0)} loading={loading} />
            {(loading || has(m.revenue)) && <Metric l="Paid" v={money(m.revenue)} loading={loading} />}
            <Metric l="Finished share" v={`${m.conversion ?? 0}%`} loading={loading} />
            {(loading || has(m.avg_work_order)) && <Metric l="Average job" v={money(m.avg_work_order)} loading={loading} />}
          </div>
        </div>
        <div className="rounded-2xl bg-white border border-slate-200/80 shadow-clay-sm p-5">
          <h3 className="text-sm font-bold text-ink-primary mb-3">Open jobs by stage</h3>
          {loading ? (
            <div className="space-y-2" aria-hidden>{[0, 1, 2].map((i) => <div key={i} className="h-5 flex items-center"><span className="sk h-3.5 w-40" /></div>)}</div>
          ) : (d?.status_mix ?? []).length === 0 ? (
            <p className="text-sm text-ink-tertiary">No open orders.</p>
          ) : (
            <div className="space-y-2">
              {(d?.status_mix ?? []).map((s) => (
                <div key={s.group} className="flex items-center justify-between text-sm">
                  <span className="text-ink-secondary">{({ new: "Waiting to start", in_progress: "Being worked on", pending: "Waiting (parts or customer)", ready: "Ready for pickup", closed: "Done", dropped: "Closed without repair" } as Record<string, string>)[s.group] || s.group.replace(/_/g, " ")}</span>
                  <span className="font-semibold tabular-nums text-ink-primary">{s.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
function Metric({ l, v, mono, loading }: { l: string; v: string; mono?: boolean; loading?: boolean }) {
  return <div><p className="text-xs text-ink-tertiary">{l}</p><p className={`text-lg font-semibold text-ink-primary tabular-nums ${mono ? "" : ""}`}>{loading ? <span className="sk inline-block h-5 w-12 align-middle" aria-hidden /> : v}</p></div>;
}

/* ── Employee hours tab ────────────────────────────────────────── */
export function EmployeeHours() {
  const [roster, setRoster] = useState<RosterRow[] | null>(null);
  // Time entries page on the server (the API serves at most 200 rows per request).
  const ep = useServerPage<Entry>("/timeclock/entries", {}, { pageSize: CO_PAGE });
  const entries = ep.err ? [] : ep.rows;
  useEffect(() => {
    api.get<{ items: RosterRow[] }>("/timeclock/roster").then((r) => setRoster(r.items || [])).catch(() => setRoster([]));
  }, []);
  const onClock = (roster ?? []).filter((r) => r.on_shift);
  const entTotal = ep.total;
  const pageEntries = entries || [];
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-ink-primary">Employee hours</h2>
        <p className="text-sm text-ink-tertiary">Who's on the clock and recent labour time entries.</p>
      </div>
      <div className="rounded-2xl bg-white border border-slate-200/80 shadow-clay-sm p-5">
        <h3 className="text-sm font-bold text-ink-primary mb-3">On the clock now</h3>
        {!roster ? <p className="text-sm text-ink-tertiary">Loading…</p> :
          onClock.length === 0 ? <p className="text-sm text-ink-tertiary">Nobody is currently clocked in.</p> :
          <div className="flex flex-wrap gap-3">
            {onClock.map((r, i) => (
              <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-xl bg-emerald-50 border border-emerald-200">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                <span className="text-sm font-semibold text-ink-primary">{r.employee_name}</span>
                <span className="text-xs tabular-nums text-emerald-700">{hm((r.elapsed_sec ?? 0) / 60)}</span>
              </div>
            ))}
          </div>}
      </div>
      <div className="rounded-2xl bg-white border border-slate-200/80 shadow-clay-card overflow-hidden">
        <table className="w-full text-left border-collapse">
          <thead className="border-b border-slate-100 bg-slate-50/60 text-[11px] font-bold text-ink-tertiary "><tr>
            <th className="py-3 px-5">Employee</th><th className="py-3 px-3">Work order</th><th className="py-3 px-3">Type</th>
            <th className="py-3 px-3">Started</th><th className="py-3 px-3 text-right">Duration</th><th className="py-3 px-3">Status</th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100 text-xs">
            {!entries && <SkeletonRows cols={6} />}
            {entries?.length === 0 && <tr><td colSpan={6} className="py-6 px-5 text-ink-tertiary">No time entries yet.</td></tr>}
            {pageEntries.map((e) => (
              <tr key={e.id} className="hover:bg-blue-50/30">
                <td className="py-3 px-5 font-bold text-ink-primary">{e.employee_name || "—"}</td>
                <td className="py-3 px-3 text-primary font-mono">{e.order_number || "—"}</td>
                <td className="py-3 px-3 text-ink-secondary capitalize">{e.category || "—"}</td>
                <td className="py-3 px-3 text-ink-secondary">{e.started_at ? new Date(e.started_at).toLocaleString() : "—"}</td>
                <td className="py-3 px-3 text-right tabular-nums font-bold text-ink-primary">{hm(e.duration_min)}</td>
                <td className="py-3 px-3">{e.running
                  ? <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-emerald-100 text-emerald-700">Running</span>
                  : <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-100 text-ink-secondary">Done</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {entries && entTotal > 0 && <Pager total={entTotal} page={ep.page} pageSize={CO_PAGE} onPage={ep.setPage} noun="time entries" />}
      </div>
    </div>
  );
}

/* ── Services tab ──────────────────────────────────────────────── */
export function Services({ onOpen, refresh }: { onOpen: (s?: Service) => void; refresh?: number }) {
  const [items, setItems] = useState<Service[] | null>(null);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState(false);
  const [page, setPage] = useState(1);
  const [catFilter, setCatFilter] = useState("");
  const [cats, setCats] = useState<{ id: string; name: string; depth: number }[]>([]);
  const loadCats = () => {
    const flat: { id: string; name: string; depth: number }[] = [];
    const walk = (ns: any[], d: number) => (ns || []).forEach((n) => { flat.push({ id: n.id, name: n.name, depth: d }); if (n.children?.length) walk(n.children, d + 1); });
    api.get<any>("/catalog/categories?entity_type=service").then((r) => { walk(r.tree || [], 0); setCats(flat); }).catch(() => setCats([]));
  };
  const { can } = useMe();
  const [importing, setImporting] = useState(false);
  const [err, setErr] = useState(false);
  const load = () => { setErr(false); api.get<{ items: Service[] }>("/catalog/services").then((r) => setItems(r.items || [])).catch(() => { setItems([]); setErr(true); }); };
  useEffect(() => { load(); loadCats(); }, [refresh]); // eslint-disable-line
  const rows = (items ?? []).filter((s) =>
    (!q || (s.name || "").toLowerCase().includes(q.toLowerCase())) &&
    (!catFilter || s.category_id === catFilter));
  useEffect(() => { setPage(1); }, [q, catFilter]);
  const pages = Math.max(1, Math.ceil(rows.length / CO_PAGE));
  const pageRows = rows.slice((Math.min(page, pages) - 1) * CO_PAGE, Math.min(page, pages) * CO_PAGE);
  return (
    <div className="space-y-4">
      {cat && <CategoryManager entity="service" onClose={() => setCat(false)} onChanged={() => { load(); loadCats(); }} />}
      {importing && <ImportDrawer kind="services" onClose={() => setImporting(false)} onDone={() => { load(); loadCats(); }} />}
      <div className="flex items-center gap-3 flex-wrap">
        <button onClick={() => onOpen()} className={primaryBtn}><M n="add" c="text-sm" />New service</button>
        {can("can_edit_labors") && <button onClick={() => setImporting(true)} className={ghostBtn}><M n="upload" c="text-sm" />Import</button>}
        <button onClick={() => setCat(true)} className={ghostBtn}><M n="inventory" c="text-sm" />Categories</button>
        <select aria-label="Category" value={catFilter} onChange={(e) => setCatFilter(e.target.value)} className={filterCls(!!catFilter)}>
          <option value="">Category: All</option>
          {cats.map((c) => <option key={c.id} value={c.id}>{`${"  ".repeat(c.depth)}${c.name}`}</option>)}
        </select>
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <M n="search" c="absolute left-3 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search services…" aria-label="Search services" className={`${inputCls} pl-9`} />
        </div>
      </div>
      <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
        <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left border-collapse">
          <thead className={thCls}><tr>
            <th className="py-3 px-5 font-semibold">Service</th><th className="py-3 px-3 font-semibold">Category</th><th className="py-3 px-3 text-right font-semibold">Duration</th><th className="py-3 px-3 text-right font-semibold">Price</th><th className="py-3 pr-4 w-12"><span className="sr-only">Actions</span></th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100 text-xs">
            {!items && <SkeletonRows cols={5} />}
            {items && err && <tr><td colSpan={5} className="py-6 px-5 text-rose-600">Couldn't load services. <button onClick={load} className="font-semibold underline">Try again</button></td></tr>}
            {items && !err && rows.length === 0 && <tr><td colSpan={5} className="py-12 px-5 text-center">
              {items.length
                ? <><div className="text-sm font-semibold text-ink-primary">No services match</div><div className="text-xs text-ink-tertiary mt-1">Try another name, or clear the category filter.</div></>
                : <><div className="text-sm font-semibold text-ink-primary">No services yet</div><div className="text-xs text-ink-tertiary mt-1">Services are the work you charge for. Add one to put it on jobs, sales and online booking.</div>
                    <button onClick={() => onOpen()} className={`${primaryBtn} mx-auto mt-3`}><M n="add" c="text-sm" />New service</button></>}
            </td></tr>}
            {pageRows.map((s) => (
              <tr key={s.id} className="hover:bg-slate-50 cursor-pointer" {...clickableRow(() => onOpen(s))}>
                <td className="py-3 px-5">
                  <span className="font-semibold text-ink-primary">{s.name}</span>
                  {s.bookable === false && <span title="Clients can't pick this service when they book online" className="block text-[11px] text-ink-tertiary mt-0.5">Not bookable online</span>}
                </td>
                <td className="py-3 px-3 text-ink-secondary">{s.category_name || s.category || <span className="text-ink-tertiary">—</span>}</td>
                <td className="py-3 px-3 text-right text-ink-secondary tabular-nums">{s.duration_min ? `${s.duration_min} min` : "—"}</td>
                <td className="py-3 px-3 text-right font-semibold text-ink-primary tabular-nums">{money(s.price)}</td>
                <td className="py-3 pr-4 text-right"><RowMenu label={`More actions for ${s.name}`} items={[
                  { label: "Edit", icon: "edit", onClick: () => onOpen(s) },
                  { label: "Duplicate", icon: "content_copy", onClick: () => onOpen({ ...s, id: undefined as unknown as string, name: `${s.name || ""} (copy)` }) },
                ]} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        {items && rows.length > 0 && <Pager total={rows.length} page={Math.min(page, pages)} pageSize={CO_PAGE} onPage={setPage} noun="services" />}
      </div>
    </div>
  );
}

/* ── Products tab ──────────────────────────────────────────────── */
export function Products({ onOpen, refresh }: { onOpen: (p?: Product) => void; refresh?: number }) {
  const [items, setItems] = useState<Product[] | null>(null);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [catFilter, setCatFilter] = useState("");          // selected category_id ("" = all)
  const [cat, setCat] = useState(false);                   // Categories manager open
  const [cats, setCats] = useState<{ id: string; name: string; depth: number }[]>([]);
  const loadCats = () => {
    const flat: { id: string; name: string; depth: number }[] = [];
    const walk = (ns: any[], d: number) => (ns || []).forEach((n) => { flat.push({ id: n.id, name: n.name, depth: d }); if (n.children?.length) walk(n.children, d + 1); });
    api.get<any>("/catalog/categories?entity_type=product").then((r) => { walk(r.tree || [], 0); setCats(flat); }).catch(() => setCats([]));
  };
  const { can } = useMe();
  const [importing, setImporting] = useState(false);
  const [err, setErr] = useState(false);
  const load = () => { setErr(false); api.get<{ items: Product[] }>("/catalog/products").then((r) => setItems(r.items || [])).catch(() => { setItems([]); setErr(true); }); };
  useEffect(() => { load(); loadCats(); }, [refresh]); // eslint-disable-line
  const catName = Object.fromEntries(cats.map((c) => [c.id, c.name]));
  const ql = q.toLowerCase();
  const rows = (items ?? []).filter((p) =>
    (!q || [p.name, p.sku, p.code, p.mpn, p.barcode].some((v) => (v || "").toLowerCase().includes(ql))) &&
    (!catFilter || p.category_id === catFilter));
  useEffect(() => { setPage(1); }, [q, catFilter]);
  const pages = Math.max(1, Math.ceil(rows.length / CO_PAGE));
  const pageRows = rows.slice((Math.min(page, pages) - 1) * CO_PAGE, Math.min(page, pages) * CO_PAGE);
  return (
    <div className="space-y-4">
      {cat && <CategoryManager entity="product" onClose={() => setCat(false)} onChanged={loadCats} />}
      {importing && <ImportDrawer kind="products" onClose={() => setImporting(false)} onDone={() => { load(); loadCats(); }} />}
      <div className="flex items-center gap-3 flex-wrap">
        <button onClick={() => onOpen()} className={primaryBtn}><M n="add" c="text-sm" />New product</button>
        {(can("can_create_products") || can("can_edit_products") || can("can_manage_inventory")) && <button onClick={() => setImporting(true)} className={ghostBtn}><M n="upload" c="text-sm" />Import</button>}
        <button onClick={() => setCat(true)} className={ghostBtn}><M n="inventory" c="text-sm" />Categories</button>
        <select aria-label="Category" value={catFilter} onChange={(e) => setCatFilter(e.target.value)} className={filterCls(!!catFilter)}>
          <option value="">Category: All</option>
          {cats.map((c) => <option key={c.id} value={c.id}>{`${"  ".repeat(c.depth)}${c.name}`}</option>)}
        </select>
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <M n="search" c="absolute left-3 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search products, SKU, MPN…" aria-label="Search products" className={`${inputCls} pl-9`} />
        </div>
      </div>
      <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
        <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left border-collapse">
          <thead className={thCls}><tr>
            <th className="py-3 px-5 font-semibold">Product</th><th className="py-3 px-3 font-semibold">Category</th><th className="py-3 px-3 font-semibold">SKU</th><th className="py-3 px-3 font-semibold">MPN</th><th className="py-3 px-3 text-right font-semibold">Cost</th><th className="py-3 px-3 text-right font-semibold">Sell price</th><th className="py-3 px-3 text-right font-semibold">In stock</th><th className="py-3 pr-4 w-12"><span className="sr-only">Actions</span></th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100 text-xs">
            {!items && <SkeletonRows cols={8} />}
            {items && err && <tr><td colSpan={8} className="py-6 px-5 text-rose-600">Couldn't load products. <button onClick={load} className="font-semibold underline">Try again</button></td></tr>}
            {items && !err && rows.length === 0 && <tr><td colSpan={8} className="py-12 px-5 text-center">
              {items.length
                ? <><div className="text-sm font-semibold text-ink-primary">No products match</div><div className="text-xs text-ink-tertiary mt-1">Search by name, SKU, MPN or barcode, or clear the category filter.</div></>
                : <><div className="text-sm font-semibold text-ink-primary">No products yet</div><div className="text-xs text-ink-tertiary mt-1">Products are the parts and items you sell. Add one to use it on jobs and at the till.</div>
                    <button onClick={() => onOpen()} className={`${primaryBtn} mx-auto mt-3`}><M n="add" c="text-sm" />New product</button></>}
            </td></tr>}
            {pageRows.map((p) => (
              <tr key={p.id} className="hover:bg-slate-50 cursor-pointer" {...clickableRow(() => onOpen(p))}>
                <td className="py-3 px-5 font-semibold text-ink-primary">{p.name}</td>
                <td className="py-3 px-3 text-ink-secondary">{p.category_id ? (catName[p.category_id] || "—") : <span className="text-ink-tertiary">Uncategorized</span>}</td>
                <td className="py-3 px-3 text-ink-secondary font-mono">{p.sku || p.code || "—"}</td>
                <td className="py-3 px-3 text-ink-tertiary font-mono">{p.mpn || "—"}</td>
                <td className="py-3 px-3 text-right text-ink-secondary tabular-nums">{money(p.cost_price)}</td>
                <td className="py-3 px-3 text-right font-semibold text-ink-primary tabular-nums">{money(p.sell_price)}</td>
                <td className={`py-3 px-3 text-right tabular-nums ${(p.qty_available ?? 0) <= 0 ? "text-ink-tertiary" : p.low_stock ? "text-amber-700 font-semibold" : "text-ink-primary"}`}>{p.qty_available ?? 0}</td>
                <td className="py-3 pr-4 text-right"><RowMenu label={`More actions for ${p.name}`} items={[
                  { label: "Edit", icon: "edit", onClick: () => onOpen(p) },
                  { label: "Duplicate", icon: "content_copy", onClick: () => onOpen({ ...p, id: undefined as unknown as string, name: `${p.name || ""} (copy)`, sku: undefined, code: undefined, barcode: undefined, mpn: undefined }) },
                ]} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        {items && rows.length > 0 && <Pager total={rows.length} page={Math.min(page, pages)} pageSize={CO_PAGE} onPage={setPage} noun="products" />}
      </div>
    </div>
  );
}

/* ── Bundles tab ───────────────────────────────────────────────── */
export function Bundles({ onOpen, refresh }: { onOpen: (b?: Bundle) => void; refresh?: number }) {
  const { can } = useMe();
  const [items, setItems] = useState<Bundle[] | null>(null);
  const [cat, setCat] = useState(false);
  const load = () => api.get<{ items: Bundle[] }>("/bundles").then((r) => setItems(r.items || [])).catch(() => setItems([]));
  useEffect(() => { load(); }, [refresh]); // eslint-disable-line
  return (
    <div className="space-y-4">
      {cat && <CategoryManager entity="bundle" onClose={() => setCat(false)} onChanged={load} />}
      <div className="flex items-center gap-3">
        {can("can_manage_bundles") && <button onClick={() => onOpen()} className={primaryBtn}><M n="add" c="text-sm" />New job kit</button>}
        <button onClick={() => setCat(true)} className={ghostBtn}><M n="inventory" c="text-sm" />Categories</button>
      </div>
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-6 space-y-3">
        {!items && <p className="text-xs text-ink-tertiary">Loading job kits…</p>}
        {items?.length === 0 && (
          <div className="flex flex-col items-start gap-3">
            <p className="text-xs text-ink-tertiary">No job kits yet. A job kit sells a set of parts and services together at one price, like "Screen replacement".</p>
            {can("can_manage_bundles") && <button onClick={() => onOpen()} className={primaryBtn}><M n="add" c="text-sm" />Create your first job kit</button>}
          </div>
        )}
        {items?.map((bd) => (
          <div key={bd.id} {...clickable(() => onOpen(bd))} className="border border-slate-200/80 rounded-xl p-4 bg-white flex items-center justify-between shadow-clay-sm hover:border-primary/40 cursor-pointer transition-all">
            <div><div className="font-bold text-ink-primary text-sm flex items-center gap-1.5">{bd.name}{bd.broken && <span className="text-[11px] font-bold bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded">Needs fixing</span>}</div><div className="text-[11px] text-ink-tertiary">{(bd.components || []).length} components{bd.broken ? " · an item no longer exists" : ""}</div></div>
            <div className="text-right">
              <span className="tabular-nums font-bold text-ink-primary text-sm">{money(bd.price)}</span>
              {bd.total_std && bd.total_std > (bd.price || 0) && <span className="text-[11px] text-emerald-600 block font-semibold">Save {money(bd.total_std - (bd.price || 0))}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
