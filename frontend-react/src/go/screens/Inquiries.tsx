// Inquiries: open leads, late ones first. Tap one to call back, start on it, mark it lost or turn
// it into a job. A missed-call alert opens /inquiries?lead=<id> straight onto that inquiry.
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowRight, Inbox, Phone, PhoneMissed, Play, RefreshCw, Search, XCircle } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Empty, Group, Page, QuietRow, Sheet, SkeletonRows } from "../components/ui";
import { INQUIRIES_CHANGED } from "../components/InquirySheet";
import { goQueue } from "../lib/offlineQueue";
import { announceChange, useToast } from "../lib/useGo";
import { haptic } from "../lib/scanner";
import { utc } from "../lib/time";
import {
  STATUS_LABEL, callsText, convertInquiry, deviceText, loadPage, replyBy, setInquiryStatus, sortInquiries, telHref,
  type Inquiry,
} from "../lib/inquiries";

export function InquiryRow({ i, onOpen, now }: { i: Inquiry; onOpen: (i: Inquiry) => void; now?: Date }) {
  const when = replyBy(i, now);
  const calls = callsText(i.call_count);
  const detail = [i.lead_type, deviceText(i)].filter(Boolean).join(" · ") || i.inquiry || i.phone || "";
  return (
    <button type="button" onClick={() => onOpen(i)} className="w-full flex min-h-16 flex-col justify-center gap-0.5 px-4 py-2.5 text-left active:bg-slate-100">
      <span className="flex items-baseline gap-3">
        <span className="min-w-0 flex-1 truncate text-go-body font-semibold text-ink-primary">{i.name || "No name"}</span>
        {when && <span className={`shrink-0 text-go-sub tabular-nums ${when.late ? "font-semibold text-rose-700" : "text-ink-secondary"}`}>{when.text}</span>}
      </span>
      <span className="flex items-baseline gap-3 text-go-sub text-ink-secondary">
        <span className="min-w-0 flex-1 truncate">{detail}</span>
        {calls && <span className="shrink-0 inline-flex items-center gap-1 font-medium text-amber-800"><PhoneMissed size={13} />{calls}</span>}
        {!calls && i.status === "in_progress" && <span className="shrink-0 text-go-caption">{STATUS_LABEL.in_progress}</span>}
      </span>
    </button>
  );
}

function Detail({ i, onClose, onChanged }: { i: Inquiry | null; onClose: () => void; onChanged: (i: Inquiry | null) => void }) {
  const { can } = useMe();
  const nav = useNavigate();
  const toast = useToast();
  const [busy, setBusy] = useState<"" | "start" | "lost" | "convert">("");
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(navigator.onLine);
    window.addEventListener("online", on); window.addEventListener("offline", on);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", on); };
  }, []);
  if (!i) return <Sheet open={false} onClose={onClose} title="Inquiry"><span /></Sheet>;
  const tel = telHref(i.phone);
  const when = replyBy(i);
  const isOpen = i.status === "new" || i.status === "in_progress";

  async function status(next: "in_progress" | "lost") {
    if (!i) return;
    setBusy(next === "lost" ? "lost" : "start");
    try {
      const r = await setInquiryStatus((m, u, b) => goQueue().mutate(m, u, b), i.id, next);
      if (!r.ok) { toast({ title: "Couldn't update the inquiry", body: r.data?.detail && String(r.data.detail), tone: "error" }); return; }
      haptic();
      toast(next === "lost" ? { title: "Marked lost" } : { title: "You're on it" });
      onChanged(next === "lost" ? null : { ...i, status: next });
    } finally { setBusy(""); }
  }

  async function convert() {
    if (!i) return;
    setBusy("convert");
    try {
      const r = await convertInquiry(api.post, i.id);
      haptic();
      toast({ title: r.number ? `Job ${r.number} created` : "Job created" });
      announceChange("jobs");
      onChanged(null);
      nav(r.path);
    } catch (e: any) {
      toast({ title: "Couldn't make the job", body: e?.message, tone: "error" });
    } finally { setBusy(""); }
  }

  const rows: [string, string][] = ([
    ["Type", i.lead_type || ""],
    ["Device", deviceText(i)],
    ["Serial", i.serial || ""],
    ["Phone", i.phone || ""],
    ["Calls", callsText(i.call_count)],
    ["Came in", utc(i.created_at)?.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }) || ""],
  ] as [string, string][]).filter(([, v]) => v);

  return (
    <Sheet open onClose={onClose} title={i.name || "Inquiry"}>
      <div className="px-2 space-y-4">
        <div className="flex items-center gap-2 text-go-sub">
          <span className="rounded-full border-[0.5px] border-slate-200 px-2.5 py-1 font-semibold text-ink-secondary">{STATUS_LABEL[i.status] || i.status}</span>
          {when && <span className={`tabular-nums ${when.late ? "font-semibold text-rose-700" : "text-ink-secondary"}`}>{when.text}</span>}
        </div>
        {i.inquiry && <p className="rounded-xl bg-slate-50 px-4 py-3 text-go-body text-ink-primary whitespace-pre-wrap">{i.inquiry}</p>}
        {rows.length > 0 && (
          <Group>
            {rows.map(([k, v]) => (
              <div key={k} className="min-h-[2.75rem] flex items-center gap-3 px-4 py-2">
                <span className="w-20 shrink-0 text-go-sub text-ink-tertiary">{k}</span>
                <span className={`min-w-0 flex-1 truncate text-go-body text-ink-primary ${k === "Serial" ? "font-mono" : ""}`}>{v}</span>
              </div>
            ))}
          </Group>
        )}
        {isOpen && (
          <div className="space-y-2 pt-1">
            {tel && (
              <a href={tel} className="min-h-[3.25rem] w-full rounded-xl border-[0.5px] border-slate-200 bg-white px-5 text-go-body font-semibold text-ink-primary active:bg-slate-50 inline-flex items-center justify-center gap-2">
                <Phone size={18} className="text-primary" />Call {i.phone}
              </a>
            )}
            <div className={`grid gap-2 ${i.status === "new" ? "grid-cols-2" : "grid-cols-1"}`}>
              {i.status === "new" && (
                <button onClick={() => status("in_progress")} disabled={!!busy}
                  className="min-h-[3.25rem] rounded-xl border-[0.5px] border-slate-200 bg-white text-go-body font-semibold text-ink-primary active:bg-slate-50 inline-flex items-center justify-center gap-2 disabled:opacity-50">
                  <Play size={17} />{busy === "start" ? "Starting…" : "Start"}
                </button>
              )}
              <button onClick={() => status("lost")} disabled={!!busy}
                className="min-h-[3.25rem] rounded-xl border-[0.5px] border-slate-200 bg-white text-go-body font-semibold text-rose-700 active:bg-slate-50 inline-flex items-center justify-center gap-2 disabled:opacity-50">
                <XCircle size={17} />{busy === "lost" ? "Saving…" : "Mark lost"}
              </button>
            </div>
            {can("can_create_tickets") && (
              <>
                <button onClick={convert} disabled={!!busy || !online}
                  className="min-h-[3.25rem] w-full rounded-xl bg-primary px-5 text-go-body font-semibold text-white active:bg-primary-container transition-colors disabled:opacity-50 inline-flex items-center justify-center gap-2">
                  {busy === "convert" ? "Making the job…" : <>Convert to job<ArrowRight size={18} /></>}
                </button>
                {!online && <p className="px-1 text-go-sub text-ink-secondary">Making a job needs a connection.</p>}
              </>
            )}
          </div>
        )}
        {i.status === "won" && i.converted_order_id && (
          <button onClick={() => nav(`/job/${encodeURIComponent(i.converted_order_id!)}`)} className="min-h-[3.25rem] w-full rounded-xl bg-primary text-go-body font-semibold text-white inline-flex items-center justify-center gap-2">
            Open job {i.converted_order_number || ""}<ArrowRight size={18} />
          </button>
        )}
      </div>
    </Sheet>
  );
}

export default function Inquiries() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Inquiry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [more, setMore] = useState(false);
  const [page, setPage] = useState(1);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<Inquiry | null>(null);
  const seq = useRef(0);

  const load = useCallback(async (query: string) => {
    const n = ++seq.current;
    setBusy(true); setErr(null);
    try {
      const r = await loadPage(api.get, query, 1);
      if (n !== seq.current) return;
      setItems(r.items); setTotal(r.total); setMore(r.more); setPage(1);
    } catch (e: any) {
      if (n === seq.current) setErr(navigator.onLine ? (e?.message || "Couldn't load inquiries") : "You're offline.");
    } finally { if (n === seq.current) setBusy(false); }
  }, []);

  useEffect(() => { const t = setTimeout(() => load(q), q ? 300 : 0); return () => clearTimeout(t); }, [q, load]);
  useEffect(() => {
    const h = () => load(q);
    window.addEventListener(INQUIRIES_CHANGED, h);
    // an inquiry made offline shows up once the queue has sent it
    const off = goQueue().subscribe((s) => { if (s.justFlushed && s.online) h(); });
    return () => { window.removeEventListener(INQUIRIES_CHANGED, h); off(); };
  }, [q, load]);

  async function next() {
    if (!items || busy) return;
    setBusy(true);
    try {
      const r = await loadPage(api.get, q, page + 1, items);
      setItems(r.items); setTotal(r.total); setMore(r.more); setPage(page + 1);
    } catch { setMore(false); } finally { setBusy(false); }
  }

  // ?lead=<id> (a missed-call alert): open that inquiry, from the list or straight from the server
  const leadId = params.get("lead");
  useEffect(() => {
    if (!leadId) return;
    const hit = items?.find((x) => x.id === leadId);
    if (hit) { setOpen(hit); return; }
    let live = true;
    api.get<Inquiry>(`/leads/${encodeURIComponent(leadId)}`).then((x) => { if (live) setOpen(x); }).catch(() => { /* silent: deep-linked inquiry just doesn't open; the list still shows */ });
    return () => { live = false; };
  }, [leadId]); // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
    setOpen(null);
    if (leadId) { params.delete("lead"); setParams(params, { replace: true }); }
  };
  const changed = (i: Inquiry | null) => {
    if (!open) return;
    if (i) { setOpen(i); setItems((l) => l?.map((x) => (x.id === i.id ? i : x)) ?? l); }
    else { setItems((l) => l?.filter((x) => x.id !== open.id) ?? l); setTotal((t) => Math.max(0, t - 1)); close(); }
  };

  const late = (items || []).filter((i) => replyBy(i)?.late).length;
  const refresh = (
    <button onClick={() => load(q)} aria-label="Refresh" className="h-11 w-11 -mr-2 inline-flex items-center justify-center rounded-full text-ink-secondary active:bg-slate-100">
      <RefreshCw size={20} className={busy ? "animate-spin" : ""} />
    </button>
  );
  const sorted = items ? sortInquiries(items) : [];

  return (
    <Page title="Inquiries" right={refresh}
      subtitle={items ? (total ? <>{total} open{late ? <span className="text-rose-700 font-semibold"> · {late} late</span> : null}</> : "Nothing open") : undefined}>
      <label className="relative mb-4 block">
        <Search size={18} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-tertiary" />
        <input value={q} onChange={(e) => setQ(e.target.value)} type="search" placeholder="Name, phone or serial" aria-label="Search inquiries"
          className="w-full min-h-[2.75rem] rounded-xl border-[0.5px] border-slate-200 bg-white pl-10 pr-4 text-go-body text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary focus:ring-4 focus:ring-primary/15" />
      </label>
      {err && !items && <Empty icon={<AlertTriangle size={22} />} title="Couldn't load" body={err} action={<button onClick={() => load(q)} className="font-semibold text-primary min-h-[2.75rem] px-4">Try again</button>} />}
      {err && items && <p className="mb-3 rounded-xl bg-amber-50 border-[0.5px] border-amber-200 px-3 py-2 text-go-sub text-amber-800">{err}</p>}
      {!items && !err && <SkeletonRows />}
      {items && !sorted.length && (q
        ? <QuietRow>No open inquiries match “{q}”.</QuietRow>
        : <Empty icon={<Inbox size={22} />} title="No open inquiries" body="Missed calls and questions about a repair show up here. Add one with +." />)}
      {sorted.length > 0 && <Group>{sorted.map((i) => <InquiryRow key={i.id} i={i} onOpen={setOpen} />)}</Group>}
      {more && (
        <button onClick={next} disabled={busy} className="mt-3 w-full min-h-[2.75rem] font-semibold text-primary disabled:opacity-50">
          {busy ? "Loading…" : `Show more (${total - (items?.length || 0)} left)`}
        </button>
      )}
      <Detail i={open} onClose={close} onChanged={changed} />
    </Page>
  );
}
