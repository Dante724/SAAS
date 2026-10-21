import { Fragment, useEffect, useRef, useState } from "react";
import {
  Check, X, Paperclip, Receipt, Download, MapPin, Clock, Phone, Star, ThumbsUp, ThumbsDown, ExternalLink,
} from "lucide-react";

// ── Public customer page: order tracking + estimate + e-sign ──────────────
// A real part of the React app (Liquid Clay tokens, flat styling) — NOT the
// legacy vanilla-JS track.html. Served at /track/:token, no auth, no shell.

type Line = { label: string; qty?: number; kind?: string; line_type?: string; amount: number; item?: string | null };

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
  return sym + v.toLocaleString(undefined, { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 });
};
const docTotal = (d: Track) => (d?.fx_rate && Number(d.fx_rate) !== 1
  ? (d.total_converted != null ? d.total_converted : (d.total || 0) * d.fx_rate) : (d?.total || 0));
const fmtDoc = (d: Track) => money(docTotal(d), d?.currency_symbol || "$");
// Paid / balance due, in plain words beside every total, so a settled job never reads as owed.
export function PaidNote({ paid, balance, paidInFull, sym, className = "" }: {
  paid?: number; balance?: number; paidInFull?: boolean; sym: string; className?: string;
}) {
  if (paidInFull) return (
    <span className={"inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-[13px] font-semibold text-emerald-700 " + className}>
      <Check size={13} strokeWidth={2.5} /> Paid in full
    </span>
  );
  if ((paid || 0) > 0 && (balance || 0) > 0) return (
    <span className={"text-[13px] text-ink-secondary " + className}>
      Paid {money(paid || 0, sym)} · <span className="font-semibold text-amber-700">Balance due {money(balance || 0, sym)}</span>
    </span>
  );
  return null;
}

// White or near-black text on the shop's colour, whichever reads better.
export function onColor(hex: string) {
  const h = hex.replace("#", ""); const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => { const x = c / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 ? "#111827" : "#ffffff";
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
export const initials = (s?: string) =>
  (s || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase() || "R";

// 144ct — sender label for the two-way message items in the updates feed
const UPD_SENDER: Record<string, string> = { shop_message: "Shop", client_message: "You" };

// Load one public document and keep it fresh: poll every 20s, pause while the tab is hidden.
export function usePublicDoc(url: string) {
  const [d, setD] = useState<Track | null>(null);
  const [err, setErr] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number>(0);
  const urlRef = useRef(url);
  urlRef.current = url;

  async function load(silent = false) {
    const at = urlRef.current;
    try {
      const r = await fetch(at);
      if (!r.ok) throw new Error();
      const data: Track = await r.json();
      if (urlRef.current !== at) return;   // the visitor moved on to another job meanwhile
      setD(data);
      setErr(false);
      setUpdatedAt(Date.now());
    } catch { if (!silent && urlRef.current === at) setErr(true); }
  }
  useEffect(() => { setD(null); setErr(false); load(); }, [url]);
  useEffect(() => {
    let id: ReturnType<typeof setInterval> | null = null;
    const start = () => { if (!id) id = setInterval(() => load(true), 20000); };
    const stop = () => { if (id) { clearInterval(id); id = null; } };
    const onVis = () => (document.hidden ? stop() : (load(true), start()));
    start();
    document.addEventListener("visibilitychange", onVis);
    return () => { stop(); document.removeEventListener("visibilitychange", onVis); };
  }, []);
  return { d, err, updatedAt, load };
}

export default function PublicTrack() {
  const token = location.pathname.split("/track/")[1]?.replace(/\/$/, "") || "";
  const api = "/api/public/track/" + token;
  const { d, err, updatedAt, load } = usePublicDoc(api);

  if (err)
    return (
      <div className="min-h-screen bg-surface text-ink-primary grid place-items-center px-5">
        <div className="text-center">
          <div className="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-rose-50 text-rose-600"><X size={26} /></div>
          <div className="text-lg font-bold">Order not found</div>
          <div className="mt-1.5 text-sm text-ink-tertiary">This tracking link may be invalid or expired.</div>
        </div>
      </div>
    );
  if (!d) return <div className="min-h-screen bg-surface" />;
  return <TrackView d={d} updatedAt={updatedAt} api={api} extrasApi={api} reload={load} />;
}

// One job as the customer sees it: status, recommendations, estimate, updates, invoice,
// messages, rating. Shared by the track page and the customer portal's job view.
// `api` takes respond / message / recommendations; `extrasApi` (the job's track link) takes
// the rating, new request, signed PDF and e-invoice, and those parts hide without it.
export function TrackView({ d, updatedAt, api, extrasApi, reload, onResponded, top }: {
  d: Track; updatedAt: number; api: string; extrasApi: string | null;
  reload: (silent?: boolean) => void;
  onResponded?: (decision: string, result: { converted_order_id?: string | null }) => void;
  top?: React.ReactNode;
}) {
  const [signOpen, setSignOpen] = useState(false);
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

  async function respond(decision: string, signature: string | null = null) {
    try {
      const r = await fetch(api + "/respond", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, signature }),
      });
      if (!r.ok) throw new Error();
      const j = await r.json().catch(() => ({}));
      flash(decision === "accept" ? "Estimate approved — thank you!" : "Estimate declined");
      setTimeout(() => { setRecSel({}); reload(); onResponded?.(decision, j); }, 800);
    } catch { flash("Could not submit your response"); }
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
  const accent = d.brand_color || "#0059ba";
  const onAccent = onColor(accent);
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
  const total = docTotal(d);
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

  const H2 = ({ children }: { children: React.ReactNode }) => <h2 className="mb-3 text-[17px] font-semibold text-ink-primary">{children}</h2>;
  const sec = "mt-10 border-t border-slate-200 pt-8";
  const btn = "inline-flex h-11 items-center justify-center gap-1.5 rounded-[10px] px-4 text-[15px] font-semibold";

  return (
    <div className="min-h-screen bg-canvas text-ink-primary font-sans pb-24 lg:pb-0">
      <div className="mx-auto grid max-w-[1040px] items-start gap-8 px-4 py-6 sm:py-10 lg:grid-cols-[minmax(0,1fr)_320px]">
        <main className="min-w-0">
          {top}
          {/* shop bar */}
          <div className="flex items-center gap-3">
            {d.logo_data
              ? <img src={d.logo_data} alt={d.shop_name || "logo"} className="h-9 w-9 shrink-0 rounded-[10px] object-contain bg-white" />
              : <div className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] text-[14px] font-semibold" style={{ background: accent, color: onAccent }}>{initials(d.shop_name)}</div>}
            <div className="min-w-0 flex-1 truncate text-[15px] font-semibold text-ink-primary">{d.shop_name}</div>
            <div className="text-[13px] tabular-nums text-ink-tertiary">{d.is_estimate ? "Estimate" : "Job"} {d.order_number}</div>
          </div>

          {/* status hero */}
          <div className="mt-8">
            {d.awaiting_approval && <span className="mb-3 inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[12px] font-medium text-amber-800">Waiting for your approval</span>}
            <h1 className="text-[24px] sm:text-[28px] font-semibold leading-tight tracking-[-0.02em] text-ink-primary">{headline}</h1>
            <p className="mt-1.5 text-[15px] text-ink-tertiary">{d.stopped ? `${d.stage_label}. Contact ${d.shop_name || "the shop"} if you have questions.`
              : [isEst ? `Status: ${d.stage_label}` : (headline === d.stage_label ? d.device : d.stage_label), ago].filter(Boolean).join(" · ")}</p>
            {/* phones have no side card; with no line list the paid note sits here */}
            {!(d.lines || []).length && <div className="mt-3 lg:hidden"><PaidNote paid={d.amount_paid} balance={d.balance_due} paidInFull={d.paid_in_full} sym={sym} /></div>}
            {!d.stopped && !isEst && (d.stages || []).length > 0 && (
              <div className="mt-5">
                <div className="flex gap-1">
                  {(d.stages || []).map((_, i) => (
                    <span key={i} className="h-1 flex-1 rounded-full" style={{ background: i <= stageIdx ? accent : "rgb(var(--c-slate-200))" }} />
                  ))}
                </div>
                <div className="mt-2 hidden sm:flex">
                  {(d.stages || []).map((s, i) => (
                    <span key={i} className={`flex-1 pr-2 text-[12px] leading-snug ${i === stageIdx ? "font-semibold text-ink-primary" : "text-ink-tertiary"}`}>{s.label}</span>
                  ))}
                </div>
                <div className="mt-2 text-[13px] text-ink-tertiary sm:hidden">Step {stageIdx + 1} of {nStages} · {d.stages![stageIdx]?.label}</div>
              </div>
            )}
          </div>

          {/* what's being repaired */}
          {(d.complaint || (d.custom_fields || []).length > 0 || sched.length > 0) && (
            <dl className="mt-8 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
              {d.device && <div><dt className="text-[13px] text-ink-tertiary">{d.asset_label || "Item"}</dt><dd className="text-[15px] text-ink-primary">{d.device}</dd></div>}
              {sched.map((r) => <div key={r.label}><dt className="text-[13px] text-ink-tertiary">{r.label}</dt><dd className="text-[15px] text-ink-primary">{r.value}</dd></div>)}
              {d.complaint && <div className="sm:col-span-2"><dt className="text-[13px] text-ink-tertiary">What you told us</dt><dd className="text-[15px] text-ink-primary">{d.complaint}</dd></div>}
              {(d.custom_fields || []).map((f) => <div key={f.label}><dt className="text-[13px] text-ink-tertiary">{f.label}</dt><dd className="text-[15px] text-ink-primary">{f.value}</dd></div>)}
            </dl>
          )}

          {/* recommendations: the customer chooses what to add */}
          {recs.length > 0 && (
            <section className={sec} id="recommendations">
              <div className="rounded-2xl border bg-white p-5 sm:p-6" style={{ borderColor: `color-mix(in srgb, ${accent} 30%, rgb(var(--c-line-200)))` }}>
                {openRecs.length > 0 && (<>
                  <h2 className="text-[17px] font-semibold text-ink-primary">{openRecs.length === 1 ? "Your technician found one more thing" : `Your technician found ${openRecs.length} more things`}</h2>
                  <p className="mt-1 text-[14px] text-ink-tertiary">Add what you'd like us to do. Nothing is added unless you choose it.</p>
                  <div className="mt-5 flex flex-col gap-6">
                    {byItem(openRecs).map((g) => (
                      <div key={g.item || "_job"} className="flex flex-col gap-3">
                        {openRecs.some((x) => x.item) && <div className="text-[13px] font-semibold text-ink-secondary">{g.item ? `For your ${g.item}` : "For the whole job"}</div>}
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
                                  {r.urgency && URGENCY[r.urgency] && <span className={`mb-1.5 inline-flex rounded-full border px-2 py-0.5 text-[12px] font-medium ${URGENCY[r.urgency].cls}`}>{URGENCY[r.urgency].label}</span>}
                                  <div className="text-[15px] font-semibold text-ink-primary">{r.title}</div>
                                  {r.note && <p className="mt-0.5 text-[14px] leading-relaxed text-ink-secondary">{r.note}</p>}
                                  {r.media_data && <p className="mt-1 text-[12px] text-ink-tertiary">Photo taken by your technician</p>}
                                </div>
                                <div className="text-right">
                                  <div className="text-[15px] font-semibold tabular-nums text-ink-primary">{money(r.line_total, sym)}</div>
                                  {(r.qty ?? 1) > 1 && <div className="text-[12px] tabular-nums text-ink-tertiary">{r.qty} × {money(r.unit_price || 0, sym)}</div>}
                                </div>
                              </div>
                              <button type="button" onClick={() => setRecSel((s) => ({ ...s, [r.id]: !on }))} aria-pressed={on}
                                className="flex h-12 w-full items-center justify-center gap-2 border-t text-[15px] font-semibold rounded-b-xl"
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
                  <div className="mt-6 rounded-xl bg-slate-50 p-4 text-[15px]">
                    <div className="flex justify-between text-ink-secondary"><span>Your {isEst ? "estimate" : "repair"}</span><span className="tabular-nums">{money(total, sym)}</span></div>
                    <div className="mt-1 flex justify-between text-ink-secondary"><span>Extras you've chosen</span><span className="tabular-nums">{recSum ? `+ ${money(recSum, sym)}` : money(0, sym)}</span></div>
                    <div className="mt-2 flex justify-between border-t border-slate-200 pt-2 font-semibold text-ink-primary"><span>New total</span><span className="tabular-nums">{money(total + recSum, sym)}</span></div>
                  </div>
                  <div className="mt-4 flex flex-wrap items-center gap-3">
                    <button type="button" disabled={!chosen.length} onClick={() => setReview(true)}
                      className={`${btn} disabled:opacity-40`} style={{ background: accent, color: onAccent }}>
                      {chosen.length ? `Review my choices · ${money(recSum, sym)}` : "Choose what to add"}
                    </button>
                    <button type="button" onClick={() => decideRecs({ later_ids: openRecs.map((x) => x.id) }, "Saved for later. You can add them any time while we have your repair.")}
                      className="text-[14px] font-medium text-ink-tertiary hover:text-ink-secondary underline-offset-4 hover:underline">Not now</button>
                  </div>
                </>)}

                {/* decided */}
                {addedRecs.length > 0 && (
                  <div className={openRecs.length ? "mt-8" : ""}>
                    <h3 className="text-[15px] font-semibold text-ink-primary">Added to your repair</h3>
                    <ul className="mt-2 divide-y divide-slate-100">
                      {addedRecs.map((r) => (
                        <li key={r.id} className="flex items-center gap-3 py-2.5">
                          <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-emerald-50 text-emerald-700"><Check size={14} /></span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-[15px] text-ink-primary">{r.title}</span>
                            {(r.decided_by_name || r.decided_at) && <span className="block text-[12px] text-ink-tertiary">Confirmed{r.decided_by_name ? ` by ${r.decided_by_name}` : ""}{r.decided_at ? `, ${fmtDate(r.decided_at)}` : ""}</span>}
                          </span>
                          <span className="text-[15px] tabular-nums text-ink-primary">{money(r.line_total, sym)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {laterRecs.length > 0 && (
                  <div className={openRecs.length || addedRecs.length ? "mt-6" : ""}>
                    <h3 className="text-[15px] font-semibold text-ink-primary">{canChange ? "Saved for later" : "Ask us about these next time"}</h3>
                    <ul className="mt-2 divide-y divide-slate-100">
                      {laterRecs.map((r) => (
                        <li key={r.id} className="flex items-center gap-3 py-2.5">
                          <span className="min-w-0 flex-1 text-[15px] text-ink-secondary">{r.title}</span>
                          <span className="text-[15px] tabular-nums text-ink-tertiary">{money(r.line_total, sym)}</span>
                          {canChange && <button type="button" onClick={() => decideRecs({ approve_ids: [r.id], name: d.client_name || undefined }, `${r.title} added to your repair`)}
                            className="text-[14px] font-semibold" style={{ color: accent }}>Add it</button>}
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
              <table className="w-full border-collapse text-[15px]">
                <thead>
                  <tr className="text-[13px] text-ink-tertiary">
                    <th className="border-b border-slate-200 pb-2 text-left font-normal">Description</th>
                    <th className="border-b border-slate-200 pb-2 text-right font-normal">Qty</th>
                    <th className="border-b border-slate-200 pb-2 text-right font-normal">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {d.lines.map((l, i) => (<Fragment key={i}>
                    {d.lines!.some((x) => x.item) && (i === 0 || (d.lines![i - 1].item || null) !== (l.item || null)) && (
                      <tr><td colSpan={3} className="pb-1 pt-4 text-[13px] font-semibold text-ink-secondary">{l.item || "Whole job"}</td></tr>
                    )}
                    <tr>
                      <td className="border-b border-slate-100 py-3 align-top text-ink-primary">{l.label}</td>
                      <td className="border-b border-slate-100 py-3 text-right align-top tabular-nums text-ink-tertiary">{l.qty != null ? l.qty : 1}</td>
                      <td className="border-b border-slate-100 py-3 text-right align-top tabular-nums text-ink-primary">{money(l.amount, sym)}</td>
                    </tr>
                  </Fragment>))}
                </tbody>
              </table>
              <div className="mt-3 flex justify-end gap-10 text-[17px] font-semibold text-ink-primary"><span>Total</span><span className="tabular-nums">{fmtDoc(d)}</span></div>
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
                      <div className="text-[15px] text-ink-primary">{e.t.label}</div>
                    ) : (<>
                      {UPD_SENDER[e.u.type] && <div className="text-[13px] font-semibold text-ink-secondary">{UPD_SENDER[e.u.type]}</div>}
                      {e.u.body && <div className="text-[15px] text-ink-primary">{e.u.body}</div>}
                      {(e.u.type === "photo" || e.u.type === "signature") && e.u.media_data && (
                        <button type="button" onClick={() => setPhoto(e.u.media_data!)} className="mt-2 block overflow-hidden rounded-lg border border-slate-200 bg-white">
                          <img src={e.u.media_data} alt="" className="h-32 w-auto max-w-[220px] object-cover" />
                        </button>
                      )}
                      {e.u.type === "file" && e.u.media_data && (
                        <a href={e.u.media_data} download={e.u.media_name || "file"} className="mt-1 inline-flex items-center gap-1.5 text-[15px] font-semibold" style={{ color: accent }}><Paperclip size={15} /> {e.u.media_name || "Download file"}</a>
                      )}
                    </>)}
                    <div className="mt-0.5 text-[13px] text-ink-tertiary">{fmtDate(e.at)}</div>
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
                  <div className="text-[15px] font-semibold tabular-nums text-ink-primary">{d.invoice.invoice_number || "Invoice"}</div>
                  <div className="text-[13px] capitalize text-ink-tertiary">{d.invoice.status || ""}</div>
                </div>
                <div className="text-right">
                  <div className="text-[17px] font-semibold tabular-nums text-ink-primary">{money(d.invoice.total_converted != null ? d.invoice.total_converted : d.invoice.total || 0, sym)}</div>
                  {d.invoice.balance_due > 0
                    ? <div className="text-[13px] text-amber-700">Balance {money(d.invoice.balance_due, sym)}</div>
                    : <div className="text-[13px] text-emerald-700">Paid</div>}
                </div>
              </div>
              {d.invoice.einvoice_available && extrasApi && (
                <>
                  <a href={`${extrasApi}/einvoice.xml`} download className={`${btn} mt-3 border border-slate-300 text-ink-secondary hover:bg-slate-50`}><Receipt size={15} /> Download e-invoice (XML)</a>
                  <div className="mt-1.5 text-[13px] text-ink-tertiary">Standards-based UBL 2.1 electronic invoice{d.invoice.einvoice_number ? ` · ${d.invoice.einvoice_number}` : ""}</div>
                </>
              )}
            </section>
          )}

          {/* message */}
          {d.allow_messages !== false && (
            <section className={sec}>
              <H2>Message {d.shop_name || "the shop"}</H2>
              {d.messages_until && (
                <p className="mb-3 text-[14px] text-ink-tertiary">Questions about this repair? You can message us until {new Date(d.messages_until).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}.</p>
              )}
              <div className="relative">
                <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={3} placeholder="Ask a question or add a note…"
                  className="w-full resize-y rounded-[10px] border border-slate-300 bg-white p-3 pb-14 text-[15px] text-ink-primary outline-none focus:border-slate-400" />
                <button onClick={sendMessage} disabled={!msg.trim()} className="absolute bottom-3 right-3 inline-flex h-9 items-center rounded-lg px-3.5 text-[14px] font-semibold disabled:opacity-40" style={{ background: accent, color: onAccent }}>Send</button>
              </div>
            </section>
          )}
          {d.allow_messages === false && d.can_new_request && extrasApi && (
            <section className={sec}>
              <H2>Need something else?</H2>
              {reqSent ? (
                <div className="flex items-center gap-2 text-[15px] font-semibold text-ink-primary"><Check size={16} className="text-emerald-600" /> Request sent. The shop will contact you.</div>
              ) : (<>
                <p className="mb-3 text-[14px] text-ink-tertiary">This repair is complete. Describe the new problem and we'll get back to you.</p>
                <div className="relative">
                  <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={3} placeholder="What's going on?"
                    className="w-full resize-y rounded-[10px] border border-slate-300 bg-white p-3 pb-14 text-[15px] text-ink-primary outline-none focus:border-slate-400" />
                  <button onClick={sendNewRequest} disabled={!msg.trim()} className="absolute bottom-3 right-3 inline-flex h-9 items-center rounded-lg px-3.5 text-[14px] font-semibold disabled:opacity-40" style={{ background: accent, color: onAccent }}>Start a new request</button>
                </div>
              </>)}
            </section>
          )}

          {/* review capture: only once the work is done */}
          {d.feedback && extrasApi && (nearEnd || d.feedback.submitted || fbResult) && (
            <section className={sec}>
              <H2>{d.feedback.heading || "How did we do?"}</H2>
              {fbResult ? (
                <div>
                  <div className="flex items-center gap-2 text-[15px] font-semibold text-ink-primary"><Check size={16} className="text-emerald-600" /> {fbResult.message}</div>
                  {fbResult.google_review_url && (
                    <a href={fbResult.google_review_url} target="_blank" rel="noopener" className={`${btn} mt-3`} style={{ background: accent, color: onAccent }}>
                      <ExternalLink size={15} /> Leave a public review
                    </a>
                  )}
                </div>
              ) : d.feedback.submitted ? (
                <div className="text-[15px] text-ink-secondary">Thanks for your feedback{d.feedback.rating != null ? `. You rated us ${d.feedback.rating}${d.feedback.system === "five_point" ? "/5" : ""}.` : "."}</div>
              ) : (
                <>
                  {d.feedback.question && <div className="mb-3 text-[15px] text-ink-secondary">{d.feedback.question}</div>}
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
                      <button onClick={() => setFbRating(1)} className={`${btn} border ` + (fbRating === 1 ? "border-emerald-500 text-emerald-700" : "border-slate-300 text-ink-secondary")}><ThumbsUp size={16} /> Good</button>
                      <button onClick={() => setFbRating(-1)} className={`${btn} border ` + (fbRating === -1 ? "border-rose-500 text-rose-600" : "border-slate-300 text-ink-secondary")}><ThumbsDown size={16} /> Not great</button>
                    </div>
                  )}
                  {d.feedback.system === "nps" && (
                    <div className="flex flex-wrap gap-1.5">
                      {Array.from({ length: 11 }, (_, n) => (
                        <button key={n} onClick={() => setFbRating(n)} className={"h-10 w-10 rounded-lg border text-[15px] font-semibold tabular-nums " + (fbRating === n ? "" : "border-slate-300 text-ink-secondary")} style={fbRating === n ? { background: accent, borderColor: accent, color: onAccent } : undefined}>{n}</button>
                      ))}
                    </div>
                  )}
                  <textarea value={fbComment} onChange={(e) => setFbComment(e.target.value)} rows={3}
                    placeholder={d.feedback.clarify_question || "Tell us more (optional)"}
                    className="mt-3 w-full resize-y rounded-[10px] border border-slate-300 bg-white p-3 text-[15px] text-ink-primary outline-none focus:border-slate-400" />
                  <button onClick={submitFeedback} disabled={fbBusy || !fbRating} className={`${btn} mt-2 disabled:opacity-40`} style={{ background: accent, color: onAccent }}>
                    {fbBusy ? "Sending…" : "Send rating"}
                  </button>
                </>
              )}
            </section>
          )}
        </main>

        {/* ── side card (desktop), the same information sits inline / in the bottom bar on phones ── */}
        <aside className="hidden lg:flex flex-col gap-4 lg:sticky lg:top-6">
          <div className="rounded-2xl border border-slate-200 bg-white p-6">
            <div className="text-[13px] text-ink-tertiary">{d.awaiting_approval ? (isEst ? "Estimate total" : "Total due") : "Total"}</div>
            <div className="mt-1 text-[32px] font-semibold leading-none tracking-[-0.02em] tabular-nums text-ink-primary">{money(total + recSum, sym)}</div>
            {recSum > 0 && <div className="mt-2 text-[13px] text-ink-tertiary">{money(total, sym)} + {money(recSum, sym)} you've chosen</div>}
            {!recSum && <PaidNote paid={d.amount_paid} balance={d.balance_due} paidInFull={d.paid_in_full} sym={sym} className="mt-2.5" />}
            {d.awaiting_approval && (
              <div className="mt-5 flex flex-col gap-2">
                <button onClick={() => setSignOpen(true)} className={`${btn} w-full`} style={{ background: accent, color: onAccent }}>Review and sign</button>
                <button onClick={() => respond("decline")} className={`${btn} w-full border border-slate-300 text-ink-secondary hover:bg-slate-50`}>Decline</button>
                <div className="mt-1 text-[13px] leading-relaxed text-ink-tertiary">Secure e-signature. You'll get a signed PDF copy.</div>
              </div>
            )}
            {chosen.length > 0 && (
              <button onClick={() => setReview(true)} className={`${btn} mt-5 w-full`} style={{ background: accent, color: onAccent }}>Review my choices</button>
            )}
            <div className="mt-5 flex items-center gap-1.5 text-[13px] text-ink-tertiary">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" /> Live · updates automatically
            </div>
          </div>

          {nearEnd && (d.shop_address || d.shop_phone) && (
            <div className="rounded-2xl border border-slate-200 bg-white p-6">
              <div className="flex items-center gap-1.5 text-[13px] text-ink-tertiary"><MapPin size={14} /> Pickup</div>
              {d.location_name && <div className="mt-2 text-[15px] font-semibold text-ink-primary">{d.location_name}</div>}
              {d.shop_address && <div className="mt-0.5 text-[14px] text-ink-secondary">{d.shop_address}</div>}
              {hoursLine && <div className="mt-1 flex items-center gap-1.5 text-[13px] text-ink-tertiary"><Clock size={13} /> {hoursLine}</div>}
              <div className="mt-4 flex flex-col gap-2">
                {d.shop_phone && <a href={`tel:${d.shop_phone}`} className={`${btn} w-full`} style={{ background: accent, color: onAccent }}><Phone size={15} /> Call the shop</a>}
                {mapHref && <a href={mapHref} target="_blank" rel="noopener" className={`${btn} w-full border border-slate-300 text-ink-secondary hover:bg-slate-50`}><MapPin size={15} /> Directions</a>}
              </div>
            </div>
          )}

          {d.has_signature && extrasApi && (
            <a href={`${extrasApi}/certificate.pdf`} target="_blank" rel="noopener"
              className={`${btn} w-full border border-slate-300 bg-white text-ink-secondary hover:bg-slate-50`}><Download size={15} /> Signed approval (PDF)</a>
          )}
        </aside>
      </div>

      <div className="py-8 text-center text-[12px] text-ink-tertiary">{d.shop_name} · Sent with Tekly</div>

      {/* phone bottom bar: only while there's something to do */}
      {(chosen.length > 0 || d.awaiting_approval || (nearEnd && d.shop_phone)) && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 px-4 pt-3 backdrop-blur lg:hidden" style={{ paddingBottom: "max(12px, env(safe-area-inset-bottom))" }}>
          {chosen.length > 0 ? (
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1 text-[14px] text-ink-secondary"><span className="font-semibold text-ink-primary">{chosen.length} added</span> · new total {money(total + recSum, sym)}</div>
              <button onClick={() => setReview(true)} className={btn} style={{ background: accent, color: onAccent }}>Review</button>
            </div>
          ) : d.awaiting_approval ? (
            <div className="flex items-center gap-3">
              <div className="min-w-0 flex-1 text-[14px] text-ink-secondary">Total <span className="font-semibold tabular-nums text-ink-primary">{fmtDoc(d)}</span></div>
              <button onClick={() => respond("decline")} className={`${btn} border border-slate-300 text-ink-secondary`}>Decline</button>
              <button onClick={() => setSignOpen(true)} className={btn} style={{ background: accent, color: onAccent }}>Review and sign</button>
            </div>
          ) : (
            <div className="flex gap-2">
              <a href={`tel:${d.shop_phone}`} className={`${btn} flex-1`} style={{ background: accent, color: onAccent }}><Phone size={15} /> Call</a>
              {mapHref && <a href={mapHref} target="_blank" rel="noopener" className={`${btn} flex-1 border border-slate-300 text-ink-secondary`}><MapPin size={15} /> Directions</a>}
            </div>
          )}
        </div>
      )}

      {review && (
        <ReviewSheet items={chosen} sym={sym} total={total} accent={accent} onAccent={onAccent} defaultName={d.client_name || ""}
          onClose={() => setReview(false)}
          onConfirm={(name) => decideRecs({ approve_ids: chosen.map((x) => x.id), name }, chosen.length === 1 ? `${chosen[0].title} added to your repair` : `${chosen.length} items added to your repair`)} />
      )}
      {photo && (
        <div role="dialog" aria-label="Photo" className="fixed inset-0 z-50 grid place-items-center bg-black/85 p-4" onClick={() => setPhoto(null)}>
          <img src={photo} alt="" className="max-h-[88vh] max-w-full rounded-xl object-contain" />
          <button aria-label="Close photo" className="absolute right-4 top-4 grid h-10 w-10 place-items-center rounded-full bg-white/15 text-white"><X size={20} /></button>
        </div>
      )}
      {signOpen && <SignModal accent={accent} onAccent={onAccent} onClose={() => setSignOpen(false)} onDone={(sig) => { setSignOpen(false); respond("accept", sig); }} />}
      {toast && <div className="fixed bottom-24 lg:bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-[10px] bg-ink-primary px-4 py-2.5 text-[14px] font-semibold text-white shadow-lg">{toast}</div>}
    </div>
  );
}

// Confirm step: just the chosen items, the new total, and the customer's name.
function ReviewSheet({ items, sym, total, accent, onAccent, defaultName, onClose, onConfirm }: {
  items: Rec[]; sym: string; total: number; accent: string; onAccent: string; defaultName: string;
  onClose: () => void; onConfirm: (name: string) => void;
}) {
  const [name, setName] = useState(defaultName);
  const [busy, setBusy] = useState(false);
  const sum = items.reduce((a, x) => a + x.line_total, 0);
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 sm:p-4" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-label="Confirm your choices" className="w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl bg-white p-6" style={{ paddingBottom: "max(24px, env(safe-area-inset-bottom))" }}>
        <div className="flex items-center justify-between">
          <h2 className="text-[17px] font-semibold text-ink-primary">Add to your repair</h2>
          <button onClick={onClose} aria-label="Close" className="text-ink-tertiary hover:text-ink-primary"><X size={20} /></button>
        </div>
        <ul className="mt-4 divide-y divide-slate-100">
          {items.map((r) => (
            <li key={r.id} className="flex justify-between gap-3 py-2.5 text-[15px]"><span className="text-ink-primary">{r.title}</span><span className="tabular-nums text-ink-primary">{money(r.line_total, sym)}</span></li>
          ))}
        </ul>
        <div className="mt-2 flex justify-between border-t border-slate-200 pt-3 text-[15px] font-semibold text-ink-primary"><span>New total</span><span className="tabular-nums">{money(total + sum, sym)}</span></div>
        <p className="mt-3 text-[14px] leading-relaxed text-ink-tertiary">We'll add these to your repair. Nothing else changes, and you can message us before we start.</p>
        <label className="mt-4 block">
          <span className="text-[13px] text-ink-tertiary">Your name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name"
            className="mt-1 h-11 w-full rounded-[10px] border border-slate-300 bg-white px-3 text-[15px] text-ink-primary outline-none focus:border-slate-400" />
        </label>
        <div className="mt-5 flex gap-2">
          <button onClick={onClose} className="inline-flex h-11 flex-1 items-center justify-center rounded-[10px] border border-slate-300 text-[15px] font-semibold text-ink-secondary">Go back</button>
          <button disabled={busy || !name.trim()} onClick={() => { setBusy(true); onConfirm(name.trim()); }}
            className="inline-flex h-11 flex-[2] items-center justify-center rounded-[10px] text-[15px] font-semibold disabled:opacity-40" style={{ background: accent, color: onAccent }}>
            Confirm, add {money(sum, sym)}
          </button>
        </div>
      </div>
    </div>
  );
}

// Signature capture — canvas + pointer events, no libraries.
function SignModal({ accent, onAccent, onClose, onDone }: { accent: string; onAccent: string; onClose: () => void; onDone: (sig: string) => void }) {
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
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-[17px] font-semibold text-ink-primary">Approve and sign</span>
          <button onClick={onClose} className="text-ink-tertiary hover:text-ink-primary"><X size={18} /></button>
        </div>
        <div className="mb-1.5 text-[13px] text-ink-secondary">Please sign below to approve.</div>
        <canvas ref={ref} className="h-[150px] w-full touch-none rounded-lg border-[1.5px] border-dashed border-primary/40 bg-slate-50" />
        <div className="mt-3 flex justify-end gap-2">
          <button onClick={clear} className="inline-flex h-11 items-center rounded-[10px] border border-slate-300 px-4 text-[15px] font-semibold text-ink-secondary hover:bg-slate-50">Clear</button>
          <button onClick={submit} className="inline-flex h-11 items-center rounded-[10px] px-4 text-[15px] font-semibold" style={{ background: accent, color: onAccent }}>Approve and sign</button>
        </div>
      </div>
    </div>
  );
}
