// One job: who, what device, what's wrong; photos, items and the customer's signature; and one main
// button that does the next step (Mark Ready → Finish & take payment). Ordinary status changes show
// at once and roll back if refused (409 gates in plain words), and are queued offline. Finishing and
// payment are online only: pay first, then close, so a job is never closed on a payment that failed.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, Camera, Check, ChevronDown, ChevronLeft, CloudOff, Ellipsis, ExternalLink, ListTodo, Loader2, MessageCircle, Minus, PenLine, Phone, Plus, RefreshCw, Smartphone, Timer, Trash2, User, Wrench, X } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import AuthedImage from "../../components/AuthedImage";
import { Card, Empty, Page, PrimaryButton, Sheet, SkeletonRows, StatusBadge, humanKey } from "../components/ui";
import ItemsSheet from "../components/ItemsSheet";
import SignatureSheet from "../components/SignatureSheet";
import PaySheet from "../components/PaySheet";
import TaskSheet from "../components/TaskSheet";
import MessageSheet from "../components/MessageSheet";
import { addPhotosToJob, shrinkPhoto, uploadPhoto, type IntakePhoto } from "../lib/photos";
import { finishTarget, money } from "../lib/pay";
import { refusal } from "../lib/statusChange";
import { goQueue } from "../lib/offlineQueue";
import { changeStatus } from "../lib/statusChange";
import { haptic } from "../lib/scanner";
import { announceChange, useQueueStatus, useStatuses, useToast, useWide, type StatusMeta } from "../lib/useGo";
import { utc } from "../lib/time";

type Order = {
  id: string; order_number: string; status: string; is_estimate?: boolean; complaint?: string | null; diagnosis?: string | null;
  total?: number; scheduled_for?: string | null; due_at?: string | null; overdue_by_date?: boolean; status_entered_at?: string | null;
  client?: { id: string; name: string; phone?: string | null; watch_out?: string | null } | null;
  device?: { id: string; brand?: string | null; model?: string | null; serial?: string | null; imei?: string | null; vin?: string | null; watch_out?: string | null } | null;
  assigned_to?: { id: string; name: string } | null;
  lines?: { id: string; description?: string; qty?: number; total?: number; unit_price?: number; line_type?: string; kind?: string }[];
  status_logs?: { from?: string; to: string; at: string; note?: string | null }[];
  intake_photos?: IntakePhoto[];
  signed?: boolean;
  client_msg_unread?: number;
};
type Line = NonNullable<Order["lines"]>[number];

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
  const { me, can } = useMe();
  const fmt = (n?: number | null) => money(me?.currency, n);
  const [order, setOrder] = useState<Order | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [allowed, setAllowed] = useState<string[] | null>(null);
  const [sheet, setSheet] = useState(false);
  const [pending, setPending] = useState(false);   // a change is on screen but not confirmed yet
  const [open, setOpen] = useState<null | "items" | "sign" | "pay" | "task" | "more" | "line" | "photo" | "msg">(null);
  const [line, setLine] = useState<Line | null>(null);
  const [photo, setPhoto] = useState<IntakePhoto | null>(null);
  const [uploading, setUploading] = useState(0);
  const [balance, setBalance] = useState<number | null>(null);
  const [finishing, setFinishing] = useState(false);
  const finishAfterPay = useRef(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const mayClose = !!me && can("can_close_tickets");
  const mayPay = !!me && can("can_take_payments");
  const { list, byKey } = useStatuses(order?.is_estimate ? "estimate" : "work_order");

  const load = useCallback(async () => {
    setErr(null);
    try {
      const o = await api.get<Order>(`/orders/${encodeURIComponent(id)}`);
      setOrder(o);
      api.get<{ items: string[] }>(`/statuses/allowed/${encodeURIComponent(id)}`).then((r) => setAllowed(r.items || [])).catch(() => setAllowed(null));
      api.get<{ remaining: number }>(`/cash/orders/${encodeURIComponent(id)}/payment-context`).then((r) => setBalance(r.remaining)).catch(() => setBalance(null));
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

  async function onPhotos(files: FileList | null) {
    if (!order || !files?.length) return;
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Photos need a connection to upload.", tone: "warn" }); return; }
    const list = Array.from(files).slice(0, 10);
    setUploading(list.length);
    const ids: string[] = [];
    for (const f of list) {
      try { ids.push(await uploadPhoto(await shrinkPhoto(f))); } catch { /* skip a bad one */ }
      setUploading((n) => Math.max(0, n - 1));
    }
    if (fileRef.current) fileRef.current.value = "";
    if (!ids.length) { toast({ title: "Photos didn't upload", tone: "error" }); return; }
    try {
      const photos = await addPhotosToJob(order.id, ids);
      setOrder((o) => (o ? { ...o, intake_photos: photos } : o));
      haptic();
    } catch (e: any) { toast({ title: "Couldn't add the photos", body: e?.message, tone: "error" }); }
  }

  async function setLineQty(l: Line, qty: number) {
    if (!order) return;
    try {
      if (qty <= 0) await api.del(`/orders/${encodeURIComponent(order.id)}/lines/${encodeURIComponent(l.id)}`);
      else await api.patch(`/orders/${encodeURIComponent(order.id)}/lines/${encodeURIComponent(l.id)}`, { qty });
      setOpen(null);
      load();
    } catch (e: any) { toast({ title: "Couldn't change the item", body: e?.message, tone: "error" }); }
  }

  // Finish: pay first (when there's a balance and this person may take payment), then close.
  const target = useMemo(() => finishTarget(list, allowed), [list, allowed]);
  async function closeJob() {
    if (!order || !target) return;
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Finishing a job needs a connection.", tone: "warn" }); return; }
    setFinishing(true);
    try {
      await api.patch(`/orders/${encodeURIComponent(order.id)}/status`, { status: target.key });
      haptic();
      toast({ title: `${order.order_number} finished`, body: target.name });
      announceChange("jobs");
      load();
    } catch (e: any) {
      let data: any = null; try { data = JSON.parse(e?.body || "null"); } catch { /* not JSON */ }
      const r = refusal(e?.status ?? 0, data, target.name);
      toast({ title: r.title, body: r.message, tone: "error" });
    } finally { setFinishing(false); }
  }
  function finish() {
    if (mayPay && (balance ?? 0) > 0.009) { finishAfterPay.current = true; setOpen("pay"); return; }
    closeJob();
  }

  const back = !wide && (
    <button onClick={() => (history.length > 1 ? nav(-1) : nav("/today"))} aria-label="Back"
      className="-ml-2 h-11 min-w-[2.75rem] pr-2 inline-flex items-center text-primary font-medium">
      <ChevronLeft size={26} />Back
    </button>
  );
  const headerRight = (
    <div className="flex -mr-2">
      <button onClick={load} aria-label="Refresh" className="h-11 w-11 inline-flex items-center justify-center rounded-full text-ink-secondary active:bg-slate-100"><RefreshCw size={20} /></button>
      <button onClick={() => setOpen("more")} aria-label="More actions" className="h-11 w-11 inline-flex items-center justify-center rounded-full text-ink-secondary active:bg-slate-100"><Ellipsis size={22} /></button>
    </div>
  );

  if (err) return <Page title="Job" back={back}><Empty icon={<AlertTriangle size={22} />} title="Can't open this job" body={err} action={<Link to="/today" className="font-semibold text-primary inline-flex min-h-[2.75rem] items-center">Back to Today</Link>} /></Page>;
  if (!order) return <Page title="Job" back={back}><SkeletonRows n={2} /></Page>;

  const meta = byKey.get(order.status);
  const group = meta?.status_group || "new";
  const isClosed = group === "closed" || group === "dropped";
  const device = order.device ? [order.device.brand, order.device.model].filter(Boolean).join(" ") || "Device" : "";
  const ident = order.device ? (order.device.imei ? `IMEI ${order.device.imei}` : order.device.serial ? `Serial ${order.device.serial}` : order.device.vin ? `VIN ${order.device.vin}` : "") : "";
  const watch = [order.client?.watch_out, order.device?.watch_out].filter(Boolean).join(" · ");
  const lines = order.lines || [];
  const photos = order.intake_photos || [];
  const mayAddItems = !isClosed && (can("can_add_services") || can("can_add_products"));
  const owes = (balance ?? 0) > 0.009;

  // The main button: the next step for where this job is.
  let primary: { label: string; run: () => void; note?: string };
  if (isClosed) {
    primary = mayPay && owes ? { label: `Take payment · ${fmt(balance)}`, run: () => { finishAfterPay.current = false; setOpen("pay"); } }
      : { label: "Change status", run: () => setSheet(true) };
  } else if (group === "ready" && !order.is_estimate) {
    primary = !target || !mayClose ? { label: "Change status", run: () => setSheet(true), note: mayClose ? undefined : "Ready. Front desk finishes the job and takes payment." }
      : { label: mayPay && owes ? `Finish & take ${fmt(balance)}` : "Finish job", run: finish };
  } else if (choices.next) {
    primary = { label: `Mark ${choices.next.name}`, run: () => pickStatus(choices.next!) };
  } else {
    primary = { label: "Change status", run: () => setSheet(true) };
  }

  const action = "min-h-[4rem] rounded-2xl border border-slate-200 bg-white flex flex-col items-center justify-center gap-1 text-[0.75rem] font-semibold text-ink-primary active:bg-slate-50 disabled:opacity-40";

  return (
    <>
      <Page title={order.order_number} back={back} right={headerRight}
        subtitle={<button onClick={() => setSheet(true)} className="inline-flex items-center gap-2 mt-1.5 min-h-[2.75rem] -my-2"><StatusBadge meta={meta} statusKey={order.status} pending={pending} /><ChevronDown size={16} className="text-ink-tertiary" />{pending && <span className="inline-flex items-center gap-1 text-[0.8125rem] text-amber-700"><CloudOff size={14} />Not sent yet</span>}{order.is_estimate && <span className="text-[0.8125rem] text-ink-tertiary">Estimate</span>}</button>}>
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

        <div className="mt-3 grid grid-cols-5 gap-1.5">
          {order.client?.phone
            ? <a href={`tel:${order.client.phone.replace(/[^\d+]/g, "")}`} className={action}><Phone size={20} />Call</a>
            : <button disabled className={action}><Phone size={20} />Call</button>}
          <button onClick={() => setOpen("msg")} disabled={!order.client} className={`${action} relative`}>
            <MessageCircle size={20} />Message
            {!!order.client_msg_unread && <span className="absolute right-1.5 top-1.5 min-w-[1.125rem] h-[1.125rem] px-1 rounded-full bg-rose-500 text-white text-[0.625rem] font-bold leading-[1.125rem] text-center">{order.client_msg_unread}</span>}
          </button>
          <button onClick={() => fileRef.current?.click()} disabled={isClosed} className={action}>{uploading ? <Loader2 size={20} className="animate-spin" /> : <Camera size={20} />}Photo</button>
          <button onClick={() => setOpen("items")} disabled={!mayAddItems} className={action}><Plus size={20} />Item</button>
          <button onClick={startTimer} disabled={me?.clock_wo_enabled === false || isClosed} className={action}><Timer size={20} />Timer</button>
        </div>
        <input ref={fileRef} type="file" accept="image/*" capture="environment" multiple hidden onChange={(e) => onPhotos(e.target.files)} />

        {(photos.length > 0 || uploading > 0) && (
          <div className="mt-4">
            <h2 className="px-1 mb-2 text-[0.8125rem] font-semibold text-ink-tertiary">Photos <span className="tabular-nums">{photos.length}</span></h2>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {photos.map((p) => (
                <button key={p.id} onClick={() => { setPhoto(p); setOpen("photo"); }} className="h-20 w-20 shrink-0 overflow-hidden rounded-2xl" aria-label="Open photo">
                  <AuthedImage src={p.marked_url || p.url} alt="Job photo" className="h-20 w-20 object-cover" />
                </button>
              ))}
              {Array.from({ length: uploading }).map((_, i) => <div key={`u${i}`} className="sk h-20 w-20 shrink-0 rounded-2xl" />)}
            </div>
          </div>
        )}

        <div className="mt-4">
          <h2 className="px-1 mb-2 text-[0.8125rem] font-semibold text-ink-tertiary">Items</h2>
          <Card className="p-4 divide-y divide-slate-100">
            {order.assigned_to && <div className="flex justify-between py-2.5 first:pt-0"><span className="text-ink-secondary">Technician</span><span className="font-medium text-ink-primary">{order.assigned_to.name}</span></div>}
            {order.scheduled_for && <div className="flex justify-between py-2.5 first:pt-0"><span className="text-ink-secondary">Scheduled</span><span className="font-medium text-ink-primary">{when(order.scheduled_for)}</span></div>}
            {order.due_at && <div className="flex justify-between py-2.5 first:pt-0"><span className="text-ink-secondary">Due</span><span className={`font-medium ${order.overdue_by_date ? "text-rose-700" : "text-ink-primary"}`}>{when(order.due_at)}{order.overdue_by_date ? " · overdue" : ""}</span></div>}
            {lines.map((l) => (
              <button key={l.id} onClick={() => { if (!isClosed) { setLine(l); setOpen("line"); } }} className="w-full flex justify-between gap-3 py-2.5 first:pt-0 text-left">
                <span className="text-ink-primary min-w-0 truncate">{(l.qty ?? 1) !== 1 ? `${l.qty} × ` : ""}{l.description || humanKey(l.line_type || "item")}</span>
                <span className="tabular-nums text-ink-secondary">{fmt(l.total)}</span>
              </button>
            ))}
            {mayAddItems && (
              <button onClick={() => setOpen("items")} className="w-full min-h-[2.75rem] flex items-center gap-2 py-2.5 first:pt-0 font-semibold text-primary">
                <Plus size={18} />Add service or part
              </button>
            )}
            <div className="flex justify-between pt-2.5 first:pt-0"><span className="font-semibold text-ink-primary">Total</span><span className="font-semibold tabular-nums text-ink-primary">{fmt(order.total)}</span></div>
            {balance != null && (order.total ?? 0) > 0 && (
              <div className="flex justify-between pt-1.5 text-[0.875rem]"><span className="text-ink-secondary">{owes ? "Still to pay" : "Paid"}</span><span className={`tabular-nums font-medium ${owes ? "text-amber-700" : "text-emerald-700"}`}>{owes ? fmt(balance) : "In full"}</span></div>
            )}
          </Card>
        </div>

        <button onClick={() => setOpen("sign")} className="mt-3 w-full min-h-[3.25rem] rounded-2xl border border-slate-200 bg-white px-4 flex items-center gap-3 text-left active:bg-slate-50">
          <PenLine size={20} className={order.signed ? "text-emerald-600" : "text-ink-tertiary"} />
          <span className="flex-1 font-medium text-ink-primary">{order.signed ? "Customer signed" : "Customer signature"}</span>
          <span className={`text-[0.875rem] font-semibold ${order.signed ? "text-emerald-700" : "text-primary"}`}>{order.signed ? "Sign again" : "Sign now"}</span>
        </button>

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
          {primary.note && <p className="mb-2 rounded-xl bg-white/90 border border-slate-200 px-3 py-2 text-center text-[0.875rem] text-ink-secondary">{primary.note}</p>}
          <div className="flex gap-2">
            <PrimaryButton onClick={primary.run} disabled={finishing} className="flex-1">{finishing ? "Finishing…" : primary.label}</PrimaryButton>
            {primary.label !== "Change status" && (
              <button onClick={() => setSheet(true)} aria-label="Other statuses" className="min-h-[3.25rem] w-14 rounded-2xl border border-slate-200 bg-white inline-flex items-center justify-center text-ink-primary"><ChevronDown size={22} /></button>
            )}
          </div>
        </div>
      </Page>

      <ItemsSheet open={open === "items"} onClose={() => { setOpen(null); load(); }} orderId={order.id} deviceId={order.device?.id} onAdded={() => {}} />
      <SignatureSheet open={open === "sign"} onClose={() => setOpen(null)} orderId={order.id} customer={order.client?.name} onSaved={load} />
      <PaySheet open={open === "pay"} onClose={() => setOpen(null)} orderId={order.id} orderNumber={order.order_number} customer={order.client?.name}
        onPaid={(left) => { setBalance(left); if (left <= 0.009 && finishAfterPay.current && !isClosed) { finishAfterPay.current = false; closeJob(); } else load(); }} />
      <MessageSheet open={open === "msg"} onClose={() => setOpen(null)} orderId={order.id}
        onRead={() => setOrder((o) => (o ? { ...o, client_msg_unread: 0 } : o))} />
      <TaskSheet open={open === "task"} onClose={() => setOpen(null)} orderId={order.id} orderLabel={order.order_number} />

      <Sheet open={open === "line"} onClose={() => setOpen(null)} title={line?.description || "Item"}>
        {line && (
          <div className="px-2">
            <div className="flex items-center justify-center gap-6 py-4">
              <button onClick={() => setLineQty(line, (line.qty ?? 1) - 1)} aria-label="One less" className="h-14 w-14 rounded-full border border-slate-200 bg-white inline-flex items-center justify-center text-ink-primary"><Minus size={22} /></button>
              <span className="text-[2.25rem] font-bold tabular-nums text-ink-primary">{line.qty ?? 1}</span>
              <button onClick={() => setLineQty(line, (line.qty ?? 1) + 1)} aria-label="One more" className="h-14 w-14 rounded-full border border-slate-200 bg-white inline-flex items-center justify-center text-ink-primary"><Plus size={22} /></button>
            </div>
            <p className="text-center text-ink-secondary">{fmt(line.unit_price)} each</p>
            <button onClick={() => setLineQty(line, 0)} className="mt-4 w-full min-h-[3.25rem] rounded-2xl border border-rose-200 bg-rose-50 font-semibold text-rose-700 inline-flex items-center justify-center gap-2"><Trash2 size={18} />Remove from job</button>
          </div>
        )}
      </Sheet>

      <Sheet open={open === "more"} onClose={() => setOpen(null)} title={order.order_number}>
        <div className="space-y-1">
          <button onClick={() => setOpen("task")} className="w-full min-h-[3.25rem] flex items-center gap-3 rounded-xl px-3 text-left text-[1rem] text-ink-primary active:bg-slate-100"><ListTodo size={20} className="text-ink-tertiary" />Add a task for this job</button>
          {mayPay && owes && <button onClick={() => { finishAfterPay.current = false; setOpen("pay"); }} className="w-full min-h-[3.25rem] flex items-center gap-3 rounded-xl px-3 text-left text-[1rem] text-ink-primary active:bg-slate-100"><Plus size={20} className="text-ink-tertiary" />Take a payment ({fmt(balance)} owed)</button>}
          <button onClick={() => { setOpen(null); setSheet(true); }} className="w-full min-h-[3.25rem] flex items-center gap-3 rounded-xl px-3 text-left text-[1rem] text-ink-primary active:bg-slate-100"><Check size={20} className="text-ink-tertiary" />Change status</button>
          <a href={`/orders?open=${encodeURIComponent(order.id)}`} className="w-full min-h-[3.25rem] flex items-center gap-3 rounded-xl px-3 text-[1rem] text-ink-primary active:bg-slate-100"><ExternalLink size={20} className="text-ink-tertiary" />Open the full job in Tekly</a>
        </div>
      </Sheet>

      {open === "photo" && photo && (
        <div className="fixed inset-0 z-50 bg-black/95 flex items-center justify-center" role="dialog" aria-label="Photo" onClick={() => setOpen(null)}>
          <AuthedImage src={photo.marked_url || photo.url} alt="Job photo" className="max-h-full max-w-full object-contain" />
          <button onClick={() => setOpen(null)} aria-label="Close" className="absolute right-4 top-[calc(env(safe-area-inset-top)+0.75rem)] h-11 w-11 rounded-full bg-white/15 text-white inline-flex items-center justify-center"><X size={22} /></button>
        </div>
      )}

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
