import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ChevronRight, ShieldCheck, X } from "lucide-react";
import { TrackView, usePublicDoc, money, onColor, initials } from "./PublicTrack";

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
      <div className="min-h-screen bg-surface text-ink-primary grid place-items-center px-5">
        <div className="text-center">
          <div className="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-rose-50 text-rose-600"><X size={26} /></div>
          <div className="text-lg font-bold">Link not found</div>
          <div className="mt-1.5 text-sm text-ink-tertiary">This link to your repairs may be invalid or expired. Ask the shop for a new one.</div>
        </div>
      </div>
    );
  if (!home) return <div className="min-h-screen bg-surface" />;
  if (job) return <PortalJob key={job} base={base} id={job} home={home} onBack={() => (fromList.current ? history.back() : open(null))} onOpen={(id) => open(id, true)} />;
  return <PortalHome home={home} onOpen={open} />;
}

function PortalHome({ home, onOpen }: { home: Home; onOpen: (id: string) => void }) {
  const accent = home.brand_color || "#0059ba";
  const onAccent = onColor(accent);
  const all = [...home.estimates, ...home.work_orders];
  const todo = all.filter(needsYou);
  const rest = (list: Card[]) => list.filter((c) => !needsYou(c));
  const firstName = (home.client_name || "").trim().split(/\s+/)[0];
  useEffect(() => { document.title = home.shop_name ? `Your repairs · ${home.shop_name}` : "Your repairs"; }, [home.shop_name]);

  return (
    <div className="min-h-screen bg-canvas text-ink-primary font-sans">
      <div className="mx-auto max-w-[720px] px-4 py-6 sm:py-10">
        {/* shop bar */}
        <div className="flex items-center gap-3">
          {home.logo_data
            ? <img src={home.logo_data} alt={home.shop_name || "logo"} className="h-9 w-9 shrink-0 rounded-[10px] object-contain bg-white" />
            : <div className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] text-[14px] font-semibold" style={{ background: accent, color: onAccent }}>{initials(home.shop_name)}</div>}
          <div className="min-w-0 flex-1 truncate text-[15px] font-semibold text-ink-primary">{home.shop_name}</div>
        </div>

        <div className="mt-8">
          <h1 className="text-[24px] sm:text-[28px] font-semibold leading-tight tracking-[-0.02em] text-ink-primary">{firstName ? `Hi ${firstName}` : "Your repairs"}</h1>
          <p className="mt-1.5 text-[15px] text-ink-tertiary">
            {all.length === 0 ? `You don't have any jobs with ${home.shop_name || "us"} yet.`
              : todo.length ? `${todo.length === 1 ? "One job needs" : `${todo.length} jobs need`} your attention.`
              : `Everything you have with ${home.shop_name || "us"}, in one place.`}
          </p>
        </div>

        {todo.length > 0 && <JobList title="Needs your attention" cards={todo} accent={accent} onOpen={onOpen} />}
        {rest(home.estimates).length > 0 && <JobList title="Estimates" cards={rest(home.estimates)} accent={accent} onOpen={onOpen} />}
        {rest(home.work_orders).length > 0 && <JobList title="Repairs" cards={rest(home.work_orders)} accent={accent} onOpen={onOpen} />}
      </div>
      <div className="py-8 text-center text-[12px] text-ink-tertiary">{home.shop_name} · Sent with RepairOS</div>
    </div>
  );
}

function JobList({ title, cards, accent, onOpen }: { title: string; cards: Card[]; accent: string; onOpen: (id: string) => void }) {
  return (
    <section className="mt-10">
      <h2 className="mb-3 text-[17px] font-semibold text-ink-primary">{title}</h2>
      <ul className="overflow-hidden rounded-2xl border border-slate-200 bg-white divide-y divide-slate-100">
        {cards.map((c) => {
          const what = c.device || c.complaint || (c.is_estimate ? "Estimate" : "Repair");
          const ask = c.awaiting_approval ? (c.is_estimate ? "Ready for you to review" : "Waiting for your approval")
            : (c.open_recommendations || 0) > 0 ? (c.open_recommendations === 1 ? "1 recommendation to choose" : `${c.open_recommendations} recommendations to choose`)
            : null;
          return (
            <li key={c.id}>
              <button type="button" onClick={() => onOpen(c.id)} className="flex w-full items-center gap-4 px-4 py-4 text-left hover:bg-slate-50 sm:px-5">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[15px] font-semibold text-ink-primary">{what}</span>
                  <span className="mt-0.5 block text-[13px] text-ink-tertiary">
                    {[`${c.is_estimate ? "Estimate" : "Job"} ${c.order_number}`, c.stage_label, fmtDay(c.created_at)].filter(Boolean).join(" · ")}
                  </span>
                  {ask && <span className="mt-1.5 inline-flex rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-[12px] font-medium text-amber-800">{ask}</span>}
                  {c.warranty_until && <span className="mt-1.5 flex items-center gap-1 text-[13px] text-emerald-700"><ShieldCheck size={14} /> Under warranty until {fmtDay(c.warranty_until)}</span>}
                </span>
                <span className="text-[15px] font-semibold tabular-nums text-ink-primary">{money(c.total_converted ?? c.total, c.currency_symbol || "$")}</span>
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
    <button type="button" onClick={onBack} className="mb-6 inline-flex items-center gap-1.5 text-[14px] font-medium text-ink-tertiary hover:text-ink-primary">
      <ArrowLeft size={16} /> All your repairs
    </button>
  );

  if (err)
    return (
      <div className="min-h-screen bg-canvas text-ink-primary font-sans">
        <div className="mx-auto max-w-[720px] px-4 py-6 sm:py-10">
          {back}
          <div className="text-lg font-bold">We couldn't open this job</div>
          <div className="mt-1.5 text-sm text-ink-tertiary">It may have been removed. Go back to see all your jobs with {home.shop_name || "the shop"}.</div>
        </div>
      </div>
    );
  if (!d) return <div className="min-h-screen bg-canvas" />;
  const trackToken = (d as { public_token?: string | null }).public_token;
  return (
    <TrackView d={d} updatedAt={updatedAt} api={api} reload={load} top={back}
      extrasApi={trackToken ? "/api/public/track/" + trackToken : null}
      // An approved estimate becomes a work order: take the customer straight to it.
      onResponded={(_, j) => { if (j?.converted_order_id && j.converted_order_id !== id) onOpen(j.converted_order_id); }} />
  );
}
