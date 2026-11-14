import { Fragment, useEffect, useRef, useState } from "react";
import {
  Check, X, Paperclip, Receipt, Download, MapPin, Clock, Phone, Star, ThumbsUp, ThumbsDown, ExternalLink, Mail, Hourglass,
} from "lucide-react";
import { safeFileHref, safeHttpHref } from "../lib/safeFileHref";
import { buttonClass, IconButton } from "../components/Button";
import EmptyState from "../components/EmptyState";
import PublicLayout, { accentFill, onColor, shopAccent, initials, publicCard, textareaCls, inputCls, fieldLabelCls } from "./PublicLayout";

export { onColor, initials };

// ── Public customer page: order tracking + estimate + e-sign ──────────────
// A real part of the React app — NOT the legacy vanilla-JS track.html. Served at /track/:token,
// no auth, no shell: the shared customer-page frame (PublicLayout), always light.

type Line = { id?: string; total?: number; group?: string | null; label: string; qty?: number; kind?: string; line_type?: string; amount: number; item?: string | null };

// Several items per job: recommendations grouped item by item (job's item order), whole job last.
function byItem(recs: Rec[]): { item: string | null; recs: Rec[] }[] {
  const out: { item: string | null; recs: Rec[] }[] = [];
  const sorted = [...recs].sort((a, b) => (a.item ? (a.item_order ?? 0) : 1e9) - (b.item ? (b.item_order ?? 0) : 1e9));
  for (const r of sorted) {
    const k = r.item || null;
    const g = out.find((x) => x.item === k);
    if (g) g.recs.push(r); else out.push({ item: k, recs: [r] });
  }
  return out;
}
type Rec = {
  id: string; title: string; note?: string; line_total: number; qty?: number; unit_price?: number; status: string;
  media_data?: string; item?: string | null; item_order?: number | null; kind?: string;
  urgency?: "now" | "soon" | null; decline_reason?: string | null; decided_by_name?: string | null; decided_at?: string | null;
};
// Urgency in plain words (never colour alone, never alarmist). Order: now, then soon, then the rest.
const URGENCY: Record<string, { label: string; cls: string }> = {
  now: { label: "Needs attention now", cls: "bg-amber-50 text-amber-800 border-amber-200" },
  soon: { label: "Can wait until your next visit", cls: "bg-slate-100 text-ink-secondary border-slate-200" },
};
const urgencyRank = (r: Rec) => (r.urgency === "now" ? 0 : r.urgency === "soon" ? 1 : 2);
type Update = { type: string; body?: string; media_data?: string; media_name?: string; at?: string };
type TL = { label: string; at?: string };
type Feedback = {
  system: string; heading?: string; subheading?: string; question?: string;
  clarify_question?: string; bands?: Record<string, string>;
  google_review_url?: string | null; submitted?: boolean; rating?: number | null;
};
export type Track = {
  shop_name?: string; client_name?: string | null; order_number?: string; stage_label?: string; is_estimate?: boolean;
  stages?: { label: string }[]; stage_index?: number; stopped?: boolean; awaiting_approval?: boolean;
  device?: string; asset_label?: string; complaint?: string; total?: number;
  amount_paid?: number; balance_due?: number; paid_in_full?: boolean;   // customer currency
  total_converted?: number; fx_rate?: number; currency_symbol?: string;
  lines?: Line[]; recommendations?: Rec[]; updates?: Update[]; timeline?: TL[];
  custom_fields?: { label: string; value: string }[];   // fields the shop set to show on the portal
  invoice?: any; has_signature?: boolean; allow_messages?: boolean; messages_until?: string | null; can_new_request?: boolean; allow_recommend?: boolean;
  // 144ix — branding + pickup + review
  logo_data?: string | null; brand_color?: string | null; website?: string | null;
  shop_address?: string | null; shop_phone?: string | null; location_name?: string | null;
  shop_hours?: Record<string, { open?: string; close?: string; closed?: boolean }> | null;
  feedback?: Feedback | null;
  // 144dr — only sent when the shop turned on "show the scheduled date" (server-side gate)
  scheduled_for?: string | null; scheduled_to?: string | null;
  // E5 — estimate terms: valid until, the version, picking lines, the deposit asked at approval
  valid_until?: string | null; expired?: boolean; revision?: number | null; can_choose_lines?: boolean;
  deposit?: { amount: number; paid: number; due: number; can_pay: boolean } | null;
};

const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
function todayHours(h?: Track["shop_hours"]) {
  if (!h) return null;
  const d = h[WEEK[(new Date().getDay() + 6) % 7]];
  if (!d || d.closed) return "Closed today";
  if (d.open && d.close) return `Open today ${d.open}–${d.close}`;
  return null;
}

// One way to show money everywhere on the page: whole amounts without cents, otherwise two decimals.
export const money = (n: number, sym = "$") => {
  const v = Number(n || 0);
  const whole = Math.abs(v - Math.round(v)) < 0.005;
  return (v < 0 ? "−" : "") + sym + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 });
};
const docTotal = (d: Track) => (d?.fx_rate && Number(d.fx_rate) !== 1
  ? (d.total_converted != null ? d.total_converted : (d.total || 0) * d.fx_rate) : (d?.total || 0));
const fmtDoc = (d: Track) => money(docTotal(d), d?.currency_symbol || "$");
// Paid / balance due, in plain words beside every total, so a settled job never reads as owed.
export function PaidNote({ paid, balance, paidInFull, sym, className = "" }: {
  paid?: number; balance?: number; paidInFull?: boolean; sym: string; className?: string;
}) {
  if (paidInFull) return (
    <span className={"inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-sm font-semibold text-emerald-700 " + className}>
      <Check size={13} strokeWidth={2.5} /> Paid in full
    </span>
  );
  if ((paid || 0) > 0 && (balance || 0) > 0) return (
    <span className={"text-sm text-ink-secondary " + className}>
      Paid {money(paid || 0, sym)} · <span className="font-semibold text-amber-700">Balance due {money(balance || 0, sym)}</span>
    </span>
  );
  return null;
}

// The server sends UTC without a zone marker; read it as UTC so times show in the customer's own clock.
const asUtc = (s: string) => (/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z");
export const fmtDate = (s?: string) =>
  s ? new Date(asUtc(s)).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
// Scheduled visit for the Details list: one line for a single day ("Tue, Oct 21, 2:00 – 4:00 PM"),
// Start and End lines when the job spans several days. Empty when the shop doesn't share it.
// Scheduled times are the shop's wall-clock time (no zone), so they are shown exactly as the shop
// booked them, whatever the visitor's own timezone: read the digits as UTC and format in UTC.
const wall = (s: string) => new Date(s.replace(/([zZ]|[+-]\d\d:?\d\d)$/, "") + "Z");
export function scheduleLines(from?: string | null, to?: string | null): { label: string; value: string }[] {
  if (!from) return [];
  const a = wall(from);
  if (isNaN(a.getTime())) return [];
  const day: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" };
  const time: Intl.DateTimeFormatOptions = { hour: "numeric", minute: "2-digit", timeZone: "UTC" };
  const full = (d: Date) => `${d.toLocaleDateString([], day)}, ${d.toLocaleTimeString([], time)}`;
  const b = to ? wall(to) : null;
  if (!b || isNaN(b.getTime()) || b.getTime() <= a.getTime()) return [{ label: "Scheduled for", value: full(a) }];
  // an end at exactly midnight ("until end of day") still belongs to the start's day
  const endDay = b.getUTCHours() === 0 && b.getUTCMinutes() === 0 && b.getTime() - a.getTime() <= 86_400_000 ? new Date(b.getTime() - 1) : b;
  if (a.toISOString().slice(0, 10) === endDay.toISOString().slice(0, 10))
    return [{ label: "Scheduled for", value: `${full(a)} – ${b.toLocaleTimeString([], time)}` }];
  return [{ label: "Start", value: full(a) }, { label: "End", value: full(b) }];
}
// 144ct — sender label for the two-way message items in the updates feed
const UPD_SENDER: Record<string, string> = { shop_message: "Shop", client_message: "You" };

// Owner decision 8: 90 days after the job closed its link answers 410 with the shop's contact only.
export type ExpiredLink = { shop: { name?: string | null; phone?: string | null; email?: string | null } };

// Load one public document and keep it fresh: poll every 20s, pause while the tab is hidden.
export function usePublicDoc(url: string) {
  const [d, setD] = useState<Track | null>(null);
  const [err, setErr] = useState(false);
  const [expired, setExpired] = useState<ExpiredLink | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number>(0);
  const urlRef = useRef(url);
  urlRef.current = url;

  async function load(silent = false) {
    const at = urlRef.current;
    try {
      const r = await fetch(at);
      if (r.status === 410) {
        const body = await r.json().catch(() => null);
        if (body?.expired && urlRef.current === at) { setExpired({ shop: body.shop || {} }); setD(null); return; }
      }
      if (!r.ok) throw new Error();
      const data: Track = await r.json();
      if (urlRef.current !== at) return;   // the visitor moved on to another job meanwhile
      setD(data);
      setErr(false);
      setExpired(null);
      setUpdatedAt(Date.now());
    } catch { if (!silent && urlRef.current === at) setErr(true); }
  }
  useEffect(() => { setD(null); setErr(false); setExpired(null); load(); }, [url]);
  useEffect(() => {
    let id: ReturnType<typeof setInterval> | null = null;
    const start = () => { if (!id) id = setInterval(() => load(true), 20000); };
    const stop = () => { if (id) { clearInterval(id); id = null; } };
    const onVis = () => (document.hidden ? stop() : (load(true), start()));
    start();
    document.addEventListener("visibilitychange", onVis);
    return () => { stop(); document.removeEventListener("visibilitychange", onVis); };
  }, []);
  return { d, err, expired, updatedAt, load };
}

export default function PublicTrack() {
  const token = location.pathname.split("/track/")[1]?.replace(/\/$/, "") || "";
  const api = "/api/public/track/" + token;
  const { d, err, expired, updatedAt, load } = usePublicDoc(api);

  if (expired) return <ExpiredTrack shop={expired.shop} />;
  if (err)
    return (
      <PublicLayout title="Track your repair">
        <div className={publicCard}>
          <EmptyState size="page" icon={<X size={20} />} title="Order not found" body="This tracking link may be invalid or expired."
            action={<a href="/track" className={buttonClass("secondary", "lg")}>Look up your repair</a>} />
        </div>
      </PublicLayout>
    );
  if (!d) return <PublicLayout shop={null}><span className="sr-only" role="status">Loading…</span></PublicLayout>;
  return <TrackView d={d} updatedAt={updatedAt} api={api} extrasApi={api} reload={load} />;
}

// The link ran out (90 days after the job closed): say so kindly and give the shop's contact.
function ExpiredTrack({ shop }: { shop: ExpiredLink["shop"] }) {
  const contact = (shop.phone || shop.email) && (
    <div className="flex flex-wrap justify-center gap-2">
      {shop.phone && <a href={`tel:${shop.phone}`} className={buttonClass("primary", "lg")}><Phone size={15} /> {shop.phone}</a>}
      {shop.email && <a href={`mailto:${shop.email}`} className={buttonClass("secondary", "lg")}><Mail size={15} /> {shop.email}</a>}
    </div>
  );
  return (
    <PublicLayout shop={{ name: shop.name || "Repair shop" }}>
      <div className={publicCard}>
        <EmptyState size="page" icon={<Hourglass size={20} />} title="This link has expired"
          body="Tracking links stop working some time after a repair is finished. If you need anything about this repair, please contact the shop."
          action={contact || undefined} />
      </div>
    </PublicLayout>
  );
}

// One job as the customer sees it: status, recommendations, estimate, updates, invoice,
// messages, rating. Shared by the track page and the customer portal's job view.
// `api` takes respond / message / recommendations; `extrasApi` (the job's track link) takes
// the rating, new request, signed PDF and e-invoice, and those parts hide without it.
const fmtDay = (iso?: string | null) => (iso ? new Date(iso + "T00:00:00").toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" }) : "");

export function TrackView({ d, updatedAt, api, extrasApi, reload, onResponded, top }: {
  d: Track; updatedAt: number; api: string; extrasApi: string | null;
  reload: (silent?: boolean) => void;
  onResponded?: (decision: string, result: { converted_order_id?: string | null }) => void;
  top?: React.ReactNode;
}) {
  const [signOpen, setSignOpen] = useState(false);
  const [declineOpen, setDeclineOpen] = useState(false);
  // E5 — lines the customer leaves out (line-by-line approval). Everything starts included.
  const [skip, setSkip] = useState<Record<string, boolean>>({});
  const [payBusy, setPayBusy] = useState(false);
  // Nothing is pre-ticked: the customer opts in, item by item.
  const [recSel, setRecSel] = useState<Record<string, boolean>>({});
  const [review, setReview] = useState(false);
  const [photo, setPhoto] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [reqSent, setReqSent] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  // review capture
  const [fbRating, setFbRating] = useState(0);
  const [fbComment, setFbComment] = useState("");
  const [fbBusy, setFbBusy] = useState(false);
  const [fbResult, setFbResult] = useState<{ message: string; google_review_url?: string | null } | null>(null);

  function flash(m: string) { setToast(m); setTimeout(() => setToast(null), 3200); }

  async function submitFeedback() {
    if (!fbRating) { flash("Pick a rating first"); return; }
    setFbBusy(true);
    try {
      const r = await fetch(extrasApi + "/feedback", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating: fbRating, comment: fbComment.trim() || null }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Could not submit");
      setFbResult({ message: j.message, google_review_url: j.google_review_url });
    } catch (e: any) { flash(e.message || "Could not submit your rating"); }
    finally { setFbBusy(false); }
  }

  async function respond(decision: string, signature: string | null = null, reason?: string) {
    const skip_line_ids = decision === "accept" && d.can_choose_lines ? Object.keys(skip).filter((k) => skip[k]) : [];
    try {
      const r = await fetch(api + "/respond", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, signature, reason: reason || undefined, skip_line_ids }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "");
      flash(decision === "accept" ? "Estimate approved — thank you!" : "Estimate declined");
      setTimeout(() => { setRecSel({}); setSkip({}); reload(); onResponded?.(decision, j); }, 800);
    } catch (e: any) { flash(e.message || "Could not submit your response"); }
  }

  // E5 — pay the deposit the shop asked for: opens the shop's payment page.
  async function payDeposit() {
    if (!extrasApi) return;
    setPayBusy(true);
    try {
      const r = await fetch(extrasApi + "/deposit", { method: "POST" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.url) throw new Error(typeof j.detail === "string" ? j.detail : "");
      window.location.href = j.url;
    } catch (e: any) { flash(e.message || "Couldn't open the payment page"); setPayBusy(false); }
  }

  // The customer's choice: add what they ticked, or save items for later. Nothing they don't
  // touch is declined behind their back.
  async function decideRecs(body: { approve_ids?: string[]; later_ids?: string[]; name?: string }, done: string) {
    try {
      const r = await fetch(api + "/recommendations/decide", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error();
      setRecSel({}); setReview(false);
      flash(done);
      reload(true);
    } catch { flash("Couldn't save your choice. Please try again."); }
  }

  async function sendMessage() {
    const body = msg.trim(); if (!body) return;
    try {
      const r = await fetch(api + "/message", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body }),
      });
      if (!r.ok) throw new Error();
      setMsg(""); flash("Message sent — the shop will see it");
    } catch { flash("Could not send your message"); }
  }

  async function sendNewRequest() {
    const body = msg.trim(); if (!body) return;
    try {
      const r = await fetch(extrasApi + "/new-request", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body }),
      });
      if (!r.ok) throw new Error();
      setMsg(""); setReqSent(true);
    } catch { flash("Could not send your request"); }
  }

  const isEst = d.is_estimate === true;
  const sym = d.currency_symbol || "$";
  const accent = shopAccent(d.brand_color);
  const onAccent = onColor(accent);
  const fill = accentFill(accent);
  const nStages = d.stages?.length ?? 6;
  const stageIdx = d.stage_index ?? 0;
  const sched = scheduleLines(d.scheduled_for, d.scheduled_to);
  const nearEnd = stageIdx >= (nStages - 2);   // "Ready for pickup" or later
  const hoursLine = todayHours(d.shop_hours);
  const mapHref = d.shop_address ? "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(d.shop_address) : null;
  const recs = d.allow_recommend !== false ? (d.recommendations || []) : [];
  const openRecs = recs.filter((x) => x.status === "sent").sort((a, b) => urgencyRank(a) - urgencyRank(b));
  const addedRecs = recs.filter((x) => x.status === "approved");
  const laterRecs = recs.filter((x) => x.status === "declined");
  const chosen = openRecs.filter((x) => recSel[x.id]);
  const recSum = chosen.reduce((a, x) => a + x.line_total, 0);
  const choosing = !!d.can_choose_lines;
  // a row's own total includes its tax, so the new total matches what the shop will charge
  const skippedSum = choosing ? (d.lines || []).filter((l) => l.id && skip[l.id]).reduce((a, l) => a + (l.total ?? l.amount ?? 0), 0) : 0;
  // a kit is approved or left out as a whole: unticking one of its rows unticks them all
  const toggleLine = (l: Line) => {
    if (!l.id) return;
    const off = !skip[l.id];
    const ids = l.group ? (d.lines || []).filter((x) => x.group === l.group && x.id).map((x) => x.id!) : [l.id];
    setSkip((s) => ({ ...s, ...Object.fromEntries(ids.map((i) => [i, off])) }));
  };
  const allSkipped = choosing && (d.lines || []).every((l) => l.id && skip[l.id]);
  const total = docTotal(d) - skippedSum;
  const dep = d.deposit || null;
  const canChange = !d.stopped && !nearEnd;     // saved items can still be added while the job is open
  const thing = d.device || (isEst ? "estimate" : "repair");

  // The status in plain words: "Your iPhone 14 is in repair."
  const headline = d.stopped ? `This ${isEst ? "estimate" : "job"} is closed`
    : d.awaiting_approval ? (d.is_estimate ? "Your estimate is ready for you to review" : "We need your approval to continue")
    : stageIdx >= nStages - 1 ? `Your ${thing} is done`
    : nearEnd ? `Your ${thing} is ready for pickup`
    : isEst ? `Your estimate for your ${d.device || "repair"}`
    : stageIdx === 0 ? `We've received your ${thing}`
    : (d.stage_label || "In progress");
  const ago = updatedAt ? `Updated ${new Date(updatedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "";

  const H2 = ({ children }: { children: React.ReactNode }) => <h2 className="mb-3 text-title font-semibold text-ink-primary">{children}</h2>;
  const sec = "mt-10 border-t border-slate-200 pt-8";
  // One button look (the shared Button, 44px): the main one in the shop's colour, the rest plain.
  const main = buttonClass("primary", "lg");
  const plain = buttonClass("secondary", "lg");
  const toggle = "inline-flex h-11 items-center justify-center gap-1.5 rounded-xl border bg-white px-4 text-sm font-semibold";
  const showBar = chosen.length > 0 || !!d.awaiting_approval || !!(dep?.can_pay && extrasApi) || !!(nearEnd && d.shop_phone);

  return (
    <PublicLayout shop={{ name: d.shop_name, logo: d.logo_data }} bottomSpace={showBar}
      right={<span className="text-sm tabular-nums text-ink-tertiary">{d.is_estimate ? "Estimate" : "Job"} {d.order_number}</span>}>
        <div className="min-w-0">
          {top}
          {/* status hero */}
          <div>
            {d.awaiting_approval && <span className="mb-3 inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800">Waiting for your approval</span>}
            <h1 className="text-2xl font-semibold leading-tight tracking-tight text-ink-primary sm:text-display">{headline}</h1>
            <p className="mt-1.5 text-base text-ink-tertiary">{d.stopped ? `${d.stage_label}. Contact ${d.shop_name || "the shop"} if you have questions.`
              : [isEst ? `Status: ${d.stage_label}` : (headline === d.stage_label ? d.device : d.stage_label), ago].filter(Boolean).join(" · ")}</p>
            {isEst && (d.valid_until || d.expired || (d.revision || 1) > 1) && (
              <p className={`mt-2 text-sm ${d.expired ? "font-semibold text-rose-600" : "text-ink-secondary"}`}>
                {d.expired ? `This estimate expired on ${fmtDay(d.valid_until)}. Contact ${d.shop_name || "the shop"} for an up-to-date one.`
                  : [d.valid_until ? `Valid until ${fmtDay(d.valid_until)}` : null, (d.revision || 1) > 1 ? `Revised (version ${d.revision})` : null].filter(Boolean).join(" · ")}
              </p>
            )}
            {dep && dep.due > 0 && (dep.can_pay || (isEst && d.awaiting_approval)) && (
              <p className="mt-1 text-sm text-ink-secondary">{dep.can_pay ? `Deposit to pay now: ${money(dep.due, sym)}` : `A deposit of ${money(dep.amount, sym)} is due when you approve.`}</p>
            )}
            {!d.stopped && !isEst && (d.stages || []).length > 0 && (
              <div className="mt-5">
                <div className="flex gap-1">
                  {(d.stages || []).map((_, i) => (
                    <span key={i} className="h-1 flex-1 rounded-full" style={{ background: i <= stageIdx ? accent : "rgb(var(--c-slate-200))" }} />
                  ))}
                </div>
                <div className="mt-2 hidden sm:flex">
                  {(d.stages || []).map((s, i) => (
                    <span key={i} className={`flex-1 pr-2 text-xs leading-snug ${i === stageIdx ? "font-semibold text-ink-primary" : "text-ink-tertiary"}`}>{s.label}</span>
                  ))}
                </div>
                <div className="mt-2 text-sm text-ink-tertiary sm:hidden">Step {stageIdx + 1} of {nStages} · {d.stages![stageIdx]?.label}</div>
              </div>
            )}
          </div>

          {/* the total at a glance; the actions live in the bar at the bottom */}
          <div className={`${publicCard} mt-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-2 p-5`}>
            <div>
              <div className="text-sm text-ink-tertiary">{d.awaiting_approval ? (isEst ? "Estimate total" : "Total due") : "Total"}</div>
              <div className="mt-1 text-display font-semibold tabular-nums text-ink-primary">{money(total + recSum, sym)}</div>
              {recSum > 0 && <div className="mt-1 text-sm text-ink-tertiary">{money(total, sym)} + {money(recSum, sym)} you've chosen</div>}
              {!recSum && <PaidNote paid={d.amount_paid} balance={d.balance_due} paidInFull={d.paid_in_full} sym={sym} className="mt-2 inline-flex" />}
            </div>
            <div className="flex items-center gap-1.5 text-sm text-ink-tertiary">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" /> Live · updates automatically
            </div>
          </div>

          {/* what's being repaired */}
          {(d.complaint || (d.custom_fields || []).length > 0 || sched.length > 0) && (
            <dl className="mt-8 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
              {d.device && <div><dt className="text-sm text-ink-tertiary">{d.asset_label || "Item"}</dt><dd className="text-base text-ink-primary">{d.device}</dd></div>}
              {sched.map((r) => <div key={r.label}><dt className="text-sm text-ink-tertiary">{r.label}</dt><dd className="text-base text-ink-primary">{r.value}</dd></div>)}
              {d.complaint && <div className="sm:col-span-2"><dt className="text-sm text-ink-tertiary">What you told us</dt><dd className="text-base text-ink-primary">{d.complaint}</dd></div>}
              {(d.custom_fields || []).map((f) => <div key={f.label}><dt className="text-sm text-ink-tertiary">{f.label}</dt><dd className="text-base text-ink-primary">{f.value}</dd></div>)}
            </dl>
          )}

          {/* recommendations: the customer chooses what to add */}
          {recs.length > 0 && (
            <section className={sec} id="recommendations">
              <div className="rounded-2xl border bg-white p-5 sm:p-6" style={{ borderColor: `color-mix(in srgb, ${accent} 30%, rgb(var(--c-line-200)))` }}>
                {openRecs.length > 0 && (<>
                  <h2 className="text-title font-semibold text-ink-primary">{openRecs.length === 1 ? "Your technician found one more thing" : `Your technician found ${openRecs.length} more things`}</h2>
                  <p className="mt-1 text-sm text-ink-tertiary">Add what you'd like us to do. Nothing is added unless you choose it.</p>
                  <div className="mt-5 flex flex-col gap-6">
                    {byItem(openRecs).map((g) => (
                      <div key={g.item || "_job"} className="flex flex-col gap-3">
                        {openRecs.some((x) => x.item) && <div className="text-sm font-semibold text-ink-secondary">{g.item ? `For your ${g.item}` : "For the whole job"}</div>}
                        {[...g.recs].sort((a, b) => urgencyRank(a) - urgencyRank(b)).map((r) => {
                          const on = !!recSel[r.id];
                          return (
                            <div key={r.id} className={`rounded-xl border transition-colors ${on ? "" : "border-slate-200"}`}
                              style={on ? { borderColor: accent, background: `color-mix(in srgb, ${accent} 5%, transparent)` } : undefined}>
                              <div className="flex gap-4 p-4">
                                {r.media_data && (
                                  <button type="button" onClick={() => setPhoto(r.media_data!)} aria-label={`See the photo for ${r.title}`}
                                    className="h-20 w-20 shrink-0 overflow-hidden rounded-lg border border-slate-200">
                                    <img src={r.media_data} alt="" className="h-full w-full object-cover" />
                                  </button>
                                )}
                                <div className="min-w-0 flex-1">
                                  {r.urgency && URGENCY[r.urgency] && <span className={`mb-1.5 inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${URGENCY[r.urgency].cls}`}>{URGENCY[r.urgency].label}</span>}
                                  <div className="text-base font-semibold text-ink-primary">{r.title}</div>
                                  {r.note && <p className="mt-0.5 text-sm leading-relaxed text-ink-secondary">{r.note}</p>}
                                  {r.media_data && <p className="mt-1 text-xs text-ink-tertiary">Photo taken by your technician</p>}
                                </div>
                                <div className="text-right">
                                  <div className="text-base font-semibold tabular-nums text-ink-primary">{money(r.line_total, sym)}</div>
                                  {(r.qty ?? 1) > 1 && <div className="text-xs tabular-nums text-ink-tertiary">{r.qty} × {money(r.unit_price || 0, sym)}</div>}
                                </div>
                              </div>
                              <button type="button" onClick={() => setRecSel((s) => ({ ...s, [r.id]: !on }))} aria-pressed={on}
                                className="flex h-12 w-full items-center justify-center gap-2 border-t text-base font-semibold rounded-b-xl"
                                style={on ? { borderColor: accent, background: accent, color: onAccent } : { borderColor: "rgb(var(--c-line-200))", color: accent }}>
                                {on ? <><Check size={17} /> Added</> : isEst ? "Add to my estimate" : "Add to my repair"}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    ))}
                  </div>

                  {/* running total */}
                  <div className="mt-6 rounded-xl bg-slate-50 p-4 text-base">
                    <div className="flex justify-between text-ink-secondary"><span>Your {isEst ? "estimate" : "repair"}</span><span className="tabular-nums">{money(total, sym)}</span></div>
                    <div className="mt-1 flex justify-between text-ink-secondary"><span>Extras you've chosen</span><span className="tabular-nums">{recSum ? `+ ${money(recSum, sym)}` : money(0, sym)}</span></div>
                    <div className="mt-2 flex justify-between border-t border-slate-200 pt-2 font-semibold text-ink-primary"><span>New total</span><span className="tabular-nums">{money(total + recSum, sym)}</span></div>
                  </div>
                  <div className="mt-4 flex flex-wrap items-center gap-3">
                    <button type="button" disabled={!chosen.length} onClick={() => setReview(true)}
                      className={`${main} disabled:opacity-40`} style={fill}>
                      {chosen.length ? `Review my choices · ${money(recSum, sym)}` : "Choose what to add"}
                    </button>
                    <button type="button" onClick={() => decideRecs({ later_ids: openRecs.map((x) => x.id) }, "Saved for later. You can add them any time while we have your repair.")}
                      className="text-sm font-medium text-ink-tertiary hover:text-ink-secondary underline-offset-4 hover:underline">Not now</button>
                  </div>
                </>)}

                {/* decided */}
                {addedRecs.length > 0 && (
                  <div className={openRecs.length ? "mt-8" : ""}>
                    <h3 className="text-base font-semibold text-ink-primary">Added to your repair</h3>
                    <ul className="mt-2 divide-y divide-slate-100">
                      {addedRecs.map((r) => (
                        <li key={r.id} className="flex items-center gap-3 py-2.5">
                          <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-emerald-50 text-emerald-700"><Check size={14} /></span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-base text-ink-primary">{r.title}</span>
                            {(r.decided_by_name || r.decided_at) && <span className="block text-xs text-ink-tertiary">Confirmed{r.decided_by_name ? ` by ${r.decided_by_name}` : ""}{r.decided_at ? `, ${fmtDate(r.decided_at)}` : ""}</span>}
                          </span>
                          <span className="text-base tabular-nums text-ink-primary">{money(r.line_total, sym)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {laterRecs.length > 0 && (
                  <div className={openRecs.length || addedRecs.length ? "mt-6" : ""}>
                    <h3 className="text-base font-semibold text-ink-primary">{canChange ? "Saved for later" : "Ask us about these next time"}</h3>
                    <ul className="mt-2 divide-y divide-slate-100">
                      {laterRecs.map((r) => (
                        <li key={r.id} className="flex items-center gap-3 py-2.5">
                          <span className="min-w-0 flex-1 text-base text-ink-secondary">{r.title}</span>
                          <span className="text-base tabular-nums text-ink-tertiary">{money(r.line_total, sym)}</span>
                          {canChange && <button type="button" onClick={() => decideRecs({ approve_ids: [r.id], name: d.client_name || undefined }, `${r.title} added to your repair`)}
                            className="text-sm font-semibold" style={{ color: accent }}>Add it</button>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* itemized estimate */}
          {d.lines && d.lines.length > 0 && (
            <section className={sec}>
              <H2>{isEst ? "Your estimate" : "What we're doing"}</H2>
              {choosing && <p className="-mt-1 mb-3 text-sm text-ink-secondary">Untick anything you don't want done. The total updates as you go.</p>}
              <table className="w-full border-collapse text-base">
                <thead>
                  <tr className="text-sm text-ink-tertiary">
                    {choosing && <th className="w-8 border-b border-slate-200 pb-2 text-left font-normal"><span className="sr-only">Include</span></th>}
                    <th className="border-b border-slate-200 pb-2 text-left font-normal">Description</th>
                    <th className="border-b border-slate-200 pb-2 text-right font-normal">Qty</th>
                    <th className="border-b border-slate-200 pb-2 text-right font-normal">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {d.lines.map((l, i) => (<Fragment key={i}>
                    {d.lines!.some((x) => x.item) && (i === 0 || (d.lines![i - 1].item || null) !== (l.item || null)) && (
                      <tr><td colSpan={choosing ? 4 : 3} className="pb-1 pt-4 text-sm font-semibold text-ink-secondary">{l.item || "Whole job"}</td></tr>
                    )}
                    <tr className={choosing && l.id && skip[l.id] ? "opacity-50" : ""}>
                      {choosing && (
                        <td className="border-b border-slate-100 py-3 align-top">
                          <input type="checkbox" checked={!(l.id && skip[l.id])} aria-label={`Include ${l.label}`}
                            onChange={() => toggleLine(l)}
                            className="mt-1 h-4 w-4" style={{ accentColor: accent }} />
                        </td>
                      )}
                      <td className={`border-b border-slate-100 py-3 align-top text-ink-primary ${choosing && l.id && skip[l.id] ? "line-through" : ""}`}>{l.label}</td>
                      <td className="border-b border-slate-100 py-3 text-right align-top tabular-nums text-ink-tertiary">{l.qty != null ? l.qty : 1}</td>
                      <td className="border-b border-slate-100 py-3 text-right align-top tabular-nums text-ink-primary">{money(l.amount, sym)}</td>
                    </tr>
                  </Fragment>))}
                </tbody>
              </table>
              <div className="mt-3 flex justify-end gap-10 text-title font-semibold text-ink-primary"><span>Total</span><span className="tabular-nums">{skippedSum ? money(total, sym) : fmtDoc(d)}</span></div>
              {allSkipped && <div className="mt-1.5 text-right text-sm text-rose-600">Keep at least one item, or decline the estimate.</div>}
              <div className="mt-1.5 flex justify-end"><PaidNote paid={d.amount_paid} balance={d.balance_due} paidInFull={d.paid_in_full} sym={sym} /></div>
            </section>
          )}

          {/* updates and history, newest first */}
          {((d.updates || []).length > 0 || (d.timeline || []).length > 0) && (
            <section className={sec}>
              <H2>Updates</H2>
              <ol className="relative ml-1 border-l border-slate-200">
                {[
                  ...(d.updates || []).map((u) => ({ at: u.at, kind: "u" as const, u })),
                  ...(d.timeline || []).map((t) => ({ at: t.at, kind: "t" as const, t })),
                ].sort((a, b) => String(b.at || "").localeCompare(String(a.at || ""))).map((e, i) => (
                  <li key={i} className="relative pb-5 pl-5 last:pb-0">
                    <span className="absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white" style={{ background: i === 0 ? accent : "rgb(var(--c-line-300))" }} />
                    {e.kind === "t" ? (
                      <div className="text-base text-ink-primary">{e.t.label}</div>
                    ) : (<>
                      {UPD_SENDER[e.u.type] && <div className="text-sm font-semibold text-ink-secondary">{UPD_SENDER[e.u.type]}</div>}
                      {e.u.body && <div className="text-base text-ink-primary">{e.u.body}</div>}
                      {(e.u.type === "photo" || e.u.type === "signature") && e.u.media_data && (
                        <button type="button" onClick={() => setPhoto(e.u.media_data!)} className="mt-2 block overflow-hidden rounded-lg border border-slate-200 bg-white">
                          <img src={e.u.media_data} alt="" className="h-32 w-auto max-w-[220px] object-cover" />
                        </button>
                      )}
                      {e.u.type === "file" && safeFileHref(e.u.media_data) && (
                        <a href={safeFileHref(e.u.media_data)} download={e.u.media_name || "file"} className="mt-1 inline-flex items-center gap-1.5 text-base font-semibold" style={{ color: accent }}><Paperclip size={15} /> {e.u.media_name || "Download file"}</a>
                      )}
                    </>)}
                    <div className="mt-0.5 text-sm text-ink-tertiary">{fmtDate(e.at)}</div>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {/* invoice */}
          {d.invoice && (
            <section className={sec}>
              <H2>Invoice</H2>
              <div className="flex items-baseline justify-between gap-3">
                <div>
                  <div className="text-base font-semibold tabular-nums text-ink-primary">{d.invoice.invoice_number || "Invoice"}</div>
                  <div className="text-sm capitalize text-ink-tertiary">{d.invoice.status || ""}</div>
                </div>
                <div className="text-right">
                  <div className="text-title font-semibold tabular-nums text-ink-primary">{money(d.invoice.total_converted != null ? d.invoice.total_converted : d.invoice.total || 0, sym)}</div>
                  {d.invoice.balance_due > 0
                    ? <div className="text-sm text-amber-700">Balance {money(d.invoice.balance_due, sym)}</div>
                    : <div className="text-sm text-emerald-700">Paid</div>}
                </div>
              </div>
              {d.invoice.einvoice_available && extrasApi && (
                <>
                  <a href={`${extrasApi}/einvoice.xml`} download className={`${plain} mt-3`}><Receipt size={15} /> Download e-invoice (XML)</a>
                  <div className="mt-1.5 text-sm text-ink-tertiary">Standards-based UBL 2.1 electronic invoice{d.invoice.einvoice_number ? ` · ${d.invoice.einvoice_number}` : ""}</div>
                </>
              )}
            </section>
          )}

          {/* message */}
          {d.allow_messages !== false && (
            <section className={sec}>
              <H2>Message {d.shop_name || "the shop"}</H2>
              {d.messages_until && (
                <p className="mb-3 text-sm text-ink-tertiary">Questions about this repair? You can message us until {new Date(d.messages_until).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}.</p>
              )}
              <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={3} placeholder="Ask a question or add a note…" aria-label="Your message"
                className={`${textareaCls} resize-y`} />
              <button onClick={sendMessage} disabled={!msg.trim()} className={`${main} mt-3`} style={fill}>Send</button>
            </section>
          )}
          {d.allow_messages === false && d.can_new_request && extrasApi && (
            <section className={sec}>
              <H2>Need something else?</H2>
              {reqSent ? (
                <div className="flex items-center gap-2 text-base font-semibold text-ink-primary"><Check size={16} className="text-emerald-600" /> Request sent. The shop will contact you.</div>
              ) : (<>
                <p className="mb-3 text-sm text-ink-tertiary">This repair is complete. Describe the new problem and we'll get back to you.</p>
                <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={3} placeholder="What's going on?" aria-label="The new problem"
                  className={`${textareaCls} resize-y`} />
                <button onClick={sendNewRequest} disabled={!msg.trim()} className={`${main} mt-3`} style={fill}>Start a new request</button>
              </>)}
            </section>
          )}

          {/* review capture: only once the work is done */}
          {d.feedback && extrasApi && (nearEnd || d.feedback.submitted || fbResult) && (
            <section className={sec}>
              <H2>{d.feedback.heading || "How did we do?"}</H2>
              {fbResult ? (
                <div>
                  <div className="flex items-center gap-2 text-base font-semibold text-ink-primary"><Check size={16} className="text-emerald-600" /> {fbResult.message}</div>
                  {safeHttpHref(fbResult.google_review_url) && (
                    <a href={safeHttpHref(fbResult.google_review_url)} target="_blank" rel="noopener" className={`${main} mt-3`} style={fill}>
                      <ExternalLink size={15} /> Leave a public review
                    </a>
                  )}
                </div>
              ) : d.feedback.submitted ? (
                <div className="text-base text-ink-secondary">Thanks for your feedback{d.feedback.rating != null ? `. You rated us ${d.feedback.rating}${d.feedback.system === "five_point" ? "/5" : ""}.` : "."}</div>
              ) : (
                <>
                  {d.feedback.question && <div className="mb-3 text-base text-ink-secondary">{d.feedback.question}</div>}
                  {d.feedback.system === "five_point" && (
                    <div className="flex gap-1.5">
                      {[1, 2, 3, 4, 5].map((n) => (
                        <button key={n} onClick={() => setFbRating(n)} aria-label={`${n} star${n === 1 ? "" : "s"}`} className="p-0.5">
                          <Star size={30} className={n <= fbRating ? "text-amber-500" : "text-slate-300"} style={{ fill: n <= fbRating ? "currentColor" : "transparent" }} />
                        </button>
                      ))}
                    </div>
                  )}
                  {d.feedback.system === "like" && (
                    <div className="flex gap-3">
                      <button onClick={() => setFbRating(1)} className={`${toggle} ` + (fbRating === 1 ? "border-emerald-500 text-emerald-700" : "border-slate-300 text-ink-secondary")}><ThumbsUp size={16} /> Good</button>
                      <button onClick={() => setFbRating(-1)} className={`${toggle} ` + (fbRating === -1 ? "border-rose-500 text-rose-600" : "border-slate-300 text-ink-secondary")}><ThumbsDown size={16} /> Not great</button>
                    </div>
                  )}
                  {d.feedback.system === "nps" && (
                    <div className="flex flex-wrap gap-1.5">
                      {Array.from({ length: 11 }, (_, n) => (
                        <button key={n} onClick={() => setFbRating(n)} className={"h-11 w-11 rounded-xl border bg-white text-base font-semibold tabular-nums " + (fbRating === n ? "" : "border-slate-300 text-ink-secondary")} style={fbRating === n ? { background: accent, borderColor: accent, color: onAccent } : undefined}>{n}</button>
                      ))}
                    </div>
                  )}
                  <textarea value={fbComment} onChange={(e) => setFbComment(e.target.value)} rows={3}
                    placeholder={d.feedback.clarify_question || "Tell us more (optional)"}
                    className={`${textareaCls} mt-3 resize-y`} />
                  <button onClick={submitFeedback} disabled={fbBusy || !fbRating} className={`${main} mt-2 disabled:opacity-40`} style={fill}>
                    {fbBusy ? "Sending…" : "Send rating"}
                  </button>
                </>
              )}
            </section>
          )}
          {/* pickup: where and when, once it's nearly ready */}
          {nearEnd && (d.shop_address || d.shop_phone) && (
            <section className={sec}>
              <H2>Pickup</H2>
              {d.location_name && <div className="text-base font-semibold text-ink-primary">{d.location_name}</div>}
              {d.shop_address && <div className="mt-0.5 flex items-start gap-1.5 text-sm text-ink-secondary"><MapPin size={14} className="mt-0.5 shrink-0" /> {d.shop_address}</div>}
              {hoursLine && <div className="mt-1 flex items-center gap-1.5 text-sm text-ink-tertiary"><Clock size={13} /> {hoursLine}</div>}
            </section>
          )}

          {d.has_signature && extrasApi && (
            <section className={sec}>
              <a href={`${extrasApi}/certificate.pdf`} target="_blank" rel="noopener" className={plain}><Download size={15} /> Signed approval (PDF)</a>
            </section>
          )}
        </div>

      {/* phone bottom bar: only while there's something to do */}
      {showBar && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white px-4 pt-3 sm:px-6" style={{ paddingBottom: "max(12px, env(safe-area-inset-bottom))" }}>
          <div className="mx-auto max-w-[592px]">
          {chosen.length > 0 ? (
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1 text-sm text-ink-secondary"><span className="font-semibold text-ink-primary">{chosen.length} added</span> · new total {money(total + recSum, sym)}</div>
              <button onClick={() => setReview(true)} className={main} style={fill}>Review</button>
            </div>
          ) : d.awaiting_approval ? (
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1 text-sm text-ink-secondary">Total <span className="font-semibold tabular-nums text-ink-primary">{skippedSum ? money(total, sym) : fmtDoc(d)}</span></div>
              <button onClick={() => setSignOpen(true)} disabled={allSkipped} className={`${main} disabled:opacity-40`} style={fill}>Review and sign</button>
              <button onClick={() => setDeclineOpen(true)} className={plain}>Decline</button>
            </div>
          ) : dep && dep.can_pay && extrasApi ? (
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1 text-sm text-ink-secondary">Deposit <span className="font-semibold tabular-nums text-ink-primary">{money(dep.due, sym)}</span></div>
              <button onClick={payDeposit} disabled={payBusy} className={`${main} disabled:opacity-60`} style={fill}>{payBusy ? "Opening…" : "Pay deposit"}</button>
            </div>
          ) : (
            <div className="flex gap-2">
              <a href={`tel:${d.shop_phone}`} className={`${main} flex-1`} style={fill}><Phone size={15} /> Call</a>
              {mapHref && <a href={mapHref} target="_blank" rel="noopener" className={`${plain} flex-1`}><MapPin size={15} /> Directions</a>}
            </div>
          )}
          </div>
        </div>
      )}

      {review && (
        <ReviewSheet items={chosen} sym={sym} total={total} accent={accent} defaultName={d.client_name || ""}
          onClose={() => setReview(false)}
          onConfirm={(name) => decideRecs({ approve_ids: chosen.map((x) => x.id), name }, chosen.length === 1 ? `${chosen[0].title} added to your repair` : `${chosen.length} items added to your repair`)} />
      )}
      {photo && (
        <div role="dialog" aria-label="Photo" className="fixed inset-0 z-50 grid place-items-center bg-black/85 p-4" onClick={() => setPhoto(null)}>
          <img src={photo} alt="" className="max-h-[88vh] max-w-full rounded-xl object-contain" />
          <button aria-label="Close photo" className="absolute right-4 top-4 grid h-10 w-10 place-items-center rounded-full bg-white/15 text-white"><X size={20} /></button>
        </div>
      )}
      {signOpen && <SignModal accent={accent} onClose={() => setSignOpen(false)} onDone={(sig) => { setSignOpen(false); respond("accept", sig); }} />}
      {declineOpen && <DeclineModal accent={accent} onClose={() => setDeclineOpen(false)} onDone={(reason) => { setDeclineOpen(false); respond("decline", null, reason); }} />}
      {toast && <div role="status" className="fixed bottom-24 left-1/2 z-50 -translate-x-1/2 rounded-xl bg-ink-primary px-4 py-2.5 text-sm font-semibold text-white shadow-menu">{toast}</div>}
    </PublicLayout>
  );
}

// Confirm step: just the chosen items, the new total, and the customer's name.
function ReviewSheet({ items, sym, total, accent, defaultName, onClose, onConfirm }: {
  items: Rec[]; sym: string; total: number; accent: string; defaultName: string;
  onClose: () => void; onConfirm: (name: string) => void;
}) {
  const [name, setName] = useState(defaultName);
  const [busy, setBusy] = useState(false);
  const sum = items.reduce((a, x) => a + x.line_total, 0);
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 sm:p-4" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-label="Confirm your choices" className="w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl bg-white p-6" style={{ paddingBottom: "max(24px, env(safe-area-inset-bottom))" }}>
        <div className="flex items-center justify-between">
          <h2 className="text-title font-semibold text-ink-primary">Add to your repair</h2>
          <IconButton label="Close" onClick={onClose} icon={<X size={20} />} />
        </div>
        <ul className="mt-4 divide-y divide-slate-100">
          {items.map((r) => (
            <li key={r.id} className="flex justify-between gap-3 py-2.5 text-base"><span className="text-ink-primary">{r.title}</span><span className="tabular-nums text-ink-primary">{money(r.line_total, sym)}</span></li>
          ))}
        </ul>
        <div className="mt-2 flex justify-between border-t border-slate-200 pt-3 text-base font-semibold text-ink-primary"><span>New total</span><span className="tabular-nums">{money(total + sum, sym)}</span></div>
        <p className="mt-3 text-sm leading-relaxed text-ink-tertiary">We'll add these to your repair. Nothing else changes, and you can message us before we start.</p>
        <label className="mt-4 block">
          <span className={fieldLabelCls}>Your name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" className={inputCls} />
        </label>
        <div className="mt-5 flex gap-2">
          <button disabled={busy || !name.trim()} onClick={() => { setBusy(true); onConfirm(name.trim()); }}
            className={buttonClass("primary", "lg", "flex-[2]")} style={accentFill(accent)}>
            Confirm, add {money(sum, sym)}
          </button>
          <button onClick={onClose} className={buttonClass("secondary", "lg", "flex-1")}>Go back</button>
        </div>
      </div>
    </div>
  );
}

// Signature capture — canvas + pointer events, no libraries.
// E5 — declining asks why (optional), so the shop can send a better estimate.
function DeclineModal({ accent, onClose, onDone }: { accent: string; onClose: () => void; onDone: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-label="Decline the estimate" className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-title font-semibold text-ink-primary">Decline the estimate</span>
          <IconButton label="Close" onClick={onClose} icon={<X size={18} />} />
        </div>
        <label className="mb-1.5 block text-sm text-ink-secondary" htmlFor="decline-reason">Can you tell us why? (optional)</label>
        <textarea id="decline-reason" value={reason} onChange={(e) => setReason(e.target.value.slice(0, 1000))} rows={3}
          placeholder="For example: too expensive, or I'll fix it another way"
          className={`${textareaCls} resize-y`} />
        <div className="mt-3 flex gap-2">
          <button onClick={() => onDone(reason.trim())} className={buttonClass("primary", "lg")} style={accentFill(accent)}>Decline</button>
          <button onClick={onClose} className={buttonClass("secondary", "lg")}>Keep it</button>
        </div>
      </div>
    </div>
  );
}

function SignModal({ accent, onClose, onDone }: { accent: string; onClose: () => void; onDone: (sig: string) => void }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const st = useRef({ drawing: false, dirty: false });

  useEffect(() => {
    const c = ref.current!; c.width = c.offsetWidth; c.height = 150;
    const ctx = c.getContext("2d")!; ctx.strokeStyle = "#18254a"; ctx.lineWidth = 2.2; ctx.lineCap = "round";
    const pos = (e: any) => { const r = c.getBoundingClientRect(); const t = e.touches ? e.touches[0] : e; return { x: (t.clientX - r.left) * (c.width / r.width), y: (t.clientY - r.top) * (c.height / r.height) }; };
    const start = (e: any) => { e.preventDefault(); st.current.drawing = true; st.current.dirty = true; const p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); };
    const move = (e: any) => { if (!st.current.drawing) return; e.preventDefault(); const p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke(); };
    const end = () => (st.current.drawing = false);
    c.addEventListener("mousedown", start); c.addEventListener("mousemove", move); window.addEventListener("mouseup", end);
    c.addEventListener("touchstart", start, { passive: false }); c.addEventListener("touchmove", move, { passive: false }); c.addEventListener("touchend", end);
    return () => { c.removeEventListener("mousedown", start); c.removeEventListener("mousemove", move); window.removeEventListener("mouseup", end); };
  }, []);

  function clear() { const c = ref.current!; c.getContext("2d")!.clearRect(0, 0, c.width, c.height); st.current.dirty = false; }
  function submit() {
    if (!st.current.dirty) return;
    onDone(ref.current!.toDataURL("image/png"));
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-title font-semibold text-ink-primary">Approve and sign</span>
          <IconButton label="Close" onClick={onClose} icon={<X size={18} />} />
        </div>
        <div className="mb-1.5 text-sm text-ink-secondary">Please sign below to approve.</div>
        <canvas ref={ref} className="h-[150px] w-full touch-none rounded-lg border-[1.5px] border-dashed border-primary/40 bg-slate-50" />
        <div className="mt-3 flex gap-2">
          <button onClick={submit} className={buttonClass("primary", "lg")} style={accentFill(accent)}>Approve and sign</button>
          <button onClick={clear} className={buttonClass("secondary", "lg")}>Clear</button>
        </div>
      </div>
    </div>
  );
}
