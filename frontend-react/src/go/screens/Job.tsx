// One job: who, what device, what's wrong, and a status sheet. The status change shows at once and
// rolls back if the server refuses (409 gates explained in plain words). Offline it is queued.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, Check, ChevronLeft, CloudOff, ExternalLink, Phone, RefreshCw, Smartphone, Timer, User, Wrench } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Card, Empty, Page, PrimaryButton, Sheet, SkeletonRows, StatusBadge, humanKey } from "../components/ui";
import { goQueue } from "../lib/offlineQueue";
import { changeStatus } from "../lib/statusChange";
import { haptic } from "../lib/scanner";
import { useQueueStatus, useStatuses, useToast, useWide, type StatusMeta } from "../lib/useGo";
import { utc } from "../lib/time";

type Order = {
  id: string; order_number: string; status: string; is_estimate?: boolean; complaint?: string | null; diagnosis?: string | null;
  total?: number; scheduled_for?: string | null; due_at?: string | null; overdue_by_date?: boolean; status_entered_at?: string | null;
  client?: { id: string; name: string; phone?: string | null; watch_out?: string | null } | null;
  device?: { id: string; brand?: string | null; model?: string | null; serial?: string | null; imei?: string | null; vin?: string | null; watch_out?: string | null } | null;
  assigned_to?: { id: string; name: string } | null;
  lines?: { id: string; description?: string; qty?: number; total?: number; line_type?: string }[];
  status_logs?: { from?: string; to: string; at: string; note?: string | null }[];
};

const GROUP_ORDER = ["new", "in_progress", "pending", "ready", "closed", "dropped"];
const GROUP_LABEL: Record<string, string> = { new: "New", in_progress: "In progress", pending: "Waiting", ready: "Ready", closed: "Done", dropped: "Cancelled" };
const when = (iso?: string | null) => iso ? new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "";

function Detail({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-3 first:pt-0 last:pb-0">
      <span className="mt-0.5 text-ink-tertiary">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-[0.8125rem] text-ink-tertiary">{label}</p>
        <div className="text-[0.9375rem] text-ink-primary break-words">{children}</div>
      </div>
    </div>
  );
}

export default function Job() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const wide = useWide();
  const toast = useToast();
  const { fmt, me } = useMe();
  const [order, setOrder] = useState<Order | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [allowed, setAllowed] = useState<string[] | null>(null);
  const [sheet, setSheet] = useState(false);
  const [pending, setPending] = useState(false);   // a change is on screen but not confirmed yet
  const { list, byKey } = useStatuses(order?.is_estimate ? "estimate" : "work_order");

  const load = useCallback(async () => {
    setErr(null);
    try {
      const o = await api.get<Order>(`/orders/${encodeURIComponent(id)}`);
      setOrder(o);
      api.get<{ items: string[] }>(`/statuses/allowed/${encodeURIComponent(id)}`).then((r) => setAllowed(r.items || [])).catch(() => setAllowed(null));
    } catch (e: any) {
      setErr(e?.status === 404 ? "This job doesn't exist in your shop." : e?.status === 403 ? (e.message || "You can't open this job.") : navigator.onLine ? (e?.message || "Couldn't load the job") : "You're offline and this job isn't saved on the phone yet.");
    }
  }, [id]);
  useEffect(() => { setOrder(null); setAllowed(null); setPending(false); load(); }, [load]);

  // A queued change went through once the queue drains: drop the "not sent" mark and show the server's view.
  const q = useQueueStatus();
  useEffect(() => {
    if (pending && q.pending === 0 && q.online && !q.syncing) { setPending(false); load(); }
  }, [q.pending, q.online, q.syncing]);

  const choices = useMemo(() => {
    const ordered = [...list].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
    const ok = allowed ? new Set(allowed) : null;
    const pick = ordered.filter((s) => !ok || ok.has(s.key));
    const curIdx = ordered.findIndex((s) => s.key === order?.status);
    const next = ordered.slice(curIdx + 1).find((s) => !["closed", "dropped"].includes(s.status_group || "") && (!ok || ok.has(s.key)));
    return { pick, next };
  }, [list, allowed, order?.status]);

  async function pickStatus(s: StatusMeta) {
    if (!order || s.key === order.status) { setSheet(false); return; }
    setSheet(false);
    const from = order.status;
    setPending(true);
    const r = await changeStatus({
      orderId: order.id, from, to: s.key, toName: s.name,
      apply: (key) => setOrder((o) => (o ? { ...o, status: key } : o)),
      mutate: (m, u, b) => goQueue().mutate(m, u, b),
    });
    if (r.kind === "saved") {
      setPending(false);
      haptic();
      if (r.data?.converted_order_id) { toast({ title: "Converted to a work order" }); nav(`/job/${encodeURIComponent(r.data.converted_order_id)}`, { replace: true }); return; }
      toast({ title: `Moved to ${s.name}`, body: r.data?.triggers_payment ? "Take the payment in Tekly on the counter." : undefined });
      load();
    } else if (r.kind === "queued") {
      toast(r.held
        ? { title: "Saved on this phone", body: "This shop only accepts changes from its own network. It will send when you're back on shop Wi-Fi.", tone: "warn" }
        : { title: "Saved on this phone", body: "You're offline. It will send when you're back online.", tone: "warn" });
    } else {
      setPending(false);
      toast({ title: r.title, body: r.message, tone: "error" });
    }
  }

  async function startTimer() {
    if (!order) return;
    try {
      await api.post("/timeclock/start", { order_id: order.id, category: "job" });
      haptic();
      toast({ title: `Timer started on ${order.order_number}` });
    } catch (e: any) { toast({ title: "Couldn't start the timer", body: e?.message, tone: "error" }); }
  }

  const back = !wide && (
    <button onClick={() => (history.length > 1 ? nav(-1) : nav("/today"))} aria-label="Back"
      className="-ml-2 h-11 min-w-[2.75rem] pr-2 inline-flex items-center text-primary font-medium">
      <ChevronLeft size={26} />Back
    </button>
  );
  const refresh = (
    <button onClick={load} aria-label="Refresh" className="h-11 w-11 -mr-2 inline-flex items-center justify-center rounded-full text-ink-secondary active:bg-slate-100"><RefreshCw size={20} /></button>
  );

  if (err) return <Page title="Job" back={back}><Empty icon={<AlertTriangle size={22} />} title="Can't open this job" body={err} action={<Link to="/today" className="font-semibold text-primary inline-flex min-h-[2.75rem] items-center">Back to Today</Link>} /></Page>;
  if (!order) return <Page title="Job" back={back}><SkeletonRows n={2} /></Page>;

  const meta = byKey.get(order.status);
  const device = order.device ? [order.device.brand, order.device.model].filter(Boolean).join(" ") || "Device" : "";
  const ident = order.device ? (order.device.imei ? `IMEI ${order.device.imei}` : order.device.serial ? `Serial ${order.device.serial}` : order.device.vin ? `VIN ${order.device.vin}` : "") : "";
  const watch = [order.client?.watch_out, order.device?.watch_out].filter(Boolean).join(" · ");
  const lines = order.lines || [];

  return (
    <>
      <Page title={order.order_number} back={back} right={refresh}
        subtitle={<span className="inline-flex items-center gap-2 mt-1.5"><StatusBadge meta={meta} statusKey={order.status} pending={pending} />{pending && <span className="inline-flex items-center gap-1 text-[0.8125rem] text-amber-700"><CloudOff size={14} />Not sent yet</span>}{order.is_estimate && <span className="text-[0.8125rem] text-ink-tertiary">Estimate</span>}</span>}>
        {watch && (
          <div className="mb-3 flex gap-2 rounded-2xl border border-amber-200 bg-amber-50 p-3 text-[0.9375rem] text-amber-800">
            <AlertTriangle size={18} className="mt-0.5 shrink-0" /><span>{watch}</span>
          </div>
        )}
        <Card className="p-4">
          <Detail icon={<User size={18} />} label="Customer">{order.client?.name || "Walk-in"}</Detail>
          {device && <Detail icon={<Smartphone size={18} />} label="Device">{device}{ident && <span className="block text-[0.8125rem] text-ink-secondary font-mono">{ident}</span>}</Detail>}
          {(order.complaint || order.diagnosis) && (
            <Detail icon={<Wrench size={18} />} label="Problem">
              {order.complaint}
              {order.diagnosis && <span className="block mt-1 text-ink-secondary">Diagnosis: {order.diagnosis}</span>}
            </Detail>
          )}
        </Card>

        <div className="mt-3 grid grid-cols-2 gap-2.5">
          {order.client?.phone ? (
            <a href={`tel:${order.client.phone.replace(/[^\d+]/g, "")}`} className="min-h-[3rem] rounded-2xl border border-slate-200 bg-white inline-flex items-center justify-center gap-2 font-semibold text-ink-primary active:bg-slate-50">
              <Phone size={18} />Call
            </a>
          ) : <span className="min-h-[3rem] rounded-2xl border border-dashed border-slate-200 inline-flex items-center justify-center text-[0.875rem] text-ink-tertiary">No phone number</span>}
          <a href={`/orders?open=${encodeURIComponent(order.id)}`} className="min-h-[3rem] rounded-2xl border border-slate-200 bg-white inline-flex items-center justify-center gap-2 font-semibold text-ink-primary active:bg-slate-50">
            <ExternalLink size={18} />Full job
          </a>
          {me?.clock_wo_enabled !== false && (
            <button onClick={startTimer} className="col-span-2 min-h-[3rem] rounded-2xl border border-slate-200 bg-white inline-flex items-center justify-center gap-2 font-semibold text-ink-primary active:bg-slate-50">
              <Timer size={18} />Start timer on this job
            </button>
          )}
        </div>

        <Card className="mt-3 p-4 divide-y divide-slate-100">
          {order.assigned_to && <div className="flex justify-between py-2.5 first:pt-0"><span className="text-ink-secondary">Technician</span><span className="font-medium text-ink-primary">{order.assigned_to.name}</span></div>}
          {order.scheduled_for && <div className="flex justify-between py-2.5 first:pt-0"><span className="text-ink-secondary">Scheduled</span><span className="font-medium text-ink-primary">{when(order.scheduled_for)}</span></div>}
          {order.due_at && <div className="flex justify-between py-2.5 first:pt-0"><span className="text-ink-secondary">Due</span><span className={`font-medium ${order.overdue_by_date ? "text-rose-700" : "text-ink-primary"}`}>{when(order.due_at)}{order.overdue_by_date ? " · overdue" : ""}</span></div>}
          {lines.map((l) => (
            <div key={l.id} className="flex justify-between gap-3 py-2.5 first:pt-0">
              <span className="text-ink-primary min-w-0 truncate">{(l.qty ?? 1) !== 1 ? `${l.qty} × ` : ""}{l.description || humanKey(l.line_type || "item")}</span>
              <span className="tabular-nums text-ink-secondary">{fmt(l.total)}</span>
            </div>
          ))}
          <div className="flex justify-between pt-2.5 first:pt-0"><span className="font-semibold text-ink-primary">Total</span><span className="font-semibold tabular-nums text-ink-primary">{fmt(order.total)}</span></div>
        </Card>

        {!!order.status_logs?.length && (
          <div className="mt-5">
            <h2 className="px-1 mb-2 text-[0.8125rem] font-semibold text-ink-tertiary">History</h2>
            <Card className="p-4 space-y-2.5">
              {[...order.status_logs].reverse().slice(0, 4).map((s, i) => (
                <div key={i} className="flex items-center justify-between gap-3 text-[0.875rem]">
                  <StatusBadge meta={byKey.get(s.to)} statusKey={s.to} />
                  <span className="text-ink-tertiary">{utc(s.at)?.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</span>
                </div>
              ))}
            </Card>
          </div>
        )}

        {/* the main action sits in the thumb zone, just above the tab bar */}
        <div className="sticky bottom-[calc(env(safe-area-inset-bottom)+5.75rem)] md:bottom-[calc(env(safe-area-inset-bottom)+5rem)] mt-6 z-10">
          <PrimaryButton onClick={() => setSheet(true)}>Change status</PrimaryButton>
        </div>
      </Page>

      <Sheet open={sheet} onClose={() => setSheet(false)} title="Change status">
        {!list.length ? <div className="px-2"><SkeletonRows n={2} /></div> : (
          <div className="space-y-0.5">
            {choices.next && (
              <>
                <p className="px-3 pt-1 pb-1 text-[0.75rem] font-semibold text-ink-tertiary">Next step</p>
                <StatusOption s={choices.next} current={false} recommended onPick={pickStatus} />
              </>
            )}
            {GROUP_ORDER.map((g) => {
              const items = choices.pick.filter((s) => (s.status_group || "new") === g && s.key !== choices.next?.key);
              if (!items.length) return null;
              return (
                <div key={g}>
                  <p className="px-3 pt-3 pb-1 text-[0.75rem] font-semibold text-ink-tertiary">{GROUP_LABEL[g]}</p>
                  {items.map((s) => <StatusOption key={s.key} s={s} current={s.key === order.status} onPick={pickStatus} />)}
                </div>
              );
            })}
            {allowed && allowed.length <= 1 && <p className="px-3 py-3 text-[0.875rem] text-ink-secondary">Your role can't move this job from here.</p>}
          </div>
        )}
      </Sheet>
    </>
  );
}

function StatusOption({ s, current, recommended, onPick }: { s: StatusMeta; current: boolean; recommended?: boolean; onPick: (s: StatusMeta) => void }) {
  const color = s.color || "#64748b";
  return (
    <button onClick={() => onPick(s)} aria-current={current ? "true" : undefined}
      className="w-full min-h-[3rem] flex items-center gap-3 rounded-xl px-3 text-left active:bg-slate-100"
      style={recommended ? { background: `${color}1a`, boxShadow: `inset 0 0 0 1px ${color}55` } : undefined}>
      <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: color }} />
      <span className="flex-1 text-[1rem] font-medium text-ink-primary">{s.name}</span>
      {current && <Check size={20} className="text-primary" aria-label="Current status" />}
    </button>
  );
}
