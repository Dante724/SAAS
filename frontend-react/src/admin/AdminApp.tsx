import { useCallback, useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { adminApi, whenSignedOut, SEGMENTS, fmtDate, type Me, type Shop } from "./api";
import SignIn from "./SignIn";
import ShopPanel from "./ShopPanel";
import { TeamView, CodesView, LogView } from "./Views";
import { AdminCard, rowCls, useAdminTheme } from "./ui";
import { clickableRow } from "../lib/clickable";
import { TeklyMark } from "../components/brand/TeklyLogo";
import Button, { IconButton } from "../components/Button";
import Tabs from "../components/Tabs";
import PageLayout, { PanelHeader, Toolbar } from "../components/PageLayout";
import { StatTile } from "../components/Card";
import EmptyState, { TableEmpty } from "../components/EmptyState";
import { Input, Select } from "../components/Field";
import SkeletonRows from "../components/SkeletonRows";
import { tableClass, theadRowClass, Th, Td } from "../components/Table";

type View = "today" | "shops" | "codes" | "team" | "log";

/** When a trial stops: the billing trial clock or a hand-set term end, whichever comes first from today. */
function endsOn(s: Shop): string | null {
  const today = new Date().toISOString().slice(0, 10);
  return [s.trial_ends_at, s.sub_end].filter((d): d is string => !!d && d >= today).sort()[0] || null;
}

export default function AdminApp() {
  useAdminTheme();
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const load = useCallback(() => adminApi.get<Me>("/me").then(setMe).catch(() => setMe(null)), []);
  useEffect(() => { whenSignedOut(() => setMe(null)); load(); }, [load]);
  if (me === undefined) return <div className="min-h-screen grid place-items-center bg-canvas text-sm text-ink-tertiary">Loading…</div>;
  if (me === null) return <SignIn onSignedIn={load} />;
  return <Shell me={me} onSignOut={async () => { try { await adminApi.post("/logout"); } catch { /* already out */ } setMe(null); }} />;
}

// The admin's own light/dark switch: it follows (and sets) the same choice as the shop app.
function ThemeSwitch() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  function flip() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("ros_theme", next ? "dark" : "light"); } catch { /* ignore */ }
  }
  return <IconButton label={dark ? "Light mode" : "Dark mode"} onClick={flip} icon={dark ? <Sun size={17} /> : <Moon size={17} />} />;
}

function Shell({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const can = (p: string) => !!me.permissions[p];
  const [view, setView] = useState<View>("today");
  const [openShop, setOpenShop] = useState<string | null>(null);
  const nav: [View, string, boolean][] = [
    ["today", "Today", true], ["shops", "Shops", can("view_shops")], ["codes", "Codes", can("create_codes")],
    ["team", "Team", can("manage_admins")], ["log", "Action log", can("read_log")],
  ];
  return (
    <div className="flex h-screen flex-col bg-canvas font-sans text-ink-primary">
      <header className="shrink-0 bg-white">
        <div className="flex h-14 items-center gap-2.5 px-4 sm:px-6">
          <TeklyMark size={28} />
          <span className="whitespace-nowrap text-base font-semibold">Tekly admin</span>
          <div className="ml-auto flex min-w-0 items-center gap-1">
            <span className="hidden truncate pr-2 text-sm text-ink-secondary md:inline">{me.name} · {me.role.name}</span>
            <ThemeSwitch />
            <Button variant="ghost" onClick={onSignOut}>Sign out</Button>
          </div>
        </div>
        <Tabs label="Admin sections" className="px-4 sm:px-6" value={view} onChange={setView}
          tabs={nav.filter(([, , ok]) => ok).map(([id, label]) => ({ id, label }))} />
      </header>
      <main className="app-main flex min-h-0 flex-1 flex-col">
        <PageLayout className="max-w-6xl">
          {view === "today" && <TodayView me={me} onOpenShop={setOpenShop} />}
          {view === "shops" && <ShopsView me={me} onOpenShop={setOpenShop} />}
          {view === "codes" && <CodesView />}
          {view === "team" && <TeamView me={me} />}
          {view === "log" && <LogView />}
        </PageLayout>
      </main>
      {openShop && <ShopPanel id={openShop} me={me} onClose={() => setOpenShop(null)} />}
    </div>
  );
}

type Req = { id: string; action: string; payload: any; reason: string; status: string; requested_by: string; shop: string; org_id: string; created_at: string };

function describe(r: Req) {
  if (r.action === "discount") return `${r.payload?.pct}% off${r.payload?.until ? ` until ${fmtDate(r.payload.until)}` : ", no end date"}`;
  if (r.action === "extend_trial") return `${r.payload?.days} more trial days`;
  if (r.action === "plan_change") return `Move to the ${r.payload?.plan} plan`;
  return r.action;
}

function TodayView({ me, onOpenShop }: { me: Me; onOpenShop: (id: string) => void }) {
  const [stats, setStats] = useState<any>(null);
  const [reqs, setReqs] = useState<Req[] | null>(null);
  const [ending, setEnding] = useState<Shop[] | null>(null);
  const [err, setErr] = useState("");
  const approver = !!me.permissions.approve_requests;
  const load = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([adminApi.get<any>("/stats"), adminApi.get<{ items: Req[] }>("/approvals")]);
      setStats(s); setReqs(r.items);
      const t = await adminApi.get<{ items: Shop[] }>("/tenants?ending_days=7&limit=50").catch(() => ({ items: [] as Shop[] }));
      setEnding([...t.items].sort((a, b) => (endsOn(a) || "").localeCompare(endsOn(b) || "")).slice(0, 10));
    } catch (e: any) { setErr(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  async function decide(id: string, decision: "approve" | "decline") {
    try { await adminApi.post(`/approvals/${id}/decide`, { decision }); load(); } catch (e: any) { setErr(e.message); }
  }
  const tiles: [string, number | undefined][] = [
    ["Paying", stats?.segments.paid], ["On trial", stats ? stats.segments.new + stats.segments.trial : undefined], ["In your book", stats?.my_book],
    [approver ? "Waiting for you" : "Suspended", stats ? (approver ? stats.pending_requests ?? 0 : stats.segments.suspended) : undefined],
  ];
  return (<>
    <PanelHeader title="Today" sub="Your numbers, what's waiting for you and the trials ending soon." />
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {tiles.map(([l, v]) => (
        <div key={l} className="rounded-2xl border border-slate-200/90 bg-white p-4 shadow-clay-card">
          <StatTile label={l} value={v === undefined ? <span className="sk inline-block h-7 w-10 rounded" aria-hidden="true" /> : v} />
        </div>
      ))}
    </div>
    <AdminCard title={approver ? "Waiting for your OK" : "Your requests"}>
      {reqs === null ? <Loading /> : reqs.length === 0
        ? <EmptyState size="inline" className="px-4" title={approver ? "Nothing is waiting for you." : "You have no open requests."} />
        : reqs.map((r) => (
          <div key={r.id} className={rowCls}>
            <button onClick={() => onOpenShop(r.org_id)} className="font-semibold hover:underline">{r.shop}</button>
            <span>{describe(r)}</span>
            <span className="text-ink-tertiary">{r.requested_by} · {r.reason}</span>
            {approver && r.status === "pending" ? (
              <span className="flex w-full gap-2 sm:ml-auto sm:w-auto">
                <Button size="sm" variant="primary" onClick={() => decide(r.id, "approve")}>Approve</Button>
                <Button size="sm" onClick={() => decide(r.id, "decline")}>Decline</Button>
              </span>
            ) : <span className="text-xs text-ink-tertiary sm:ml-auto">{r.status === "expired" ? "Expired" : "Waiting"}</span>}
          </div>
        ))}
    </AdminCard>
    <AdminCard title="Trials ending this week">
      {ending === null ? <Loading /> : ending.length === 0
        ? <EmptyState size="inline" className="px-4" title="No trials end in the next 7 days." />
        : ending.map((s) => (
          <button key={s.id} onClick={() => onOpenShop(s.id)} className={`${rowCls} w-full text-left hover:bg-slate-50`}>
            <span className="font-semibold">{s.name}</span><span className="text-ink-tertiary">{s.country || ""}</span>
            <span className="ml-auto text-amber-700">Ends {fmtDate(endsOn(s))}</span>
          </button>
        ))}
    </AdminCard>
    {err && <p role="alert" className="text-sm text-rose-600">{err}</p>}
  </>);
}

function Loading() {
  return <div className="space-y-2.5 px-4 py-4" role="status" aria-label="Loading"><span className="sk block h-3 w-1/2 rounded" /><span className="sk block h-3 w-1/3 rounded" /></div>;
}

function ShopsView({ me, onOpenShop }: { me: Me; onOpenShop: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [segment, setSegment] = useState("");
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ items: Shop[]; total: number } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const t = setTimeout(() => {
      const p = new URLSearchParams({ limit: "50", offset: String(page * 50) });
      if (q.trim()) p.set("q", q.trim());
      if (segment) p.set("segment", segment);
      if (mine) p.set("mine", "true");
      adminApi.get<{ items: Shop[]; total: number }>(`/tenants?${p}`).then(setData).catch((e) => setErr(e.message));
    }, 250);
    return () => clearTimeout(t);
  }, [q, segment, mine, page]);
  const showBilling = me.field_levels.billing && me.field_levels.billing !== "hidden";
  const cols = showBilling ? 7 : 6;
  return (<>
    <PanelHeader title="Shops" sub="Every shop on Tekly. Open one to see its details and act on it." />
    <Toolbar
      filters={<>
        <div className="w-40"><Select value={segment} onChange={(e) => { setSegment(e.target.value); setPage(0); }} aria-label="Stage">
          <option value="">All stages</option>{Object.entries(SEGMENTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select></div>
        <label className="flex h-9 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-sm text-ink-secondary">
          <input type="checkbox" checked={mine} onChange={(e) => { setMine(e.target.checked); setPage(0); }} />My book</label>
      </>}
      search={<Input value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} placeholder="Search name, email, phone or TK-7K4M-92Q"
        className="sm:w-80" aria-label="Search shops" />} />
    <AdminCard>
      <div className="overflow-x-auto">
        <table className={tableClass}>
          <thead><tr className={theadRowClass}>
            <Th>Shop</Th><Th>Account ID</Th><Th>Stage</Th><Th>Plan</Th><Th>Country</Th><Th>Looked after by</Th>
            {showBilling && <Th num>Discount</Th>}
          </tr></thead>
          <tbody className="text-sm">
            {!data && <SkeletonRows cols={cols} rows={5} />}
            {data && data.items.length === 0 && <TableEmpty cols={cols} title="No shops match" body="Try another search or stage." />}
            {data?.items.map((s) => (
              <tr key={s.id} {...clickableRow(() => onOpenShop(s.id))} className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <Td className="font-semibold">{s.name}</Td>
                <Td className="font-mono text-xs">{s.account_id}</Td>
                <Td>{SEGMENTS[s.segment] || s.segment}</Td>
                <Td className="capitalize">{s.plan}</Td>
                <Td>{s.country || "—"}</Td>
                <Td className="text-ink-secondary">{s.assigned_to || "Nobody"}</Td>
                {showBilling && <Td num>{s.discount_pct !== undefined ? (s.discount_pct ? `${s.discount_pct}%` : "—") : ""}</Td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AdminCard>
    {data && data.total > 50 && (
      <div className="flex items-center gap-3 text-sm">
        <Button size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</Button>
        <Button size="sm" disabled={(page + 1) * 50 >= data.total} onClick={() => setPage((p) => p + 1)}>Next</Button>
        <span className="tabular-nums text-ink-tertiary">{page * 50 + 1}–{Math.min((page + 1) * 50, data.total)} of {data.total}</span>
      </div>
    )}
    {err && <p role="alert" className="text-sm text-rose-600">{err}</p>}
  </>);
}
