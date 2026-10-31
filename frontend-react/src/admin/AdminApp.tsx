import { useCallback, useEffect, useState } from "react";
import { adminApi, whenSignedOut, SEGMENTS, fmtDate, type Me, type Shop } from "./api";
import SignIn from "./SignIn";
import ShopPanel from "./ShopPanel";
import { TeamView, CodesView, LogView } from "./Views";
import { clickableRow } from "../lib/clickable";

type View = "today" | "shops" | "codes" | "team" | "log";
const card = "bg-white rounded-xl border border-slate-200";

/** When a trial stops: the billing trial clock or a hand-set term end, whichever comes first from today. */
function endsOn(s: Shop): string | null {
  const today = new Date().toISOString().slice(0, 10);
  return [s.trial_ends_at, s.sub_end].filter((d): d is string => !!d && d >= today).sort()[0] || null;
}

export default function AdminApp() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const load = useCallback(() => adminApi.get<Me>("/me").then(setMe).catch(() => setMe(null)), []);
  useEffect(() => { whenSignedOut(() => setMe(null)); load(); }, [load]);
  if (me === undefined) return <div className="min-h-screen grid place-items-center text-sm text-ink-tertiary">Loading…</div>;
  if (me === null) return <SignIn onSignedIn={load} />;
  return <Shell me={me} onSignOut={async () => { try { await adminApi.post("/logout"); } catch { /* already out */ } setMe(null); }} />;
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
    <div className="min-h-screen bg-slate-50 text-ink-primary">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-6">
          <span className="font-semibold">Tekly admin</span>
          <nav className="flex gap-1 overflow-x-auto">
            {nav.filter(([, , ok]) => ok).map(([k, label]) => (
              <button key={k} onClick={() => setView(k)}
                className={`px-3 py-1.5 rounded-lg text-sm whitespace-nowrap ${view === k ? "bg-slate-100 font-semibold" : "text-ink-secondary hover:text-ink-primary"}`}>{label}</button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="hidden sm:inline text-ink-secondary">{me.name} · {me.role.name}</span>
            <button onClick={onSignOut} className="text-ink-secondary hover:text-ink-primary">Sign out</button>
          </div>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 py-6">
        {view === "today" && <TodayView me={me} onOpenShop={setOpenShop} />}
        {view === "shops" && <ShopsView me={me} onOpenShop={setOpenShop} />}
        {view === "codes" && <CodesView />}
        {view === "team" && <TeamView me={me} />}
        {view === "log" && <LogView />}
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
  const [reqs, setReqs] = useState<Req[]>([]);
  const [ending, setEnding] = useState<Shop[]>([]);
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
  return (
    <div className="space-y-6">
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[["Paying", stats.segments.paid], ["On trial", stats.segments.new + stats.segments.trial], ["In your book", stats.my_book],
            [approver ? "Waiting for you" : "Suspended", approver ? stats.pending_requests ?? 0 : stats.segments.suspended]].map(([l, v]) => (
            <div key={l as string} className="bg-white rounded-xl border border-slate-200 p-4"><div className="text-xs text-ink-tertiary">{l}</div><div className="text-2xl font-semibold mt-1 tabular-nums">{v}</div></div>
          ))}
        </div>
      )}
      <section className={card}>
        <h2 className="px-4 py-3 border-b border-slate-100 text-sm font-semibold">{approver ? "Waiting for your OK" : "Your requests"}</h2>
        {reqs.length === 0 && <p className="px-4 py-4 text-sm text-ink-tertiary">{approver ? "Nothing is waiting for you." : "You have no open requests."}</p>}
        {reqs.map((r) => (
          <div key={r.id} className="px-4 py-3 border-b border-slate-100 last:border-0 flex flex-wrap items-center gap-3 text-sm">
            <button onClick={() => onOpenShop(r.org_id)} className="font-semibold hover:underline">{r.shop}</button>
            <span>{describe(r)}</span>
            <span className="text-ink-tertiary">{r.requested_by} · {r.reason}</span>
            {approver && r.status === "pending" ? (
              <span className="ml-auto flex gap-2">
                <button onClick={() => decide(r.id, "decline")} className="px-3 py-1 rounded-lg border border-slate-200 text-xs font-semibold">Decline</button>
                <button onClick={() => decide(r.id, "approve")} className="px-3 py-1 rounded-lg bg-primary text-white text-xs font-semibold">Approve</button>
              </span>
            ) : <span className="ml-auto text-xs text-ink-tertiary">{r.status === "expired" ? "Expired" : "Waiting"}</span>}
          </div>
        ))}
      </section>
      <section className={card}>
        <h2 className="px-4 py-3 border-b border-slate-100 text-sm font-semibold">Trials ending this week</h2>
        {ending.length === 0 && <p className="px-4 py-4 text-sm text-ink-tertiary">No trials end in the next 7 days.</p>}
        {ending.map((s) => (
          <button key={s.id} onClick={() => onOpenShop(s.id)} className="w-full text-left px-4 py-3 border-b border-slate-100 last:border-0 flex gap-3 text-sm hover:bg-slate-50">
            <span className="font-semibold">{s.name}</span><span className="text-ink-tertiary">{s.country || ""}</span>
            <span className="ml-auto text-amber-700">Ends {fmtDate(endsOn(s))}</span>
          </button>
        ))}
      </section>
      {err && <p role="alert" className="text-sm text-rose-600">{err}</p>}
    </div>
  );
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
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <input value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} placeholder="Search name, email, phone or TK-7K4M-92Q"
          className="flex-1 min-w-[220px] px-3 py-2 rounded-lg border border-slate-300 text-sm bg-white" aria-label="Search shops" />
        <select value={segment} onChange={(e) => { setSegment(e.target.value); setPage(0); }} className="px-3 py-2 rounded-lg border border-slate-300 text-sm bg-white" aria-label="Stage">
          <option value="">All stages</option>{Object.entries(SEGMENTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <label className="flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-300 text-sm bg-white">
          <input type="checkbox" checked={mine} onChange={(e) => { setMine(e.target.checked); setPage(0); }} />My book</label>
      </div>
      <div className={`${card} overflow-x-auto`}>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-ink-tertiary border-b border-slate-100">
            <th className="px-4 py-2 font-semibold">Shop</th><th className="px-4 py-2 font-semibold">Account ID</th>
            <th className="px-4 py-2 font-semibold">Stage</th><th className="px-4 py-2 font-semibold">Plan</th>
            <th className="px-4 py-2 font-semibold">Country</th><th className="px-4 py-2 font-semibold">Looked after by</th>
            {showBilling && <th className="px-4 py-2 font-semibold">Discount</th>}
          </tr></thead>
          <tbody>
            {data?.items.map((s) => (
              <tr key={s.id} {...clickableRow(() => onOpenShop(s.id))} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 cursor-pointer">
                <td className="px-4 py-2.5 font-semibold">{s.name}</td>
                <td className="px-4 py-2.5 font-mono text-xs">{s.account_id}</td>
                <td className="px-4 py-2.5">{SEGMENTS[s.segment] || s.segment}</td>
                <td className="px-4 py-2.5 capitalize">{s.plan}</td>
                <td className="px-4 py-2.5">{s.country || "—"}</td>
                <td className="px-4 py-2.5 text-ink-secondary">{s.assigned_to || "Nobody"}</td>
                {showBilling && <td className="px-4 py-2.5 tabular-nums">{s.discount_pct !== undefined ? (s.discount_pct ? `${s.discount_pct}%` : "—") : ""}</td>}
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.items.length === 0 && <p className="px-4 py-6 text-sm text-ink-tertiary">No shops match.</p>}
      </div>
      {data && data.total > 50 && (
        <div className="flex items-center gap-3 text-sm">
          <button disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="px-3 py-1 rounded-lg border border-slate-200 disabled:opacity-40">Previous</button>
          <span className="text-ink-tertiary">{page * 50 + 1}–{Math.min((page + 1) * 50, data.total)} of {data.total}</span>
          <button disabled={(page + 1) * 50 >= data.total} onClick={() => setPage((p) => p + 1)} className="px-3 py-1 rounded-lg border border-slate-200 disabled:opacity-40">Next</button>
        </div>
      )}
      {err && <p role="alert" className="text-sm text-rose-600">{err}</p>}
    </div>
  );
}
