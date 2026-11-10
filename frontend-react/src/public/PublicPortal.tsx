import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ChevronRight, ShieldCheck, X } from "lucide-react";
import Button from "../components/Button";
import EmptyState from "../components/EmptyState";
import { TrackView, usePublicDoc, money, PaidNote } from "./PublicTrack";
import PublicLayout, { PublicTitle, publicCard, shopAccent } from "./PublicLayout";

// ── Customer portal: every job of one customer, behind their magic link ────────
// Served at /portal/:token, no auth, no shell. The job list is this page's own; opening a job
// shows the very same view as the track page (TrackView), so approvals, recommended work,
// messages and the rest work identically. The open job lives in ?job=<id>, so the phone's
// back button returns to the list and a job can be linked to directly.

type Card = {
  id: string; order_number: string; is_estimate: boolean; stage_label: string;
  total: number; total_converted?: number; currency_symbol?: string;
  complaint?: string | null; device?: string | null; created_at?: string | null;
  warranty_until?: string | null; awaiting_approval?: boolean; open_recommendations?: number;
  amount_paid?: number; balance_due?: number; paid_in_full?: boolean;
};
type Home = {
  shop_name?: string; client_name?: string; logo_data?: string | null; brand_color?: string | null;
  work_orders: Card[]; estimates: Card[];
};

const jobFromUrl = () => new URLSearchParams(location.search).get("job");
const fmtDay = (s?: string | null) =>
  s ? new Date(s.slice(0, 10) + "T00:00:00").toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "";
const needsYou = (c: Card) => !!c.awaiting_approval || (c.open_recommendations || 0) > 0;

export default function PublicPortal({ token }: { token: string }) {
  const base = "/api/public/portal/" + token;
  const [home, setHome] = useState<Home | null>(null);
  const [err, setErr] = useState(false);
  const [job, setJob] = useState<string | null>(jobFromUrl());
  const fromList = useRef(false);   // the open job was pushed from the list: Back is history.back()

  async function loadHome() {
    try {
      const r = await fetch(base);
      if (!r.ok) throw new Error();
      setHome(await r.json());
    } catch { setErr(true); }
  }
  useEffect(() => { if (token) loadHome(); else setErr(true); }, []);
  useEffect(() => {
    const onPop = () => {
      const j = jobFromUrl();
      fromList.current = false;
      setJob(j);
      if (!j) loadHome();
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function open(id: string | null, replace = false) {
    const url = location.pathname + (id ? "?job=" + encodeURIComponent(id) : "");
    history[replace ? "replaceState" : "pushState"](null, "", url);
    if (!replace) fromList.current = !!id;
    setJob(id);
    window.scrollTo(0, 0);
    if (!id) loadHome();   // back on the list: show what changed while the job was open
  }

  if (err)
    return (
      <PublicLayout title="Your repairs">
        <div className={publicCard}>
          <EmptyState size="page" icon={<X size={20} />} title="Link not found" body="This link to your repairs may be invalid or expired. Ask the shop for a new one." />
        </div>
      </PublicLayout>
    );
  if (!home) return <PublicLayout shop={null}><span className="sr-only" role="status">Loading…</span></PublicLayout>;
  if (job) return <PortalJob key={job} base={base} id={job} home={home} onBack={() => (fromList.current ? history.back() : open(null))} onOpen={(id) => open(id, true)} />;
  return <PortalHome home={home} onOpen={open} />;
}

function PortalHome({ home, onOpen }: { home: Home; onOpen: (id: string) => void }) {
  const accent = shopAccent(home.brand_color);
  const all = [...home.estimates, ...home.work_orders];
  const todo = all.filter(needsYou);
  const rest = (list: Card[]) => list.filter((c) => !needsYou(c));
  const firstName = (home.client_name || "").trim().split(/\s+/)[0];
  useEffect(() => { document.title = home.shop_name ? `Your repairs · ${home.shop_name}` : "Your repairs"; }, [home.shop_name]);

  return (
    <PublicLayout shop={{ name: home.shop_name, logo: home.logo_data }}>
        <PublicTitle title={firstName ? `Hi ${firstName}` : "Your repairs"}
          sub={all.length === 0 ? `You don't have any jobs with ${home.shop_name || "us"} yet.`
            : todo.length ? `${todo.length === 1 ? "One job needs" : `${todo.length} jobs need`} your attention.`
            : `Everything you have with ${home.shop_name || "us"}, in one place.`} />

        {todo.length > 0 && <JobList title="Needs your attention" cards={todo} accent={accent} onOpen={onOpen} />}
        {rest(home.estimates).length > 0 && <JobList title="Estimates" cards={rest(home.estimates)} accent={accent} onOpen={onOpen} />}
        {rest(home.work_orders).length > 0 && <JobList title="Repairs" cards={rest(home.work_orders)} accent={accent} onOpen={onOpen} />}
    </PublicLayout>
  );
}

function JobList({ title, cards, accent, onOpen }: { title: string; cards: Card[]; accent: string; onOpen: (id: string) => void }) {
  return (
    <section className="mt-10">
      <h2 className="mb-3 text-title font-semibold text-ink-primary">{title}</h2>
      <ul className={`${publicCard} overflow-hidden divide-y divide-slate-100`}>
        {cards.map((c) => {
          const what = c.device || c.complaint || (c.is_estimate ? "Estimate" : "Repair");
          const ask = c.awaiting_approval ? (c.is_estimate ? "Ready for you to review" : "Waiting for your approval")
            : (c.open_recommendations || 0) > 0 ? (c.open_recommendations === 1 ? "1 recommendation to choose" : `${c.open_recommendations} recommendations to choose`)
            : null;
          return (
            <li key={c.id}>
              <button type="button" onClick={() => onOpen(c.id)} className="flex w-full items-center gap-4 px-4 py-4 text-left hover:bg-slate-50 sm:px-5">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-base font-semibold text-ink-primary">{what}</span>
                  <span className="mt-0.5 block text-sm text-ink-tertiary">
                    {[`${c.is_estimate ? "Estimate" : "Job"} ${c.order_number}`, c.stage_label, fmtDay(c.created_at)].filter(Boolean).join(" · ")}
                  </span>
                  {ask && <span className="mt-1.5 inline-flex rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800">{ask}</span>}
                  {c.warranty_until && <span className="mt-1.5 flex items-center gap-1 text-sm text-emerald-700"><ShieldCheck size={14} /> Under warranty until {fmtDay(c.warranty_until)}</span>}
                </span>
                <span className="flex flex-col items-end gap-1">
                  <span className="text-base font-semibold tabular-nums text-ink-primary">{money(c.total_converted ?? c.total, c.currency_symbol || "$")}</span>
                  {c.paid_in_full ? <PaidNote paidInFull sym={c.currency_symbol || "$"} />
                    : (c.amount_paid || 0) > 0 && (c.balance_due || 0) > 0
                      ? <span className="text-sm font-semibold tabular-nums text-amber-700">{money(c.balance_due || 0, c.currency_symbol || "$")} due</span> : null}
                </span>
                <ChevronRight size={18} className="shrink-0" style={{ color: accent }} />
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function PortalJob({ base, id, home, onBack, onOpen }: {
  base: string; id: string; home: Home; onBack: () => void; onOpen: (id: string) => void;
}) {
  const api = base + "/order/" + encodeURIComponent(id);
  const { d, err, updatedAt, load } = usePublicDoc(api);
  const back = (
    <div className="-ml-3.5 mb-4"><Button variant="ghost" onClick={onBack} icon={<ArrowLeft size={16} />}>All your repairs</Button></div>
  );
  const shop = { name: home.shop_name, logo: home.logo_data };

  if (err)
    return (
      <PublicLayout shop={shop}>
        {back}
        <PublicTitle title="We couldn't open this job" sub={`It may have been removed. Go back to see all your jobs with ${home.shop_name || "the shop"}.`} />
      </PublicLayout>
    );
  if (!d) return <PublicLayout shop={shop}>{back}<span className="sr-only" role="status">Loading…</span></PublicLayout>;
  const trackToken = (d as { public_token?: string | null }).public_token;
  return (
    <TrackView d={d} updatedAt={updatedAt} api={api} reload={load} top={back}
      extrasApi={trackToken ? "/api/public/track/" + trackToken : null}
      // An approved estimate becomes a work order: take the customer straight to it.
      onResponded={(_, j) => { if (j?.converted_order_id && j.converted_order_id !== id) onOpen(j.converted_order_id); }} />
  );
}
