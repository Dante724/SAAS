import { Fragment, useEffect, useRef, useState } from "react";
import {
  FileSignature, Check, X, Camera, MessageSquare, Paperclip, RefreshCw,
  Wrench, Box, Receipt, ClipboardList, Download, MapPin, Clock, Phone, Star,
  ThumbsUp, ThumbsDown, ExternalLink, Store, User,
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
type Rec = { id: string; title: string; note?: string; line_total: number; status: string; media_data?: string; item?: string | null; item_order?: number | null };
type Update = { type: string; body?: string; media_data?: string; media_name?: string; at?: string };
type TL = { label: string; at?: string };
type Feedback = {
  system: string; heading?: string; subheading?: string; question?: string;
  clarify_question?: string; bands?: Record<string, string>;
  google_review_url?: string | null; submitted?: boolean; rating?: number | null;
};
type Track = {
  shop_name?: string; order_number?: string; stage_label?: string; is_estimate?: boolean;
  stages?: { label: string }[]; stage_index?: number; awaiting_approval?: boolean;
  device?: string; asset_label?: string; complaint?: string; total?: number;
  total_converted?: number; fx_rate?: number; currency_symbol?: string;
  lines?: Line[]; recommendations?: Rec[]; updates?: Update[]; timeline?: TL[];
  custom_fields?: { label: string; value: string }[];   // fields the shop set to show on the portal
  invoice?: any; has_signature?: boolean; allow_messages?: boolean; messages_until?: string | null; can_new_request?: boolean; allow_recommend?: boolean;
  // 144ix — branding + pickup + review
  logo_data?: string | null; brand_color?: string | null; website?: string | null;
  shop_address?: string | null; shop_phone?: string | null; location_name?: string | null;
  shop_hours?: Record<string, { open?: string; close?: string; closed?: boolean }> | null;
  feedback?: Feedback | null;
};

const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
function todayHours(h?: Track["shop_hours"]) {
  if (!h) return null;
  const d = h[WEEK[(new Date().getDay() + 6) % 7]];
  if (!d || d.closed) return "Closed today";
  if (d.open && d.close) return `Open today ${d.open}–${d.close}`;
  return null;
}

const token = location.pathname.split("/track/")[1]?.replace(/\/$/, "") || "";

function fmtDoc(d: Track) {
  if (d?.currency_symbol && d?.fx_rate && Number(d.fx_rate) !== 1) {
    const v = d.total_converted != null ? d.total_converted : (d.total || 0) * d.fx_rate;
    return d.currency_symbol + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return (d?.currency_symbol || "₹") + Number(d?.total || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}
const money = (n: number, sym = "₹") => sym + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const fmtDate = (s?: string) =>
  s ? new Date(s).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
const initials = (s?: string) =>
  (s || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase() || "R";

function lineIcon(l: Line) {
  if (l.kind === "product" || l.line_type === "part") return <Box size={13} />;
  if (l.kind === "fee" || l.line_type === "fee") return <Receipt size={13} />;
  return <Wrench size={13} />;
}
const UPD_ICON: Record<string, JSX.Element> = {
  comment: <MessageSquare size={15} />, photo: <Camera size={15} />,
  signature: <FileSignature size={15} />, file: <Paperclip size={15} />, system: <RefreshCw size={15} />,
  shop_message: <Store size={15} />, client_message: <User size={15} />,
};
// 144ct — sender label for the two-way message items in the updates feed
const UPD_SENDER: Record<string, string> = { shop_message: "Shop", client_message: "You" };

// soft-tint status pill, keyed to the stage group (same palette as the app)
function stageTint(d: Track): { bg: string; fg: string } {
  const n = (d.stages || []).length || 1, i = d.stage_index || 0, f = i / Math.max(1, n - 1);
  if (d.awaiting_approval) return { bg: "rgba(245,166,35,.16)", fg: "#b7791f" };
  if (i >= n - 1) return { bg: "rgba(74,222,128,.18)", fg: "#1e8a44" };
  if (f < 0.34) return { bg: "rgba(79,142,247,.15)", fg: "#326fd1" };
  if (f < 0.67) return { bg: "rgba(168,85,247,.15)", fg: "#8027cf" };
  return { bg: "rgba(44,212,192,.15)", fg: "#0a9887" };
}

export default function PublicTrack() {
  const [d, setD] = useState<Track | null>(null);
  const [err, setErr] = useState(false);
  const [signOpen, setSignOpen] = useState(false);
  const [recSel, setRecSel] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState("");
  const [reqSent, setReqSent] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number>(0);
  // review capture
  const [fbRating, setFbRating] = useState(0);
  const [fbComment, setFbComment] = useState("");
  const [fbBusy, setFbBusy] = useState(false);
  const [fbResult, setFbResult] = useState<{ message: string; google_review_url?: string | null } | null>(null);

  async function load(silent = false) {
    try {
      const r = await fetch("/api/public/track/" + token);
      if (!r.ok) throw new Error();
      const data: Track = await r.json();
      setD(data);
      setUpdatedAt(Date.now());
      if (!silent) {
        const sel: Record<string, boolean> = {};
        (data.recommendations || []).filter((x) => x.status === "sent").forEach((x) => (sel[x.id] = true));
        setRecSel(sel);
      }
    } catch { if (!silent) setErr(true); }
  }
  useEffect(() => { load(); }, []);
  // #2 — live auto-refresh: poll every 20s, pause while the tab is hidden.
  useEffect(() => {
    let id: ReturnType<typeof setInterval> | null = null;
    const start = () => { if (!id) id = setInterval(() => load(true), 20000); };
    const stop = () => { if (id) { clearInterval(id); id = null; } };
    const onVis = () => (document.hidden ? stop() : (load(true), start()));
    start();
    document.addEventListener("visibilitychange", onVis);
    return () => { stop(); document.removeEventListener("visibilitychange", onVis); };
  }, []);
  function flash(m: string) { setToast(m); setTimeout(() => setToast(null), 3200); }

  async function submitFeedback() {
    if (!fbRating) { flash("Pick a rating first"); return; }
    setFbBusy(true);
    try {
      const r = await fetch("/api/public/track/" + token + "/feedback", {
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
      const r = await fetch("/api/public/track/" + token + "/respond", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, signature }),
      });
      if (!r.ok) throw new Error();
      flash(decision === "accept" ? "Estimate approved — thank you!" : "Estimate declined");
      setTimeout(load, 800);
    } catch { flash("Could not submit your response"); }
  }

  async function submitRecs(approve: boolean) {
    const open = (d?.recommendations || []).filter((x) => x.status === "sent");
    const approve_ids = approve ? open.filter((x) => recSel[x.id]).map((x) => x.id) : [];
    const decline_ids = approve ? open.filter((x) => !recSel[x.id]).map((x) => x.id) : open.map((x) => x.id);
    if (approve && !approve_ids.length) { flash("Select at least one item, or use Decline all"); return; }
    try {
      const r = await fetch("/api/public/track/" + token + "/recommendations/decide", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approve_ids, decline_ids }),
      });
      if (!r.ok) throw new Error();
      flash(approve ? "Thank you — approved" : "Noted, thanks");
      load();
    } catch { flash("Could not submit your choice"); }
  }

  async function sendMessage() {
    const body = msg.trim(); if (!body) return;
    try {
      const r = await fetch("/api/public/track/" + token + "/message", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body }),
      });
      if (!r.ok) throw new Error();
      setMsg(""); flash("Message sent — the shop will see it");
    } catch { flash("Could not send your message"); }
  }

  async function sendNewRequest() {
    const body = msg.trim(); if (!body) return;
    try {
      const r = await fetch("/api/public/track/" + token + "/new-request", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body }),
      });
      if (!r.ok) throw new Error();
      setMsg(""); setReqSent(true);
    } catch { flash("Could not send your request"); }
  }

  if (err)
    return (
      <div className="min-h-screen bg-surface text-ink-primary grid place-items-center px-5">
        <div className="text-center">
          <div className="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-rose-50 text-rose-600"><X size={26} /></div>
          <div className="text-lg font-extrabold">Order not found</div>
          <div className="mt-1.5 text-sm text-ink-tertiary">This tracking link may be invalid or expired.</div>
        </div>
      </div>
    );
  if (!d) return <div className="min-h-screen bg-surface" />;

  const isEst = d.is_estimate === true || (d.awaiting_approval && !d.has_signature);
  const sym = d.currency_symbol || "₹";
  const tint = stageTint(d);
  const accent = d.brand_color || "#0059ba";
  const nStages = d.stages?.length ?? 6;
  const nearEnd = (d.stage_index ?? 0) >= (nStages - 2);   // "Ready for pickup" or later
  const hoursLine = todayHours(d.shop_hours);
  const mapHref = d.shop_address ? "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(d.shop_address) : null;
  const openRecs = (d.recommendations || []).filter((x) => x.status === "sent");
  const decidedRecs = (d.recommendations || []).filter((x) => x.status === "approved" || x.status === "declined");
  const recSum = openRecs.filter((x) => recSel[x.id]).reduce((a, x) => a + x.line_total, 0);
  const recCount = openRecs.filter((x) => recSel[x.id]).length;

  const Pill = (
    <span className="inline-flex items-center gap-1.5 rounded-full px-3 py-[5px] text-[12.5px] font-extrabold"
      style={{ background: tint.bg, color: tint.fg }}>
      <span className="h-[7px] w-[7px] rounded-full" style={{ background: tint.fg }} />{d.stage_label}
    </span>
  );

  const secH = "mb-3 text-[11px] font-extrabold uppercase tracking-[.07em] text-ink-tertiary";
  const sec = "mt-6 border-t border-slate-200 pt-5";

  return (
    <div className="min-h-screen bg-surface text-ink-primary font-sans">
      <div className="mx-auto grid max-w-[940px] items-start gap-5 px-4 py-7 lg:grid-cols-[minmax(0,1fr)_300px]">
        {/* ── document sheet ── */}
        <main className="rounded-xl border border-slate-200 bg-white p-6 sm:p-8">
          {/* letterhead */}
          <div className="flex items-start justify-between gap-4 border-b border-slate-200 pb-5">
            <div className="flex min-w-0 items-center gap-3">
              {d.logo_data
                ? <img src={d.logo_data} alt={d.shop_name || "logo"} className="h-11 w-11 shrink-0 rounded-xl object-contain" style={{ background: "#fff" }} />
                : <div className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-[17px] font-extrabold text-white" style={{ background: accent }}>{initials(d.shop_name)}</div>}
              <div className="min-w-0">
                <div className="text-[15px] font-extrabold text-ink-primary">{d.shop_name}</div>
                <div className="text-xs font-semibold text-ink-tertiary">Repair {isEst ? "estimate" : "tracking"}</div>
              </div>
            </div>
            <div className="min-w-0 text-right text-[12.5px] leading-relaxed text-ink-tertiary">
              <div className="text-sm font-extrabold text-ink-primary">{d.order_number}</div>
              {d.device && <div>{d.device}</div>}
            </div>
          </div>

          {/* title + status */}
          <div className="mt-5 mb-4 flex flex-wrap items-center gap-2.5">
            <div className="text-2xl font-extrabold tracking-[-.5px] text-ink-primary">
              {isEst ? "Estimate" : "Work order"} {d.order_number}
            </div>
            {Pill}
          </div>

          {/* details */}
          <div className="grid grid-cols-2 gap-2.5">
            <Detail label={d.asset_label || "Device"} value={d.device || "—"} />
            <Detail label="Estimated total" value={<span className="text-base font-extrabold text-primary">{fmtDoc(d)}</span>} />
            {d.complaint && (
              <div className="col-span-2 rounded-lg bg-slate-50 p-3">
                <div className="text-[11px] font-bold uppercase tracking-wide text-ink-tertiary">Reported issue</div>
                <div className="mt-0.5 text-[13px] font-semibold text-ink-primary">{d.complaint}</div>
              </div>
            )}
            {(d.custom_fields || []).map((f) => <Detail key={f.label} label={f.label} value={f.value} />)}
          </div>

          {/* status stepper */}
          <div className={sec}>
            <div className={secH}>Status</div>
            <div className="flex items-center">
              {(d.stages || []).map((s, i) => {
                const done = i < (d.stage_index || 0), cur = i === (d.stage_index || 0);
                return (
                  <div key={i} className="relative flex flex-1 flex-col items-center gap-1.5">
                    {i < (d.stages!.length - 1) && (
                      <div className="absolute left-1/2 top-[13px] z-0 h-0.5 w-full"
                        style={{ background: done ? accent : "#c2c6d5" }} />
                    )}
                    <div className="z-10 grid h-[26px] w-[26px] place-items-center rounded-full text-[12px] font-extrabold"
                      style={done || cur
                        ? { background: accent, color: "#fff", boxShadow: cur ? `0 0 0 4px ${accent}2e` : undefined }
                        : { background: "#fff", color: "#6c7a99", border: "1.5px solid #c2c6d5" }}>
                      {done ? <Check size={13} /> : i + 1}
                    </div>
                    <div className="max-w-[64px] text-center text-[9.5px] font-bold leading-tight"
                      style={{ color: done || cur ? "#14213f" : "#6c7a99" }}>{s.label}</div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* #3 — review capture (settings active; near completion or already rated) */}
          {d.feedback && (nearEnd || d.feedback.submitted || fbResult) && (
            <div className={sec}>
              <div className={secH}>{d.feedback.heading || "How did we do?"}</div>
              {fbResult ? (
                <div>
                  <div className="flex items-center gap-2 text-[14px] font-semibold text-ink-primary"><Check size={16} className="text-emerald-600" /> {fbResult.message}</div>
                  {fbResult.google_review_url && (
                    <a href={fbResult.google_review_url} target="_blank" rel="noopener"
                      className="mt-3 inline-flex items-center gap-1.5 rounded-lg py-2 px-3.5 text-sm font-bold text-white" style={{ background: accent }}>
                      <ExternalLink size={15} /> Leave a public review
                    </a>
                  )}
                </div>
              ) : d.feedback.submitted ? (
                <div className="text-[13px] text-ink-secondary">Thanks for your feedback{d.feedback.rating != null ? ` — you rated ${d.feedback.rating}${d.feedback.system === "five_point" ? "/5" : ""}.` : "."}</div>
              ) : (
                <>
                  {d.feedback.question && <div className="mb-2.5 text-[13px] text-ink-secondary">{d.feedback.question}</div>}
                  {d.feedback.system === "five_point" && (
                    <div className="flex gap-1.5">
                      {[1, 2, 3, 4, 5].map((n) => (
                        <button key={n} onClick={() => setFbRating(n)} aria-label={`${n} star`} className="p-0.5">
                          <Star size={30} style={{ fill: n <= fbRating ? "#f5a623" : "transparent", color: n <= fbRating ? "#f5a623" : "#c2c6d5" }} />
                        </button>
                      ))}
                    </div>
                  )}
                  {d.feedback.system === "like" && (
                    <div className="flex gap-3">
                      <button onClick={() => setFbRating(1)} className={"flex items-center gap-1.5 rounded-lg border px-4 py-2 text-sm font-bold " + (fbRating === 1 ? "border-emerald-500 text-emerald-700" : "border-slate-300 text-ink-secondary")}><ThumbsUp size={16} /> Good</button>
                      <button onClick={() => setFbRating(-1)} className={"flex items-center gap-1.5 rounded-lg border px-4 py-2 text-sm font-bold " + (fbRating === -1 ? "border-rose-500 text-rose-600" : "border-slate-300 text-ink-secondary")}><ThumbsDown size={16} /> Bad</button>
                    </div>
                  )}
                  {d.feedback.system === "nps" && (
                    <div className="flex flex-wrap gap-1.5">
                      {Array.from({ length: 11 }, (_, n) => (
                        <button key={n} onClick={() => setFbRating(n)} className={"h-9 w-9 rounded-lg border text-sm font-bold " + (fbRating === n ? "text-white" : "border-slate-300 text-ink-secondary")} style={fbRating === n ? { background: accent, borderColor: accent } : undefined}>{n}</button>
                      ))}
                    </div>
                  )}
                  <textarea value={fbComment} onChange={(e) => setFbComment(e.target.value)} rows={2}
                    placeholder={d.feedback.clarify_question || "Tell us more (optional)"}
                    className="mt-3 w-full resize-y rounded-lg border border-slate-300 bg-white p-2.5 text-sm text-ink-primary outline-none focus:border-primary" />
                  <button onClick={submitFeedback} disabled={fbBusy || !fbRating}
                    className="mt-2 rounded-lg px-3.5 py-2 text-sm font-bold text-white disabled:opacity-60" style={{ background: accent }}>
                    {fbBusy ? "Sending…" : "Submit rating"}
                  </button>
                </>
              )}
            </div>
          )}

          {/* recommendations */}
          {d.allow_recommend !== false && (openRecs.length > 0 || decidedRecs.length > 0) && (
            <div className={sec}>
              <div className="flex items-center gap-1.5 text-[13px] font-extrabold text-ink-primary"><ClipboardList size={15} /> Recommended for your repair</div>
              {openRecs.length > 0 && (
                <>
                  <div className="mt-1 mb-3 text-[12.5px] text-ink-tertiary">Pick what you'd like us to do, then approve.</div>
                  <div className="flex flex-col gap-2">
                    {byItem(openRecs).map((g) => (<div key={g.item || "_job"} className="flex flex-col gap-2">
                    {openRecs.some((x) => x.item) && <div className="mt-1 text-[11.5px] font-extrabold uppercase tracking-wide text-ink-tertiary">{g.item ? `For your ${g.item}` : "For the whole job"}</div>}
                    {g.recs.map((r) => (
                      <label key={r.id} className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5 hover:bg-slate-50">
                        <input type="checkbox" className="h-[19px] w-[19px] accent-primary" checked={!!recSel[r.id]}
                          onChange={(e) => setRecSel((s) => ({ ...s, [r.id]: e.target.checked }))} />
                        {r.media_data && <img src={r.media_data} alt="" className="h-10 w-10 shrink-0 rounded-lg object-cover" />}
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className="text-sm font-bold text-ink-primary">{r.title}</span>
                          {r.note && <span className="text-xs leading-snug text-ink-tertiary">{r.note}</span>}
                        </span>
                        <span className="whitespace-nowrap text-sm font-extrabold text-primary">{money(r.line_total, sym)}</span>
                      </label>
                    ))}
                    </div>))}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2.5">
                    <span className="text-[13px] font-bold text-ink-secondary">{recCount ? `${recCount} selected · ${money(recSum, sym)}` : "Nothing selected"}</span>
                    <div className="flex gap-2">
                      <button onClick={() => submitRecs(false)} className="rounded-lg border border-slate-300 px-3.5 py-2 text-sm font-bold text-ink-secondary hover:bg-slate-50">Decline all</button>
                      <button onClick={() => submitRecs(true)} className="rounded-lg bg-primary px-3.5 py-2 text-sm font-bold text-white hover:opacity-90">{recCount ? `Approve — ${money(recSum, sym)}` : "Approve selected"}</button>
                    </div>
                  </div>
                </>
              )}
              {decidedRecs.length > 0 && (
                <div className="mt-2 flex flex-col gap-2">
                  {decidedRecs.map((r) => (
                    <div key={r.id} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5 opacity-90">
                      <span className={"grid h-[22px] w-[22px] shrink-0 place-items-center rounded-full text-white " + (r.status === "approved" ? "bg-emerald-600" : "bg-rose-500")}>
                        {r.status === "approved" ? <Check size={13} /> : <X size={13} />}
                      </span>
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="text-sm font-bold text-ink-primary">{r.title}</span>
                        {(r.note || r.item) && <span className="text-xs leading-snug text-ink-tertiary">{[r.item ? `For your ${r.item}` : "", r.note || ""].filter(Boolean).join(" · ")}</span>}
                      </span>
                      <span className="whitespace-nowrap text-sm font-extrabold text-primary">{money(r.line_total, sym)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* itemized estimate as a table */}
          {d.lines && d.lines.length > 0 && (
            <div className={sec}>
              <div className={secH}>Itemized {isEst ? "estimate" : "summary"}</div>
              <table className="w-full border-collapse text-[13px]">
                <thead>
                  <tr className="text-[10.5px] uppercase tracking-wide text-ink-tertiary">
                    <th className="border-b-2 border-slate-300 px-1.5 pb-2 text-left font-extrabold">Description</th>
                    <th className="border-b-2 border-slate-300 px-1.5 pb-2 text-right font-extrabold">Qty</th>
                    <th className="border-b-2 border-slate-300 px-1.5 pb-2 text-right font-extrabold">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {d.lines.map((l, i) => (<Fragment key={i}>
                    {d.lines!.some((x) => x.item) && (i === 0 || (d.lines![i - 1].item || null) !== (l.item || null)) && (
                      <tr><td colSpan={3} className="px-1.5 pb-1 pt-3 text-[11.5px] font-extrabold uppercase tracking-wide text-ink-tertiary">{l.item || "Whole job"}</td></tr>
                    )}
                    <tr>
                      <td className="border-b border-slate-200 px-1.5 py-2.5 align-top">
                        <span className="flex items-center gap-2 font-semibold text-ink-primary">
                          <span className="grid h-[22px] w-[22px] place-items-center rounded-md bg-blue-50 text-primary">{lineIcon(l)}</span>{l.label}
                        </span>
                      </td>
                      <td className="border-b border-slate-200 px-1.5 py-2.5 text-right align-top text-ink-secondary">{l.qty != null ? l.qty : 1}</td>
                      <td className="border-b border-slate-200 px-1.5 py-2.5 text-right align-top font-bold text-ink-primary">{sym}{Number(l.amount).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                    </tr>
                  </Fragment>))}
                </tbody>
              </table>
              <div className="mt-1.5 flex justify-end">
                <div className="min-w-[240px]">
                  <div className="mt-1.5 flex justify-between border-t-2 border-slate-300 px-1.5 pt-2.5 text-[17px] font-extrabold text-ink-primary">
                    <span>Total</span><span>{fmtDoc(d)}</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* updates */}
          {(d.updates || []).length > 0 && (
            <div className={sec}>
              <div className={secH}>Updates</div>
              <div className="flex flex-col">
                {d.updates!.map((u, i) => (
                  <div key={i} className="flex gap-3 border-b border-slate-100 py-3 last:border-0">
                    <div className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-lg bg-blue-50 text-primary">{UPD_ICON[u.type] || <MessageSquare size={15} />}</div>
                    <div className="min-w-0 flex-1">
                      {UPD_SENDER[u.type] && <div className="text-[11px] font-bold uppercase tracking-wide text-ink-tertiary">{UPD_SENDER[u.type]}</div>}
                      {u.body && <div className="text-[13px] font-semibold text-ink-primary">{u.body}</div>}
                      {u.type === "photo" && u.media_data && <img src={u.media_data} alt="" className="mt-2 max-w-[160px] rounded-lg border border-slate-200" />}
                      {u.type === "signature" && u.media_data && <img src={u.media_data} alt="" className="mt-2 max-w-[160px] rounded-lg border border-slate-200 bg-white" />}
                      {u.type === "file" && u.media_data && (
                        <a href={u.media_data} download={u.media_name || "file"} className="mt-1 inline-flex items-center gap-1.5 text-sm font-semibold text-primary"><Paperclip size={15} /> {u.media_name || "Download file"}</a>
                      )}
                      <div className="mt-0.5 text-[11px] text-ink-tertiary">{fmtDate(u.at)}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* invoice */}
          {d.invoice && (
            <div className={sec}>
              <div className={secH}>Invoice</div>
              <div className="flex items-baseline justify-between gap-3">
                <div>
                  <div className="font-bold text-ink-primary">{d.invoice.invoice_number || "Invoice"}</div>
                  <div className="text-xs capitalize text-ink-tertiary">{d.invoice.status || ""}</div>
                </div>
                <div className="text-right">
                  <div className="text-base font-extrabold text-ink-primary">{sym}{Number(d.invoice.total_converted != null ? d.invoice.total_converted : d.invoice.total || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div>
                  {d.invoice.balance_due > 0
                    ? <div className="text-xs text-amber-700">Balance {sym}{Number(d.invoice.balance_due).toLocaleString(undefined, { maximumFractionDigits: 2 })}</div>
                    : <div className="text-xs text-emerald-600">Paid</div>}
                </div>
              </div>
              {d.invoice.einvoice_available && (
                <>
                  <a href={`/api/public/track/${token}/einvoice.xml`} download className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3.5 py-2 text-sm font-bold text-ink-secondary hover:bg-slate-50"><Receipt size={15} /> Download e-invoice (XML)</a>
                  <div className="mt-1.5 text-[11px] text-ink-tertiary">Standards-based UBL 2.1 electronic invoice{d.invoice.einvoice_number ? ` · ${d.invoice.einvoice_number}` : ""}</div>
                </>
              )}
            </div>
          )}

          {/* progress history */}
          <div className={sec}>
            <div className={secH}>Progress history</div>
            <div className="flex flex-col gap-3">
              {(d.timeline || []).length
                ? d.timeline!.map((t, i) => (
                  <div key={i} className="flex gap-3">
                    <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary" />
                    <div><div className="text-[13px] font-semibold text-ink-primary">{t.label}</div><div className="text-[11px] text-ink-tertiary">{fmtDate(t.at)}</div></div>
                  </div>
                ))
                : <div className="text-[13px] text-ink-tertiary">No updates yet</div>}
            </div>
          </div>

          {/* message */}
          {d.allow_messages !== false && (
            <div className={sec}>
              <div className={secH}>Message the shop</div>
              {d.messages_until && (
                <p className="mb-2 text-[13px] text-ink-tertiary">Questions about this repair? You can message the shop until {new Date(d.messages_until).toLocaleDateString()}.</p>
              )}
              <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={2} placeholder="Ask a question or add a note…"
                className="w-full resize-y rounded-lg border border-slate-300 bg-white p-2.5 text-sm text-ink-primary outline-none focus:border-primary" />
              <button onClick={sendMessage} className="mt-2 rounded-lg bg-primary px-3.5 py-2 text-sm font-bold text-white hover:opacity-90">Send message</button>
            </div>
          )}
          {d.allow_messages === false && d.can_new_request && (
            <div className={sec}>
              <div className={secH}>Need something else?</div>
              {reqSent ? (
                <div className="flex items-center gap-2 text-[14px] font-semibold text-ink-primary"><Check size={16} className="text-emerald-600" /> Request sent. The shop will contact you.</div>
              ) : (<>
                <p className="mb-2 text-[13px] text-ink-tertiary">This repair is complete. Describe the new problem and the shop will get back to you.</p>
                <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={2} placeholder="What's going on?"
                  className="w-full resize-y rounded-lg border border-slate-300 bg-white p-2.5 text-sm text-ink-primary outline-none focus:border-primary" />
                <button onClick={sendNewRequest} className="mt-2 rounded-lg bg-primary px-3.5 py-2 text-sm font-bold text-white hover:opacity-90">Start a new request</button>
              </>)}
            </div>
          )}
        </main>

        {/* ── sticky rail ── */}
        <aside className="flex flex-col gap-3 order-first lg:order-none lg:sticky lg:top-5">
          <div className="rounded-xl border border-slate-200 bg-white p-[18px]" style={{ borderTop: `3px solid ${accent}` }}>
            <div className="text-[11px] font-extrabold uppercase tracking-[.07em] text-ink-tertiary">
              {d.awaiting_approval ? (isEst ? "Estimate total" : "Total due") : "Total"}
            </div>
            <div className="mt-0.5 text-[30px] font-extrabold leading-none tracking-[-.6px] text-ink-primary">{fmtDoc(d)}</div>
            {d.awaiting_approval ? (
              <>
                <div className="mt-3.5 flex flex-col gap-2">
                  <button onClick={() => setSignOpen(true)} className="w-full rounded-lg py-2.5 text-sm font-bold text-white" style={{ background: accent }}>Review &amp; sign</button>
                  <button onClick={() => respond("decline")} className="w-full rounded-lg border border-slate-300 py-2.5 text-sm font-bold text-ink-secondary hover:bg-slate-50">Decline</button>
                </div>
                <div className="mt-3 text-xs leading-relaxed text-ink-tertiary">Secure e-signature — you'll receive a signed PDF copy.</div>
              </>
            ) : (
              <div className="mt-3">{Pill}</div>
            )}
            <div className="mt-3 flex items-center gap-1.5 text-[11px] text-ink-tertiary" title={updatedAt ? new Date(updatedAt).toLocaleTimeString() : ""}>
              <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: "#22c55e" }} />
              Live · updates automatically
            </div>
          </div>

          {/* #5 — pickup logistics once the job is ready */}
          {nearEnd && (d.shop_address || d.shop_phone) && (
            <div className="rounded-xl border border-slate-200 bg-white p-[18px]">
              <div className="flex items-center gap-1.5 text-[11px] font-extrabold uppercase tracking-[.07em] text-ink-tertiary"><MapPin size={13} /> Pickup</div>
              {d.location_name && <div className="mt-2 text-sm font-bold text-ink-primary">{d.location_name}</div>}
              {d.shop_address && <div className="mt-0.5 text-[13px] text-ink-secondary">{d.shop_address}</div>}
              {hoursLine && <div className="mt-1 flex items-center gap-1.5 text-[12px] text-ink-tertiary"><Clock size={12} /> {hoursLine}</div>}
              <div className="mt-3 flex flex-col gap-2">
                {mapHref && <a href={mapHref} target="_blank" rel="noopener" className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-300 py-2 text-sm font-bold text-ink-secondary hover:bg-slate-50"><MapPin size={15} /> Directions</a>}
                {d.shop_phone && <a href={`tel:${d.shop_phone}`} className="flex w-full items-center justify-center gap-1.5 rounded-lg py-2 text-sm font-bold text-white" style={{ background: accent }}><Phone size={15} /> Call the shop</a>}
              </div>
            </div>
          )}

          {d.has_signature && (
            <div className="rounded-xl border border-slate-200 bg-white p-[18px]">
              <div className="text-[11px] font-extrabold uppercase tracking-[.07em] text-ink-tertiary">Signed approval</div>
              <a href={`/api/public/track/${token}/certificate.pdf`} target="_blank" rel="noopener"
                className="mt-2.5 flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-300 py-2.5 text-sm font-bold text-ink-secondary hover:bg-slate-50"><Download size={15} /> Download PDF</a>
            </div>
          )}
        </aside>
      </div>

      <div className="py-6 text-center text-[11.5px] text-ink-tertiary">Powered by {d.shop_name} · RepairOS</div>

      {signOpen && <SignModal onClose={() => setSignOpen(false)} onDone={(sig) => { setSignOpen(false); respond("accept", sig); }} />}
      {toast && <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-ink-primary px-4 py-2.5 text-sm font-semibold text-white shadow-lg">{toast}</div>}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <div className="text-[11px] font-bold uppercase tracking-wide text-ink-tertiary">{label}</div>
      <div className="mt-0.5 text-[15px] font-bold text-ink-primary">{value}</div>
    </div>
  );
}

// Signature capture — canvas + pointer events, no libraries.
function SignModal({ onClose, onDone }: { onClose: () => void; onDone: (sig: string) => void }) {
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
          <span className="text-base font-extrabold text-ink-primary">Approve estimate</span>
          <button onClick={onClose} className="text-ink-tertiary hover:text-ink-primary"><X size={18} /></button>
        </div>
        <div className="mb-1.5 text-[13px] text-ink-secondary">Please sign below to approve.</div>
        <canvas ref={ref} className="h-[150px] w-full touch-none rounded-lg border-[1.5px] border-dashed border-primary/40 bg-slate-50" />
        <div className="mt-3 flex justify-end gap-2">
          <button onClick={clear} className="rounded-lg border border-slate-300 px-3.5 py-2 text-sm font-bold text-ink-secondary hover:bg-slate-50">Clear</button>
          <button onClick={submit} className="rounded-lg py-2 px-3.5 text-sm font-bold text-white" style={{ background: "linear-gradient(135deg,#2c72d9,#6848be)" }}>Approve &amp; sign</button>
        </div>
      </div>
    </div>
  );
}
