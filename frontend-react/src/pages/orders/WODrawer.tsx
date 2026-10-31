import { askCancelReason, droppedStatus } from "../../lib/cancelReason";
import { PlanBanner, CheckoutOffer } from "../memberships/WOPlan";
import RecommendationsPanel from "./RecommendationsPanel";
import { DeferredWork, MarginNote, ReadyWhy, ServiceDue, TogetherChips } from "../../components/sense/Sense";
import PartsPOPanel from "./PartsPOPanel";
import { humanize } from "../../lib/humanize";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import Section, { sectionCard } from "../../components/Section";
import { fmtServerTime } from "../../lib/serverTime";
import { ChannelPicker, DeliveryNote, useThread, type SendChannel, type ThreadSent } from "./ConversationPanel";
import SignaturePad from "../../components/SignaturePad";
import SendMenu, { type SendWay } from "../../components/SendMenu";
import { deliveryText, deliveryTone, flashDelivery, type Delivery, type Flash, type Tone } from "../../lib/delivery";
import PhotoAnnotator from "./PhotoAnnotator";
import TagChips from "../../components/TagChips";
import { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import FloatingMenu from "../../components/FloatingMenu";
import PrintMenu from "./PrintMenu";
import { autoPrintIfClosed } from "../../lib/autoPrint";
import { Order, deviceName, money } from "./types";
import { Avatar, deviceIcon, dueText, friendlyDate } from "./look";
import { api, openAuthedDoc } from "../../api/client";
import { readAsDataUrl } from "../../lib/files";
import { confirmDialog, alertDialog, promptDialog } from "../../lib/dialog";
import AuthedImage from "../../components/AuthedImage";
import ClientDrawer from "./ClientDrawer";
import AssetDrawer from "./AssetDrawer";
import { useMe } from "../../context/Me";
import { useCurrencies } from "../../hooks/useCurrencies";
import CatalogPicker, { type CatalogItem } from "../../components/CatalogPicker";
import StatusControl, { type FullStatus } from "../../components/StatusControl";
import { escapeHtml, printHtmlInFrame } from "../../lib/safeHtml";
import JobCardFields from "../../components/JobCardFields";
import ReaderCharge from "../../components/ReaderCharge";
import AccountPicker, { type PayAccount, type PaymentOption, accountsFor, pickDefaultAccount } from "../../components/AccountPicker";
import CardPaymentsList from "../../components/CardPaymentsList";
import { binChoices, byLocation, needsBinChoice } from "../../lib/stockBins";
import LineQtyEditor from "./LineQtyEditor";
import AutoPrintPicker from "../../components/AutoPrintPicker";
import KitGroupHeader from "./KitGroupHeader";
import EstimateTerms, { startRevision } from "./EstimateTerms";
import JobTasks from "./JobTasks";
import { sendInvoice } from "../invoices/billing";
import { groupKitLines } from "../../lib/kitGroups";
import { loadFailed, saveFailed } from "../../lib/notice";
import { clickable } from "../../lib/clickable";
import { jobTypeLabel, type WOTypeRef } from "./jobType";

type Emp = { id: string; name: string; in_managers?: boolean; in_assignees?: boolean };
type Line = { id: string; kind?: string; line_type?: string; description?: string; qty?: number; unit_price?: number; discount?: number; discount_type?: string; technician_id?: string | null; work_share?: number; booked_hours?: number; co_technicians?: { employee_id: string; share: number }[]; total?: number; in_stock?: boolean; order_item_id?: string | null;
  bundle_group_id?: string | null; bundle_name?: string | null; bundle_print_mode?: string | null; bundle_price_locked?: number | null; bundle_qty?: number | null };
type Detail = Order & {
  complaint?: string; tech_notes?: string; diagnosis?: string; conclusion?: string;
  warranty_until?: string | null; due_at?: string | null;
  manager?: { id?: string; name?: string } | null;
  assigned_to?: { id?: string; name?: string } | null;
  client?: { id?: string; name?: string; phone?: string; email?: string; balance?: number; watch_out?: string | null } | null;
  device?: { id?: string; brand?: string; model?: string; serial?: string; imei?: string; asset_type?: string; color?: string; watch_out?: string | null; has_passcode?: boolean } | null;
  signature?: string | null; is_estimate?: boolean;
  intake_photos?: { id: string; url: string; marked_id?: string; marked_url?: string }[];
  lines?: Line[];
};
type Timing = {
  status: string; elapsed_min: number; typical_min: number | null; typical_p80_min: number | null;
  sample_n: number; stalled: boolean; over_by_min: number | null;
  predicted_ready_at: string | null; confidence: "none" | "low" | "high"; is_terminal: boolean;
};
type Tab = "general" | "services" | "checklists" | "invoices" | "activity";

// House button and input looks, shared across the drawer.
const btnPrimary = "h-9 px-3.5 rounded-xl text-xs font-semibold bg-primary text-white hover:brightness-110 transition-all disabled:bg-slate-200 disabled:text-ink-tertiary disabled:hover:brightness-100 inline-flex items-center gap-1.5";
const btnSecondary = "h-9 px-3.5 rounded-xl text-xs font-semibold bg-white text-ink-primary border border-slate-200 hover:bg-slate-50 transition-colors disabled:opacity-50 inline-flex items-center gap-1.5";
const btnGhost = "h-9 px-3.5 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100 transition-colors";
const houseInput = "glass-input rounded-xl px-3 py-2 text-sm";
const menuRow = "w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-left hover:bg-slate-50";
const or = (real: any, sample: string) => (real != null && real !== "" ? String(real) : sample);
const fmtDT = (iso?: string | null) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
const fmtDur = (min?: number | null) => {
  if (min == null) return "";
  if (min < 60) return `${Math.round(min)} min`;
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return m ? `${h}h ${m}m` : `${h}h`;
};

export default function WODrawer({ order, onClose, onChanged }: { order: Order; onClose: () => void; onChanged?: () => void }) {
  const { can, me } = useMe();
  const nav = useNavigate();
  const [tab, setTab] = useState<Tab>("general");
  const [sub, setSub] = useState<null | "client" | "asset">(null);
  const [d, setD] = useState<Detail>(order as Detail);
  const [statuses, setStatuses] = useState<FullStatus[]>([]);
  // Edits that only save on an explicit button (notes, watch-outs) register here while
  // unsaved, so "Save and close" can save them and a plain close can warn first.
  const pending = useRef(new Map<string, () => Promise<boolean>>());
  const setPending = (key: string, save: (() => Promise<boolean>) | null) => { if (save) pending.current.set(key, save); else pending.current.delete(key); };
  const [saving, setSaving] = useState(false);
  const [emps, setEmps] = useState<Emp[]>([]);
  const [timing, setTiming] = useState<Timing | null>(null);
  const [toast, setToast] = useState<{ m: string; tone?: Tone } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [labor, setLabor] = useState<any>(null);   // 144v: who's timing this WO, per person + line
  const loadLabor = () => api.get<any>(`/orders/${order.id}/labor`).then(setLabor).catch(() => setLabor(null));
  useEffect(() => { loadLabor(); /* eslint-disable-next-line */ }, [order.id]);
  // 144y/144as — procure the parts this WO needs: raise a PO for the shortfall, and/or
  // reserve on-hand stock for the job. Gated to staff with inventory access.
  const canBuy = can("can_manage_inventory");
  const [partsOpen, setPartsOpen] = useState(false);   // ADR-144po: the ⋯ → Parts & purchase orders panel
  const [menuOpen, setMenuOpen] = useState(false);
  // The ⋯ menu closes on any click outside it (the tab bar sits above the click-catcher) and on Escape.
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    // Clicks inside a sub-menu opened from it (Auto-print's picker renders in a portal) don't count as outside.
    const onDown = (e: MouseEvent) => {
      const t = e.target as Element;
      if (!menuRef.current?.contains(t) && !t.closest?.('[role="menu"]')) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [menuOpen]);
  const [watchEditing, setWatchEditing] = useState(false);   // the watch-out editor in the alert area
  // A warning (e.g. "saved, but not sent: set up text messages") stays up longer than a plain "Saved".
  const flash: Flash = (m, tone) => {
    setToast({ m, tone });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), tone ? 6000 : 2200);
  };

  const reload = () => api.get<Detail>(`/orders/${order.id}`).then(setD).catch(loadFailed("the job"));
  useEffect(() => { reload(); }, [order.id]);
  // 144bl Comeback Shield: flag a device that's back soon after a prior visit (gated by role).
  const [comeback, setComeback] = useState<{ is_comeback?: boolean; count?: number; days_since?: number | null; prior_order_number?: string } | null>(null);
  useEffect(() => { if (can("can_view_comeback_alerts")) api.get<any>(`/orders/${order.id}/comeback`).then(setComeback).catch(() => setComeback(null)); /* eslint-disable-next-line */ }, [order.id]);
  const [incomingPo, setIncomingPo] = useState<{ po_number?: string; status?: string; expected_at?: string | null; parts: { part: string; outstanding: number }[] }[]>([]);
  const loadIncomingPo = () => api.get<any>(`/orders/${order.id}/incoming-po`).then((r) => setIncomingPo(r.items || [])).catch(loadFailed("incoming parts orders"));
  useEffect(() => { loadIncomingPo(); /* eslint-disable-next-line */ }, [order.id]);
  const isEst = !!d.is_estimate;
  useEffect(() => {
    const rt = isEst ? "estimate" : "work_order";
    api.get<any>(`/statuses?record_type=${rt}`).then((r) => setStatuses(r.items || r || [])).catch(loadFailed("statuses"));
  }, [order.id, isEst]);
  useEffect(() => { api.get<Timing>(`/orders/${order.id}/timing`).then(setTiming).catch(() => setTiming(null)); }, [order.id, d.status]);
  useEffect(() => { api.get<any>("/staff/employees").then((r) => setEmps(r.items || r || [])).catch(loadFailed("the staff list")); }, []);
  const [woTypes, setWoTypes] = useState<WOTypeRef[] | null>(null);
  useEffect(() => { api.get<any>("/forms/types").then((r) => setWoTypes(r.items || [])).catch(() => setWoTypes([])); }, []);

  // B11: a status that needs something first (a comment, or payment) asks for it right here,
  // under the header, instead of failing with a message the drawer gave no way to fix.
  const [gate, setGate] = useState<null | { key: string; name: string; reason?: string; kind: "comment" | "unpaid"; message?: string; canOverride?: boolean }>(null);
  const [gateText, setGateText] = useState("");
  async function changeStatus(key: string, extra: { reason?: string; comment?: string; allow_unpaid?: boolean } = {}) {
    let reason: string | null = extra.reason ?? null;
    if (reason === null) { const ds = droppedStatus(statuses as any[], key); if (ds) { reason = await askCancelReason(ds.name); if (reason === null) return; } }
    const target = statuses.find((s) => s.key === key);
    const name = target?.name || key;
    if (target?.require_comment && !extra.comment && !((d as any).comments || []).length) {
      setGateText(""); setGate({ key, name, reason: reason || undefined, kind: "comment" }); return;
    }
    const prev = d.status; setD({ ...d, status: key });
    try {
      const r = await api.patch<any>(`/orders/${order.id}/status`, { status: key, reason: reason || undefined, comment: extra.comment, allow_unpaid: extra.allow_unpaid || undefined });
      setGate(null); flash("Status updated"); void autoPrintIfClosed(order.id, r); onChanged?.(); reload(); loadLabor();
    } catch (e: any) {
      setD({ ...d, status: prev });
      let detail: any = null; try { detail = JSON.parse(e?.body || "null")?.detail; } catch { /* not JSON */ }
      if (e?.status === 409 && detail?.code === "comment_required") { setGateText(""); setGate({ key, name, reason: reason || undefined, kind: "comment" }); return; }
      if (e?.status === 409 && detail?.code === "unpaid") { setGate({ key, name, reason: reason || undefined, kind: "unpaid", message: detail.message, canOverride: !!detail.can_override }); return; }
      flash(e.message || "That didn't save. Try again.");
    }
  }
  async function continueLinked() {
    const why = await promptDialog(`Start a new job that carries on from ${d.order_number}? It keeps the customer, device and job details, and the two jobs are linked.`,
      { title: "Continue as a linked job", placeholder: "What needs doing this time (optional)", defaultValue: "" });
    if (why === null) return;
    try {
      const r = await api.post<{ id: string; order_number: string }>(`/orders/${order.id}/continue`, { complaint: why.trim() || undefined });
      flash(`Job ${r.order_number} created`); onChanged?.(); nav(`/orders?open=${r.id}`);
    } catch (e: any) { flash(e.message || "That didn't save. Try again."); }
  }
  async function saveNotes(tech_notes: string, conclusion: string) {
    try { await api.patch(`/orders/${order.id}/notes`, { tech_notes, conclusion }); setD((p) => ({ ...p, tech_notes, conclusion })); flash("Notes saved"); return true; }
    catch (e: any) { flash(e.message || "That didn't save. Try again."); return false; }
  }
  // Copies the customer's tracking link; staff open it from the "Open" link beside it (a real
  // new-tab link), so the staff tab never turns into the customer page.
  async function copyTrackingLink() {
    try {
      // idempotent: returns the existing public token or mints one
      const r = await api.post<{ token: string; path: string }>(`/orders/${order.id}/public-link`, {});
      setD((p) => ({ ...p, public_token: r.token } as any));
      const url = `${window.location.origin}/track/${r.token}`;
      try { await navigator.clipboard.writeText(url); flash("Tracking link copied"); }
      catch { flash("Couldn't copy. Use Open to see the link."); }
    } catch (e: any) { flash(e.message || "That didn't save. Try again."); }
  }
  // I6 — send the estimate (tracking link, plus the PDF by email). The server says whether it
  // really went out; we show that sentence, never a blanket "sent".
  const [estBusy, setEstBusy] = useState<"" | "send" | "convert" | "revise">("");
  async function sendEstimate(way?: SendWay) {
    setEstBusy("send");
    try {
      const r = await api.post<{ ok: boolean; delivery?: Delivery }>(`/orders/${order.id}/send-estimate`, { channel: way || "auto" });
      flashDelivery(flash, r?.delivery, "Estimate saved on the job");
      setThreadKey((k) => k + 1);
    } catch (e: any) { flash(e.message || "Couldn't send the estimate", "warn"); }
    finally { setEstBusy(""); }
  }
  // E1/E4 — an approved estimate becomes the work order (parts come out of stock).
  async function convertEstimate() {
    if (!(await confirmDialog("Turn this estimate into a work order? Parts will be taken from stock.", { title: "Convert to work order", confirmLabel: "Convert" }))) return;
    setEstBusy("convert");
    try {
      const r = await api.post<{ ok: boolean; order_id?: string }>(`/orders/${order.id}/convert`, {});
      flash("Now a work order");
      onChanged?.();
      if (r?.order_id && r.order_id !== order.id) { onClose(); nav(`/orders?open=${r.order_id}`); }
      else await reload();
    } catch (e: any) { flash(e.message || "Couldn't convert the estimate", "warn"); }
    finally { setEstBusy(""); }
  }
  // E5 — a new version of the estimate: the old one goes to the history, the customer can answer again.
  async function newRevision() {
    setEstBusy("revise");
    try {
      const n = await startRevision(order.id);
      if (n != null) { flash(`Revision ${n} started. Change the estimate, then send it.`); await reload(); onChanged?.(); }
    } catch (e: any) { flash(e.message || "Couldn't start a new revision", "warn"); }
    finally { setEstBusy(""); }
  }
  // J6 — the job's type and priority are real pickers now (the type list is woTypes, loaded above).
  async function changeType(typeId: string) {
    if (!typeId || typeId === (d as any).type_id) return;
    try {
      const r = await api.patch<{ status: string; remapped_to?: string | null }>(`/orders/${order.id}/type`, { type_id: typeId });
      flash(r.remapped_to ? "Job type changed, and the status moved to its starting status" : "Job type changed");
      await reload(); onChanged?.();
    } catch (e: any) { flash(e.message || "Couldn't change the job type", "warn"); }
  }
  async function changePriority(p: number) {
    const prev = d.priority;
    setD({ ...d, priority: p });
    try { await api.patch(`/orders/${order.id}/priority`, { priority: p }); flash("Priority updated"); onChanged?.(); }
    catch (e: any) { setD((x) => ({ ...x, priority: prev })); flash(e.message || "Couldn't change the priority", "warn"); }
  }
  // J1 — customer signs on the shop's screen (e.g. at pick-up).
  const [sigOpen, setSigOpen] = useState(false);
  const [sigBusy, setSigBusy] = useState(false);
  async function saveSignature(signature: string, note: string) {
    setSigBusy(true);
    try {
      await api.post(`/orders/${order.id}/signature`, { signature, note: note || undefined });
      setSigOpen(false); flash("Signature saved"); await reload(); onChanged?.();
    } catch (e: any) { flash(e.message || "Couldn't save the signature", "warn"); }
    finally { setSigBusy(false); }
  }
  // G3 — both customer message boxes read one thread; bump this so the other box reloads.
  const [threadKey, setThreadKey] = useState(0);
  // Save and close never touches the status (that's the status menu in the header):
  // it commits fields that save when you leave them, saves pending notes/warnings, then closes.
  async function saveAndClose() {
    (document.activeElement as HTMLElement | null)?.blur?.();
    setSaving(true);
    try {
      for (const save of [...pending.current.values()]) if (!(await save())) return;   // stay open on a failed save
      onChanged?.(); onClose();
    } finally { setSaving(false); }
  }
  async function tryClose() {
    if (pending.current.size && !(await confirmDialog("You have changes that aren't saved yet. Close without saving them?", { title: "Unsaved changes", confirmLabel: "Close without saving" }))) return;
    onClose();
  }
  async function del() {
    // Archiving refunds any payments taken on the job, so say so before asking for the reason.
    const reason = await promptDialog(`Any payments on ${woNum} are refunded. You can find it later under Archived.`,
      { title: `Archive ${woNum}?`, confirmLabel: "Archive and refund", placeholder: "Customer cancelled" });
    if (!reason) return;
    try { await api.post(`/orders/${order.id}/archive`, { reason, refund: true }); onChanged?.(); onClose(); }
    catch (e: any) { flash(`Couldn't archive ${woNum}. ${e.message || "Try again."}`); }
  }
  async function reassign(field: "assigned_to_id" | "manager_id", id: string) {
    try {
      await api.patch(`/orders/${order.id}/assign`, { [field]: id });
      const who = emps.find((e) => e.id === id);
      setD((prev) => field === "manager_id" ? { ...prev, manager: who ? { id, name: who.name } : null } : { ...prev, assigned_to: who ? { id, name: who.name } : null } as any);
      flash("Reassigned"); onChanged?.();
    } catch (e: any) { flash(e.message || "That didn't save. Try again."); }
  }
  async function reschedule(iso: string) {
    try { await api.patch(`/orders/${order.id}/schedule`, { scheduled_for: iso || null }); setD((p) => ({ ...p, scheduled_for: iso })); flash("Rescheduled"); onChanged?.(); }
    catch (e: any) { flash(e.message || "That didn't save. Try again."); }
  }
  // Start the job/labour timer on THIS work order (switch stops any running one first),
  // so the top-bar clock shows which job you're on and time is attributed here.
  async function startTimer(lineId?: string) {
    try {
      await api.post(`/timeclock/start?switch=true`, { order_id: order.id, order_line_id: lineId || null, category: "job" });
      flash(lineId ? "Timer started on this line" : `Timer started on ${woNum}`);
      loadLabor();
    } catch (e: any) { flash(e.message || "Could not start timer"); }
  }
  const secHM = (s: number) => { const h = Math.floor((s || 0) / 3600), m = Math.floor(((s || 0) % 3600) / 60); return h ? `${h}h ${m}m` : `${m}m`; };
  async function stopEntry(empId: string) {
    const ent = (labor?.entries || []).find((e: any) => e.employee_id === empId && e.running);
    if (!ent) return;
    try { await api.post(`/timeclock/entries/${ent.id}/stop`, {}); flash("Timer stopped"); loadLabor(); }
    catch (e: any) { flash(e.message || "Couldn't stop timer"); }
  }
  // 144au — job timer rollup for the header pill + Services-tab per-line clock-in.
  const byEmp = labor?.by_employee || [];
  const timerRunning = byEmp.some((b: any) => b.running);
  const timerTotalSec = byEmp.reduce((n: number, b: any) => n + (b.total_seconds || 0), 0);
  async function stopAllTimers() {
    for (const b of byEmp.filter((x: any) => x.running)) await stopEntry(b.employee_id);
  }
  async function printWO() {
    const css = `body{font-family:Inter,system-ui,sans-serif;margin:0;color:#14213f}.s{padding:22px;page-break-after:always}.s:last-child{page-break-after:auto}h1{font-size:16px;margin:0 0 4px}.k{font-size:10px;font-weight:800;text-transform:uppercase;color:#9aa7c4;letter-spacing:.05em;margin-top:12px}.v{font-size:14px;font-weight:600}.n{font-size:22px;font-weight:800;font-family:monospace}hr{border:none;border-top:1px dashed #c9d3ea;margin:12px 0}`;
    const head = `<h1>${escapeHtml(me?.org_name || "Tekly")}</h1>`;   // the shop's own name on its stickers
    const client = `<div class="k">Client</div><div class="v">${escapeHtml(c.name || "Walk-in")} ${c.phone ? "· " + escapeHtml(c.phone) : ""}</div>`;
    // Several items per job: one bench sticker per item, so each tool on the shelf carries its own.
    let items: JobItem[] = [];
    try { const r = await api.get<any>(`/orders/${order.id}/items`); if (r.enabled) items = r.items || []; } catch { /* print the job sticker */ }
    const stickers = items.length > 1
      ? items.map((it, i) => `<div class="s">${head}<div class="n">${escapeHtml(woNum)}-${i + 1}</div><div class="v">Item ${i + 1} of ${items.length}</div><hr/>
          ${client}
          <div class="k">Item</div><div class="v">${escapeHtml(itemName(it))}${it.asset?.serial ? " · SN " + escapeHtml(it.asset.serial) : ""}</div>
          <div class="k">Problem</div><div class="v">${escapeHtml(it.complaint || (it.is_primary ? d.complaint : "") || "—")}</div>
          ${it.collected_at ? `<div class="k">Collected</div><div class="v">Yes</div>` : ""}</div>`).join("")
      : `<div class="s">${head}<div class="v" style="font-family:monospace">${escapeHtml(woNum)}</div><hr/>
      ${client}
      <div class="k">${escapeHtml(dev.asset_label || "Device")}</div><div class="v">${escapeHtml(hasDevice ? devTitle : "No device")}${dev.serial ? " · SN " + escapeHtml(dev.serial) : ""}</div>
      <div class="k">Status</div><div class="v">${escapeHtml((d.status || "").replace(/_/g, " "))}</div>
      <div class="k">Complaint</div><div class="v">${escapeHtml(d.complaint || "—")}</div>
      <div class="k">Total</div><div class="v">${escapeHtml(money(d.total))}</div></div>`;
    // Hidden sandboxed frame instead of a pop-up window, so pop-up blockers can't stop it.
    printHtmlInFrame(`<html><head><title>${escapeHtml(woNum)}</title><style>${css}</style></head><body>${stickers}</body></html>`);
  }

  // A kit can print as one line for the customer; a customer can always ask for every item.
  const hasKit = (d.lines || []).some((l) => l.bundle_group_id);
  async function printItemised() {
    try {
      const t = await api.get<any>("/documents/templates");
      const want = isEst ? "estimate" : "work_order";
      const tpls = (t?.items || []).filter((x: any) => x.doc_type === want);
      const tpl = tpls.find((x: any) => x.is_default) || tpls[0];
      if (!tpl) { flash("Add a work order template first (Settings → Documents)"); return; }
      const r = await api.get<any>(`/documents/render/${order.id}/${tpl.id}?itemised=true`);
      if (r?.html) printHtmlInFrame(r.html);
    } catch (e: any) { flash(e.message || "Could not print the itemised copy"); }
  }

  const woNum = d.order_number || order.order_number || order.id;
  const urgent = (d.priority ?? 0) >= 1;
  const c = d.client || {}; const dev = d.device || {};
  const hasDevice = !!dev.id;
  const devTitle = deviceName(d.device) !== "—" ? deviceName(d.device) : (dev.asset_label || "Device");
  const isClosed = !!statuses.find((st) => st.key === d.status && ["closed", "dropped"].includes(st.status_group || ""));
  const due = dueText(d.due_at, !isClosed);
  const showTiming = !!timing && !timing.is_terminal && (timing.stalled || timing.typical_min != null || !!timing.predicted_ready_at);
  const hasWatch = watchEditing || !!(d.device?.watch_out || d.client?.watch_out);

  // 144dd — tab badges reflect real state (no always-on dots). Amber Checklists dot only when a
  // required checklist item is still undone; red Activity count only when a customer message is unread.
  const unread = Number((d as any).client_msg_unread || 0);
  const ALL_TABS: { id: Tab; label: string; badge?: string; dot?: string }[] = [
    { id: "general", label: "General info" },
    { id: "services", label: `Services & products${d.lines?.length ? ` (${d.lines.length})` : ""}` },
    { id: "checklists", label: "Checklists", dot: (d as any).checklists_pending ? "bg-amber-500" : undefined },
    { id: "invoices", label: "Invoices & payments", badge: (d as any).payment_due ? "Due" : undefined },
    { id: "activity", label: "Activity", badge: (unread > 0 && tab !== "activity") ? String(unread) : undefined },
  ];
  const TABS = ALL_TABS.filter((t) => t.id !== "invoices" || can("can_view_invoices_tab")); // legacy data-perm parity

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 40 }}>
      <div className="absolute inset-0 bg-ink-primary/30" aria-hidden="true" onClick={tryClose} />
      <div className="relative h-full w-full max-w-[840px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>

        {/* Header. A thin edge in the job's own status colour (from Settings) says where it stands at a glance;
            the status name stays in the header too, so colour is never the only signal. */}
        <div aria-hidden="true" className="h-[3px] flex-shrink-0" style={{ background: statuses.find((st) => st.key === d.status)?.color || "#64748b" }} />
        <header className="relative z-20 px-4 sm:px-6 py-3 sm:py-4 border-b border-slate-200/90 bg-white flex flex-wrap items-center justify-between gap-2 flex-shrink-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 min-w-0">
            <span className="tabular-nums text-[11px] font-semibold px-2 py-1 rounded-lg bg-slate-100 text-ink-secondary whitespace-nowrap">{woNum}</span>
            <h3 className="text-base font-semibold text-ink-primary tracking-tight">{hasDevice ? devTitle : (c.name || "Work order")}</h3>
            <StatusControl statuses={statuses} currentKey={d.status} elapsedMin={timing?.elapsed_min}
              isEstimate={isEst} onChange={changeStatus} />
            {urgent && (
              <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200 flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />{(d.priority ?? 0) >= 2 ? "Urgent" : "High priority"}
              </span>
            )}
            {!!(d as any).estimate_approved_at && (
              <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200 flex items-center gap-1">
                <M n="check" c="text-xs" />Customer approved
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 ml-auto">
            {/* 144au — job timer as a header pill: always reachable on every tab (Jobber pattern) */}
            {!isEst && (timerRunning ? (
              <button onClick={stopAllTimers} title="Stop the job timer"
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100 transition-colors">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                <span className="tabular-nums">{secHM(timerTotalSec)}</span><M n="stop" c="text-sm" />
              </button>
            ) : (
              <button onClick={() => startTimer()} title="Start the job timer"
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-semibold text-ink-secondary border border-slate-200/80 hover:bg-slate-100 transition-colors">
                <M n="play_arrow" c="text-sm" />Timer{timerTotalSec > 0 ? ` · ${secHM(timerTotalSec)}` : ""}
              </button>
            ))}
            {incomingPo.length > 0 && (
              <button type="button" onClick={() => setPartsOpen(true)} title={incomingPo.map((p) => `${p.po_number}: ${p.parts.map((x) => `${x.part} ×${x.outstanding}`).join(", ")}`).join("\n")}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-semibold bg-amber-50 text-amber-800 border border-amber-200 hover:bg-amber-100 transition-colors">
                <M n="local_shipping" c="text-sm" />Waiting on parts
              </button>
            )}
            <div ref={menuRef} className="relative">
              <button aria-label="More actions" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((v) => !v)}
                className="p-1.5 rounded-lg text-ink-secondary hover:text-ink-primary hover:bg-slate-100 border border-slate-200/80 transition-colors" title="More actions"><M n="more_horiz" c="text-base" /></button>
              {menuOpen && (<>
                <div className="fixed inset-0 z-30" aria-hidden="true" onClick={() => setMenuOpen(false)} />
                <div role="menu" className="absolute right-0 top-full mt-1 z-40 w-72 max-h-[70vh] overflow-y-auto bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5">
                  <button role="menuitem" type="button" onClick={() => { setMenuOpen(false); setPartsOpen(true); }} className={menuRow}>
                    <M n="local_shipping" c="text-base text-ink-tertiary" />
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm text-ink-primary">Parts &amp; purchase orders</span>
                      <span className="block text-xs text-ink-tertiary">{incomingPo.length ? `${incomingPo.length} on order` : "Order or hold parts for this job"}</span>
                    </span>
                  </button>
                  {!(order as any).is_estimate && can("can_create_tickets") && (
                    <button role="menuitem" type="button" onClick={() => { setMenuOpen(false); continueLinked(); }} className={menuRow}>
                      <M n="redo" c="text-base text-ink-tertiary" />
                      <span className="flex-1 min-w-0">
                        <span className="block text-sm text-ink-primary">Continue as a linked job</span>
                        <span className="block text-xs text-ink-tertiary">A new job for the same customer and device</span>
                      </span>
                    </button>
                  )}
                  {(d.device?.id || d.client?.id) && (
                    <button role="menuitem" type="button" onClick={() => { setMenuOpen(false); setWatchEditing(true); }} className={menuRow}>
                      <M n="warning" c="text-base text-ink-tertiary" />
                      <span className="text-sm text-ink-primary">{d.device?.watch_out || d.client?.watch_out ? "Edit watch-outs" : "Add watch-out"}</span>
                    </button>
                  )}
                  <div className="my-1 border-t border-slate-100" />
                  <button role="menuitem" type="button" onClick={() => { setMenuOpen(false); navigator.clipboard?.writeText(`${window.location.origin}/orders?open=${order.id}`).then(() => flash("Staff link copied")).catch(saveFailed("copy the link")); }} className={menuRow}>
                    <M n="link" c="text-base text-ink-tertiary" />
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm text-ink-primary">Copy staff link</span>
                      <span className="block text-xs text-ink-tertiary">Opens this job for your team</span>
                    </span>
                  </button>
                  <button role="menuitem" type="button" onClick={() => { setMenuOpen(false); copyTrackingLink(); }} className={menuRow}>
                    <M n="link" c="text-base text-ink-tertiary" />
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm text-ink-primary">Copy customer tracking link</span>
                      <span className="block text-xs text-ink-tertiary">The status page the customer sees</span>
                    </span>
                  </button>
                  {(d as any).public_token && (
                    <a role="menuitem" href={`/track/${(d as any).public_token}`} target="_blank" rel="noopener noreferrer" onClick={() => setMenuOpen(false)} className={menuRow}>
                      <M n="open_in_new" c="text-base text-ink-tertiary" /><span className="text-sm text-ink-primary">Open tracking page</span>
                    </a>
                  )}
                  <div className="my-1 border-t border-slate-100" />
                  {(can("can_delete_orders") || can("can_delete_tickets")) && <button role="menuitem" type="button" onClick={() => { setMenuOpen(false); del(); }} className={`${menuRow} text-rose-600 hover:!bg-rose-50`}>
                    <M n="delete" c="text-base" /><span className="text-sm font-medium">Archive</span>
                  </button>}
                </div>
              </>)}
            </div>
            <button aria-label="Close" onClick={tryClose} className="w-8 h-8 rounded-full flex items-center justify-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100 transition-colors" title="Close"><M n="close" c="text-lg" /></button>
          </div>
          <div className="w-full">
            <TagChips entityType="order" entityId={order.id} />
          </div>
        </header>
        {partsOpen && <PartsPOPanel orderId={order.id} lines={(d.lines || []) as any[]} canBuy={canBuy} isEstimate={isEst}
          onClose={() => setPartsOpen(false)} onChanged={() => { loadIncomingPo(); reload(); }} />}

        {/* Alerts: one area above the tabs, holding only the alerts this job has right now. */}
        {(gate || showTiming || comeback?.is_comeback || hasWatch) && (
          <div className="px-4 sm:px-6 py-3 border-b border-slate-200/80 bg-white flex-shrink-0 space-y-2">
            {gate && (
              <div role="region" aria-label={gate.kind === "comment" ? "Comment needed" : "Still to pay"}
                className={`rounded-xl border px-3.5 py-3 ${gate.kind === "unpaid" ? "bg-amber-50 border-amber-200" : "bg-slate-50 border-slate-200"}`}>
                {gate.kind === "comment" ? (
                  <form onSubmit={(e) => { e.preventDefault(); if (gateText.trim()) changeStatus(gate.key, { reason: gate.reason, comment: gateText.trim() }); }} className="space-y-2">
                    <p className="text-xs font-semibold text-ink-primary">Moving to {gate.name} needs a comment on the job.</p>
                    <textarea autoFocus value={gateText} onChange={(e) => setGateText(e.target.value)} rows={2} aria-label="Comment"
                      placeholder="What was done, or what the customer agreed to" className="glass-input w-full rounded-xl px-3 py-2 text-sm resize-none" />
                    <div className="flex items-center gap-2">
                      <button type="submit" disabled={!gateText.trim()} className={btnPrimary}>Add comment and move to {gate.name}</button>
                      <button type="button" onClick={() => setGate(null)} className={btnGhost}>Cancel</button>
                    </div>
                  </form>
                ) : (
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-amber-900">{gate.message}</p>
                    <div className="flex flex-wrap items-center gap-2">
                      {can("can_view_invoices_tab") && <button type="button" onClick={() => { setGate(null); setTab("invoices"); }} className={btnPrimary}>Take payment</button>}
                      {gate.canOverride && <button type="button" onClick={() => changeStatus(gate.key, { reason: gate.reason, allow_unpaid: true })}
                        className="h-9 px-3.5 rounded-xl text-xs font-semibold border border-amber-300 bg-white text-amber-900 hover:bg-amber-100">Move to {gate.name} unpaid</button>}
                      <button type="button" onClick={() => setGate(null)} className={btnGhost}>Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Cycle time — predicted ready + stall detection (Feature 2) */}
            {showTiming && timing && (
              <div className={`rounded-xl border px-3.5 py-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs ${timing.stalled ? "bg-rose-50 border-rose-200" : "bg-slate-50 border-slate-200/80"}`}>
                {timing.stalled ? (
                  <span className="flex items-center gap-1.5 font-semibold text-rose-800">
                    <M n="timer" c="text-sm" />Running long
                    <span className="font-medium text-rose-700">· {fmtDur(timing.elapsed_min)} in this status{timing.typical_min ? ` vs about ${fmtDur(timing.typical_min)} usually` : ""}</span>
                  </span>
                ) : timing.typical_min ? (
                  <span className="flex items-center gap-1.5 text-ink-secondary">
                    <M n="insights" c="text-sm text-ink-tertiary" />
                    <span className="font-semibold text-ink-primary">{fmtDur(timing.elapsed_min)}</span> in this status
                    <span className="text-ink-tertiary">· usually about {fmtDur(timing.typical_min)}</span>
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 text-ink-tertiary"><M n="insights" c="text-sm" />{fmtDur(timing.elapsed_min)} in this status</span>
                )}
                {timing.predicted_ready_at && (
                  <span className="ml-auto flex items-center gap-1.5 text-ink-secondary">
                    <M n="event_available" c="text-sm text-emerald-600" />
                    Est. ready <span className="font-semibold text-ink-primary">{fmtDT(timing.predicted_ready_at)}</span>
                    {timing.confidence === "low" && <span className="text-ink-tertiary" title={`Based on ${timing.sample_n} past job(s)`}>· rough</span>}
                    <ReadyWhy sampleN={timing.sample_n} typicalMin={timing.typical_min} fmt={fmtDur} />
                  </span>
                )}
              </div>
            )}

            {comeback?.is_comeback && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2 flex items-start gap-2 text-xs text-amber-900">
                <M n="replay" c="text-sm mt-px text-amber-700" />
                <span><b className="font-semibold">Possible comeback.</b> This device was here {comeback.days_since != null ? `${comeback.days_since} day${comeback.days_since === 1 ? "" : "s"} ago` : "recently"} (WO {comeback.prior_order_number}){comeback.count && comeback.count > 1 ? ` · ${comeback.count} visits in 45 days` : ""}. Check the earlier fix before promising.</span>
              </div>
            )}
            <WatchOutBanner device={d.device} client={d.client} editing={watchEditing} setEditing={setWatchEditing}
              onChanged={reload} onFlash={flash} onPending={(f) => setPending("watchout", f)} />
          </div>
        )}

        {/* I6/E1 — an estimate's own actions: send it to the customer, or turn it into the job */}
        {isEst && (
          <div className="px-4 sm:px-6 py-2.5 border-b border-slate-200/70 bg-white flex-shrink-0 flex flex-wrap items-center gap-2">
            <SendMenu label="Send estimate" busy={estBusy === "send"} onSend={sendEstimate} />
            <button type="button" onClick={convertEstimate} disabled={!!estBusy}
              className={btnSecondary}>
              <M n="build" c="text-xs" />{estBusy === "convert" ? "Converting…" : "Convert to work order"}
            </button>
            <button type="button" onClick={newRevision} disabled={!!estBusy}
              className={btnSecondary}>
              <M n="edit_note" c="text-xs" />{estBusy === "revise" ? "Starting…" : "New revision"}
            </button>
            <span className="ml-auto text-[11px] text-ink-tertiary">{(d as any).estimate_approved_at ? "The customer approved this estimate."
              : (d as any).estimate_decline_reason ? "The customer declined this estimate."
              : "The customer can approve it from their tracking link."}</span>
          </div>
        )}
        {isEst && <EstimateTerms orderId={order.id} d={d as any} onChanged={() => { reload(); onChanged?.(); }} onFlash={flash} />}

        {/* Tabs */}
        <nav className="px-4 sm:px-6 border-b border-slate-200/80 flex space-x-6 text-xs font-medium bg-white flex-shrink-0 overflow-x-auto whitespace-nowrap">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`py-3 border-b-2 transition-colors flex items-center gap-1.5 ${tab === t.id ? "text-ink-primary border-primary font-semibold" : "text-ink-secondary hover:text-ink-primary border-transparent"}`}>
              <span>{t.label}</span>
              {t.badge && <span className="text-[11px] bg-rose-100 text-rose-700 px-1.5 rounded-full tabular-nums font-semibold">{t.badge}</span>}
              {t.dot && <span role="img" aria-label="Checklist incomplete" title="Checklist incomplete" className={`w-2 h-2 rounded-full ${t.dot}`} />}
            </button>
          ))}
        </nav>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 bg-slate-50/70 space-y-6">
          {tab === "general" && (
            <div className="space-y-5">
              {(urgent || d.scheduled_for || d.due_at) && (
                <div className={`${sectionCard} p-3.5 flex items-center justify-between`}>
                  <div className="flex items-center gap-3">
                    <div className={`w-9 h-9 rounded-xl grid place-items-center ${urgent ? "bg-rose-50 text-rose-600" : "bg-primary/10 text-primary"}`}><M n="schedule" c="text-base" /></div>
                    <div>
                      <p className="text-sm font-semibold text-ink-primary">{urgent ? ((d.priority ?? 0) >= 2 ? "Urgent job, same day" : "High priority job") : "Booked job"}</p>
                      <p className="text-xs text-ink-secondary">{[d.scheduled_for ? `Booked for ${friendlyDate(d.scheduled_for)}` : null, d.due_at ? `due ${fmtDT(d.due_at)}` : null].filter(Boolean).join(" · ") || "Being tracked"}</p>
                    </div>
                  </div>
                  {due ? <span className={`text-[11px] font-semibold px-2.5 py-1 rounded-full ${due[1] ? "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200" : "bg-slate-100 text-ink-secondary"}`}>{due[0]}</span>
                    : <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full bg-slate-100 text-ink-secondary">{urgent ? "Priority" : "Booked"}</span>}
                </div>
              )}

              {/* Client + Device cards */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div {...clickable(() => c.id && setSub("client"))} className="bg-white rounded-2xl border border-slate-200/90 p-4 shadow-clay-sm hover:border-primary/40 hover:shadow-clay-card transition-all cursor-pointer group">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2.5">
                      <Avatar name={c.name} size={40} />
                      <div>
                        <div className="flex items-center gap-1.5"><h4 className="text-sm font-semibold text-ink-primary group-hover:text-primary transition-colors">{c.name || "Unknown client"}</h4></div>
                        <p className="text-xs text-ink-secondary tabular-nums">{c.phone || "No phone"}</p>
                      </div>
                    </div>
                    <M n="open_in_new" c="text-ink-tertiary group-hover:text-primary transition-colors text-sm" />
                  </div>
                  <div className="mt-3 pt-2.5 border-t border-slate-100 text-xs text-ink-tertiary flex items-center justify-between">
                    <span className="truncate">{c.email || "No email"}</span>
                    {c.balance ? <span className={`text-[11px] font-semibold ${c.balance > 0 ? "text-rose-600" : "text-emerald-600"}`}>{c.balance > 0 ? "Owes " : "Credit "}{money(Math.abs(c.balance))}</span>
                      : <span className="text-[11px] font-semibold text-emerald-600 flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Nothing owed</span>}
                  </div>
                </div>
                {hasDevice ? (
                <div {...clickable(() => setSub("asset"))} className="bg-white rounded-2xl border border-slate-200/90 p-4 shadow-clay-sm hover:border-primary/40 hover:shadow-clay-card transition-all cursor-pointer group">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2.5">
                      <div className="w-10 h-10 rounded-xl bg-slate-100 text-ink-secondary flex items-center justify-center"><M n={deviceIcon(d.device)} c="text-xl" /></div>
                      <div>
                        <div className="flex items-center gap-1.5"><h4 className="text-sm font-semibold text-ink-primary group-hover:text-primary transition-colors">{devTitle}</h4>
                          {d.warranty_until && <span className="text-[11px] px-1.5 py-0.5 rounded-md bg-emerald-50 text-emerald-700 font-semibold">Under warranty</span>}
                        </div>
                        <p className="text-xs text-ink-secondary">{[dev.color, dev.asset_type].filter(Boolean).join(" · ") || dev.asset_label || "Device"}</p>
                      </div>
                    </div>
                    <button onClick={(e) => { e.stopPropagation(); setSub("asset"); }} className="flex items-center gap-1 px-2 py-1 rounded-lg text-primary text-[11px] font-semibold hover:bg-primary/10 transition-colors shrink-0"><M n="open_in_new" c="text-sm" />Details</button>
                  </div>
                  {(dev.serial || dev.imei) && (
                  <div className="mt-3 pt-2.5 border-t border-slate-100 text-xs text-ink-tertiary flex items-center justify-between">
                    {dev.serial && <span>Serial <span className="font-mono text-ink-secondary">{dev.serial}</span></span>}{dev.imei && <span>IMEI <span className="font-mono text-ink-secondary">{dev.imei}</span></span>}
                  </div>)}
                  <DevicePasscode assetId={String(dev.id)} hasPasscode={!!dev.has_passcode} onFlash={flash}
                    onChanged={(has) => setD((p) => (p.device ? { ...p, device: { ...p.device, has_passcode: has } } : p))} />
                </div>
                ) : (
                <div className="bg-slate-50/60 rounded-2xl border border-dashed border-slate-200 p-4 flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <div className="w-10 h-10 rounded-xl bg-slate-100 text-ink-tertiary flex items-center justify-center border border-slate-200"><M n="smartphone" c="text-xl" /></div>
                    <div>
                      <h4 className="text-sm font-semibold text-ink-secondary">No device on this job</h4>
                      <p className="text-xs text-ink-tertiary">Estimates and non-device jobs don't need one.</p>
                    </div>
                  </div>
                </div>
                )}
              </div>

              {/* Order parameters */}
              <Section title="Job details">
                <div className="space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <Ro label="Job type">
                    {woTypes && woTypes.length ? (
                      <Pick label="Job type" value={(d as any).type_id || ""} onChange={changeType}
                        opts={[...((d as any).type_id ? [] : [{ value: "", label: "No job type" }]), ...woTypes.map((t) => ({ value: t.id, label: t.name }))]} />
                    ) : <ReadOnly>{jobTypeLabel({ is_estimate: d.is_estimate, type_id: (d as any).type_id }, woTypes)}</ReadOnly>}
                  </Ro>
                  <Ro label="Priority">
                    <Pick label="Priority" value={String(Math.min(d.priority ?? 0, 2))} onChange={(v) => changePriority(Number(v))} red={urgent}
                      opts={[{ value: "0", label: "Normal" }, { value: "1", label: "High" }, { value: "2", label: "Urgent, same day" }]} />
                  </Ro>
                  <Ro label="Manager"><SelEmp label="Manager" value={d.manager?.id || ""} emps={emps.filter((e) => e.in_managers !== false || e.id === d.manager?.id)} onChange={(id) => reassign("manager_id", id)} /></Ro>
                  <Ro label="Technician"><SelEmp label="Technician" value={d.assigned_to?.id || ""} emps={emps.filter((e) => e.in_assignees !== false || e.id === d.assigned_to?.id)} onChange={(id) => reassign("assigned_to_id", id)} /></Ro>
                  <Ro label="Time on this job">
                    {byEmp.length > 0 ? (
                      <div className="rounded-lg bg-slate-50 border border-slate-200/70 p-2 space-y-1">
                        <div className="text-[11px] font-medium text-ink-tertiary">On this job</div>
                        {byEmp.map((b: any) => (
                          <div key={b.employee_id} className="flex items-center justify-between text-[11px]">
                            <span className="flex items-center gap-1.5"><Avatar name={b.employee_name} size={20} />{b.running && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />}<span className="font-semibold text-ink-primary">{b.employee_name}</span></span>
                            <span className="flex items-center gap-2">
                              <span className="tabular-nums text-ink-secondary">{secHM(b.total_seconds)}{b.running ? " · running" : ""}</span>
                              {b.running && <button type="button" onClick={() => stopEntry(b.employee_id)} className="px-1.5 py-0.5 rounded-md bg-rose-50 text-rose-600 border border-rose-200 text-[11px] font-semibold hover:bg-rose-100">Stop</button>}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : <span className="text-sm text-ink-tertiary">No time logged yet</span>}
                  </Ro>
                  <Ro label="Booked for">
                    <div className="relative">
                      <DateField mode="datetime" value={d.scheduled_for ? d.scheduled_for.slice(0, 16) : ""} onChange={(v) => reschedule(v)} />
                    </div>
                  </Ro>
                  <Ro label="Due">
                    <ReadOnly>
                      <span className={due?.[1] ? "text-rose-700 font-medium" : d.due_at ? "" : "text-ink-tertiary"}>{d.due_at ? fmtDT(d.due_at) : "No due date"}</span>
                      {due && <span className={`ml-2 px-2 py-0.5 rounded-full text-[11px] font-semibold ${due[1] ? "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200" : "bg-slate-100 text-ink-secondary"}`}>{due[0]}</span>}
                    </ReadOnly>
                  </Ro>
                </div>
                <Ro label="What the customer reported">
                  <p className={`text-sm leading-relaxed whitespace-pre-wrap ${d.complaint ? "text-ink-primary" : "text-ink-tertiary"}`}>{or(d.complaint, "No complaint recorded yet.")}</p>
                </Ro>
                <JobCardFields orderId={order.id} typeId={(d as any).type_id} value={(d as any).custom_data} onFlash={flash}
                  onSaved={(cd) => setD({ ...d, custom_data: cd } as Detail)} />
                <NotesEditor initTech={d.tech_notes || ""} initConcl={d.conclusion || ""} diagnosis={d.diagnosis || ""} onSave={saveNotes} onPending={(f) => setPending("notes", f)} />
                </div>
              </Section>

              <ChecklistRings orderId={order.id} onOpen={() => setTab("checklists")} />

              {(d as any).written_off > 0 && (
                <div role="note" className="flex items-start gap-2 rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2.5 text-xs text-ink-secondary">
                  <M n="block" c="text-base text-ink-tertiary" />
                  <span>Dropped{(d as any).cancel_reason ? `: ${(d as any).cancel_reason}` : ""}. The {money((d as any).written_off)} the customer still owed was written off, so it isn't counted as a debt or as profit.</span>
                </div>
              )}
              <LinkedJobs orderId={order.id} />
              <JobItems orderId={order.id} onFlash={flash} onChanged={reload} />

              <JobTasks orderId={order.id} emps={emps} onFlash={flash} />

              <IntakePhotos orderId={order.id} photos={d.intake_photos} onChanged={reload} onFlash={flash} />

              {/* J1 — the customer's signature on this job, if they've signed */}
              {d.signature && (
                <div className="bg-white rounded-xl border border-slate-200/90 p-3.5 flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2"><M n="draw" c="text-base text-primary" /><h5 className="text-xs font-bold text-ink-primary">Customer signature</h5></div>
                    <img src={d.signature} alt="Customer signature" style={{ background: "#fff" }} className="mt-2 h-16 max-w-[260px] object-contain rounded-lg border border-slate-200" />
                  </div>
                  <button onClick={() => setSigOpen(true)} className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50">Sign again</button>
                </div>
              )}

              {/* Barcode */}
              <Section title="Bench ticket" icon="qr_code_2" meta={<span className="font-mono">{woNum}</span>}
                description="Scan it with a barcode gun to open this job."
                actions={<button onClick={printWO} className={btnSecondary}><M n="print" c="text-sm" />Print sticker</button>} />
            </div>
          )}

          {tab === "services" && (
            <div className="space-y-4">
              <ServicesTab orderId={order.id} locationId={(d as any).location_id} deviceId={d.device?.id} lines={d.lines} total={d.total} onFlash={flash} onChanged={reload}
                labor={labor} onClockIn={(lineId) => startTimer(lineId)} isEstimate={isEst} emps={emps}
                locked={isClosed && !can("can_edit_closed_ticket")} />
            </div>
          )}
          {tab === "checklists" && <ChecklistsTab orderId={order.id} typeId={(d as any).type_id} />}
          {tab === "invoices" && <InvoicesTab onChanged={reload} orderId={order.id} total={d.total} writtenOff={(d as any).written_off || 0} deposit={(d as any).deposit_amount} currencyCode={(d as any).currency_code} fxRate={(d as any).fx_rate} currencySymbol={(d as any).currency_symbol} onCurrencyChange={(code, rate, symbol) => setD({ ...d, currency_code: code, fx_rate: rate, currency_symbol: symbol } as any)} onFlash={flash} />}
          {/* G3 — one customer conversation: ActivityTab shows the shared thread and its message box */}
          {tab === "activity" && (
            <ActivityTab orderId={order.id} clientName={c.name} clientPhone={c.phone} onFlash={flash}
              threadKey={threadKey} onSent={() => setThreadKey((k) => k + 1)} />
          )}
        </div>

        {/* Footer: print on the left; close and save on the right (the drawer convention) */}
        <footer className="px-6 py-3.5 border-t border-slate-200/90 bg-white flex flex-wrap items-center justify-between gap-3 flex-shrink-0">
          <div className="flex flex-wrap items-center gap-2">
            <PrintMenu key={order.id} orderId={order.id} status={d.status} canInvoices={can("can_view_invoices")} onSticker={printWO}
              onItemised={hasKit && can("can_print_itemised_copy") ? printItemised : undefined} itemisedType={(order as any).is_estimate ? "estimate" : "work_order"}
              autoPrint={(order as any).is_estimate ? undefined : { canChange: can("can_manage_settings") }} onError={flash} />
            <button onClick={() => setSigOpen(true)} className={btnSecondary}><M n="draw" c="text-sm" />{d.signature ? "Sign again" : "Customer signature"}</button>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={tryClose} className={btnSecondary}>Close</button>
            <button onClick={saveAndClose} disabled={saving} className={`${btnPrimary} shadow-clay-sm`}><M n="check" c="text-sm" />{saving ? "Saving…" : "Save and close"}</button>
          </div>
        </footer>

        {toast && <div role="status" className={`absolute bottom-20 left-1/2 -translate-x-1/2 max-w-[90%] text-xs font-semibold px-4 py-2 rounded-xl shadow-clay-drawer z-20 ${toast.tone === "warn" ? "bg-amber-50 text-amber-900 border border-amber-300 flex items-start gap-1.5" : "bg-ink-primary text-white"}`}>{toast.tone === "warn" && <M n="info" c="text-sm" />}{toast.m}</div>}
        {sigOpen && <SignaturePad busy={sigBusy} onSave={saveSignature} onClose={() => setSigOpen(false)}
          hint={d.signature ? "This replaces the signature already on the job." : "Ask the customer to sign in the box."} />}
      </div>

      {sub === "client" && c.id && <ClientDrawer woNum={woNum} clientId={String(c.id)} onClose={() => { setSub(null); reload(); }} onOpenAsset={() => setSub("asset")} />}
      {sub === "asset" && dev.id && <AssetDrawer woNum={woNum} assetId={String(dev.id)} onClose={() => setSub(null)} onSaved={reload} />}
    </div>
  );
}

/* ---- J3 device passcode — hidden until someone allowed asks to see it (each look is logged) ---- */
export function DevicePasscode({ assetId, hasPasscode, onFlash, onChanged }: {
  assetId: string; hasPasscode: boolean; onFlash: Flash; onChanged: (has: boolean) => void;
}) {
  const [shown, setShown] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setShown(null); setEditing(false); }, [assetId]);
  async function reveal() {
    setBusy(true);
    try { const r = await api.get<{ passcode: string | null }>(`/assets/${assetId}/passcode`); setShown(r?.passcode ?? ""); }
    catch (e: any) { onFlash(e.message || "Couldn't show the passcode", "warn"); }
    finally { setBusy(false); }
  }
  async function save(passcode: string | null) {
    setBusy(true);
    try {
      const r = await api.put<{ ok: boolean; has_passcode: boolean }>(`/assets/${assetId}/passcode`, { passcode });
      onChanged(!!r?.has_passcode); setEditing(false); setVal(""); setShown(null);
      onFlash(passcode ? "Passcode saved" : "Passcode removed");
    } catch (e: any) { onFlash(e.message || "Couldn't save the passcode", "warn"); }
    finally { setBusy(false); }
  }
  const link = "text-[11px] font-semibold text-primary hover:underline disabled:opacity-50";
  return (
    <div onClick={(e) => e.stopPropagation()} className="mt-2.5 pt-2.5 border-t border-slate-100 text-xs text-ink-tertiary cursor-default">
      {editing ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <button type="button" disabled={busy || !val.trim()} onClick={() => save(val.trim())} className="px-2.5 py-1 rounded-lg bg-primary text-white text-[11px] font-bold disabled:opacity-50">Save</button>
          <input type="password" autoComplete="off" autoFocus value={val} onChange={(e) => setVal(e.target.value)} aria-label="Device passcode"
            onKeyDown={(e) => { if (e.key === "Enter" && val.trim()) save(val.trim()); if (e.key === "Escape") setEditing(false); }}
            placeholder="New passcode" className="glass-input flex-1 min-w-[120px] py-1 px-2 text-xs font-mono rounded-lg" />
          {hasPasscode && <button type="button" disabled={busy} onClick={() => save(null)} className="text-[11px] font-semibold text-rose-600 hover:underline">Remove</button>}
          <button type="button" onClick={() => { setEditing(false); setVal(""); }} className="text-[11px] font-semibold text-ink-tertiary hover:text-ink-primary">Cancel</button>
        </div>
      ) : hasPasscode ? (
        <div className="flex items-center gap-2">
          <M n="lock" c="text-sm" />
          <span>Passcode <span className="font-mono text-ink-secondary">{shown !== null ? (shown || "(empty)") : "••••"}</span></span>
          {shown === null
            ? <button type="button" disabled={busy} onClick={reveal} className={link}>Show</button>
            : <button type="button" onClick={() => setShown(null)} className={link}>Hide</button>}
          <button type="button" onClick={() => setEditing(true)} className="ml-auto text-[11px] font-semibold text-ink-tertiary hover:text-ink-primary">Change</button>
        </div>
      ) : (
        <button type="button" onClick={() => setEditing(true)} className="flex items-center gap-1 text-[11px] font-semibold text-ink-tertiary hover:text-primary"><M n="lock" c="text-sm" />Add device passcode</button>
      )}
    </div>
  );
}

/* ---- several items per job ---- */
type JobItem = { id: string; asset_id?: string | null; asset?: { id: string; brand?: string; model?: string; serial?: string; imei?: string } | null;
  label?: string | null; complaint?: string | null; outcome: string; collected_at?: string | null; is_primary?: boolean; line_count: number; total: number;
  unbilled_lines?: number; unbilled_total?: number };
type JobLink = { id: string; order_number: string };
const itemName = (it: JobItem) => (it.asset ? [it.asset.brand, it.asset.model].filter(Boolean).join(" ") || it.asset.serial || "Item" : it.label || "Item");
const OUTCOMES: [string, string][] = [["open", "Open"], ["fixed", "Fixed"], ["cant_fix", "Can't fix"], ["declined", "Declined"]];
const OUT_TONE: Record<string, string> = { fixed: "bg-emerald-50 text-emerald-700 ring-emerald-200", cant_fix: "bg-amber-50 text-amber-700 ring-amber-200", declined: "bg-slate-100 text-ink-secondary ring-slate-200", open: "bg-white text-ink-primary ring-slate-300" };

// Settings › Industry & assets › "Several items per job". Hidden unless the shop has it on, except a
// job that already holds several items still lists them (read-only) after it's switched off.
function JobItems({ orderId, onFlash, onChanged }: { orderId: string; onFlash: (m: string) => void; onChanged?: () => void }) {
  const { can } = useMe();
  const nav = useNavigate();
  const [st, setSt] = useState<{ enabled: boolean; items: JobItem[]; split_from?: JobLink | null; split_off?: JobLink[]; job_unbilled?: number } | null>(null);
  // "Collect & invoice": pick what the customer is taking now (null = not picking).
  const [pick, setPick] = useState<Set<string> | null>(null);
  const [jobLines, setJobLines] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [cands, setCands] = useState<any[]>([]);
  const [pickAsset, setPickAsset] = useState(""); const [label, setLabel] = useState(""); const [complaint, setComplaint] = useState("");
  const load = () => api.get<any>(`/orders/${orderId}/items`).then((r) => setSt({ enabled: !!r.enabled, items: r.items || [], split_from: r.split_from, split_off: r.split_off || [], job_unbilled: r.job_unbilled || 0 })).catch(() => setSt(null));
  useEffect(() => { load(); setPick(null); /* eslint-disable-next-line */ }, [orderId]);
  if (!st || (!st.enabled && st.items.length < 2)) return null;
  const ro = !st.enabled;
  async function call(fn: () => Promise<any>, ok?: string) {
    try { const r = await fn(); if (r?.items) setSt((s) => (s ? { ...s, items: r.items } : s)); if (ok) onFlash(ok); onChanged?.(); }
    catch (e: any) { onFlash(e?.message || "Couldn't save that"); }
  }
  function openAdd() { setAdding(true); setPickAsset(""); setLabel(""); setComplaint(""); api.get<any>(`/orders/${orderId}/items/candidates`).then((r) => setCands(r.items || [])).catch(() => setCands([])); }
  async function add() {
    if (!pickAsset && !label.trim()) return;
    await call(() => api.post(`/orders/${orderId}/items`, { asset_id: pickAsset || undefined, label: pickAsset ? undefined : label, complaint }), "Item added");
    setAdding(false);
  }
  const done = st.items.filter((i) => i.outcome !== "open").length;
  const waiting = st.items.filter((i) => !i.collected_at);
  const canBill = !ro && can("can_create_invoices") && waiting.length > 0;
  const canSplit = !ro && can("can_create_tickets");
  const picked = pick ? waiting.filter((i) => pick.has(i.id)) : [];
  const lastOut = picked.length > 0 && picked.length === waiting.length;
  const withJob = jobLines ?? lastOut;
  const pickTotal = picked.reduce((n, i) => n + (i.unbilled_total || 0), 0) + (withJob ? st.job_unbilled || 0 : 0);
  function startPick() { setPick(new Set()); setJobLines(null); setAdding(false); }
  function toggle(id: string) { setPick((p) => { const n = new Set(p || []); if (n.has(id)) n.delete(id); else n.add(id); return n; }); }
  async function collect() {
    if (!picked.length) return;
    setBusy(true);
    try {
      const r = await api.post<any>(`/orders/${orderId}/items/collect`, { item_ids: picked.map((i) => i.id), include_job_lines: withJob });
      onFlash(r.invoice ? `Invoice ${r.invoice.invoice_number} created for ${money(r.invoice.total)}. The job stays open.` : "Marked as collected. Nothing left to bill.");
      setPick(null); load(); onChanged?.();
    } catch (e: any) { onFlash(e?.message || "Couldn't collect those items"); }
    finally { setBusy(false); }
  }
  async function split(it: JobItem) {
    const msg = `Move ${itemName(it)}${it.line_count ? ` and its ${it.line_count} line${it.line_count === 1 ? "" : "s"} (${money(it.total)})` : ""} to a new job for the same customer? The two jobs stay linked.`;
    if (!(await confirmDialog(msg, { title: "Split into its own job", confirmLabel: "Split" }))) return;
    try {
      const r = await api.post<any>(`/orders/${orderId}/items/${it.id}/split`, {});
      onFlash(`${itemName(it)} is now job ${r.order_number}`);
      load(); onChanged?.();
    } catch (e: any) { onFlash(e?.message || "Couldn't split that item"); }
  }
  const links = [...(st.split_from ? [{ ...st.split_from, rel: "Split from" }] : []), ...(st.split_off || []).map((l) => ({ ...l, rel: "Split off" }))];
  return (
    <Section flush title="Items on this job" icon="inventory" meta={st.items.length}
      description={`${done} of ${st.items.length} have a result${ro ? " · read-only, several items per job is off" : ""}`}
      actions={!ro && !adding && !pick ? (<>
        {canBill && <button onClick={startPick} className={btnSecondary}><M n="shopping_bag" c="text-sm" />Collect &amp; invoice</button>}
        <button onClick={openAdd} className={btnSecondary}><M n="add" c="text-sm" />Add item</button>
      </>) : undefined}>
      {links.length > 0 && (
        <div className="px-4 py-2 border-b border-slate-100 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-tertiary">
          <M n="link" c="text-sm" />
          {links.map((l) => (
            <span key={l.id}>{l.rel} <button type="button" onClick={() => nav(`/orders?open=${l.id}`)} className="font-mono font-semibold text-primary hover:underline">{l.order_number}</button></span>
          ))}
        </div>
      )}
      {pick && (
        <div className="px-4 py-2.5 bg-primary/5 border-b border-primary/10 text-xs text-ink-primary">
          <span className="font-semibold">What is the customer taking now?</span> <span className="text-ink-secondary">Tick the items. They get one invoice for just their work, and the job stays open for the rest.</span>
        </div>
      )}
      {adding && (
        <div className="px-4 py-3 bg-slate-50/70 border-b border-slate-100 space-y-2">
          <div className="flex flex-wrap gap-2">
            {cands.length > 0 && (
              <select value={pickAsset} onChange={(e) => { setPickAsset(e.target.value); if (e.target.value) setLabel(""); }} aria-label="Customer's item" className="glass-input rounded-lg px-2 py-2 text-xs font-medium min-w-[200px]">
                <option value="">Pick one of the customer's items…</option>
                {cands.map((a) => <option key={a.id} value={a.id}>{[a.brand, a.model].filter(Boolean).join(" ") || "Item"}{a.serial ? ` · ${a.serial}` : ""}</option>)}
              </select>
            )}
            <input value={label} onChange={(e) => { setLabel(e.target.value); if (e.target.value) setPickAsset(""); }} placeholder={cands.length ? "Or type what it is" : "Makita grinder"} aria-label="Item name" className="glass-input flex-1 min-w-[180px] rounded-lg px-3 py-2 text-xs" />
          </div>
          <textarea value={complaint} onChange={(e) => setComplaint(e.target.value)} rows={2} placeholder="What's wrong with it?" aria-label="Problem" className="glass-input w-full rounded-lg px-3 py-2 text-xs" />
          <div className="flex gap-2">
            <button onClick={add} disabled={!pickAsset && !label.trim()} className="px-3.5 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-40">Add item</button>
            <button onClick={() => setAdding(false)} className="px-3 py-1.5 rounded-lg text-xs font-semibold text-ink-secondary hover:bg-slate-100">Cancel</button>
          </div>
        </div>
      )}
      <div className="divide-y divide-slate-100">
        {st.items.map((it, n) => (
          <div key={it.id} className="px-4 py-3">
            <div className="flex items-start gap-3">
              {pick && !it.collected_at
                ? <input type="checkbox" checked={pick.has(it.id)} onChange={() => toggle(it.id)} aria-label={`Customer is taking ${itemName(it)}`} className="w-5 h-5 mt-1 accent-primary flex-shrink-0 cursor-pointer" />
                : <span className={`w-7 h-7 rounded-lg grid place-items-center text-[11px] font-semibold flex-shrink-0 ${pick ? "bg-slate-50 text-ink-tertiary" : "bg-slate-100 text-ink-secondary"}`}>{n + 1}</span>}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-ink-primary">{itemName(it)}</span>
                  {it.asset?.serial && <span className="text-[11px] text-ink-tertiary">Serial <span className="font-mono">{it.asset.serial}</span></span>}
                  {it.is_primary && <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-md bg-primary/10 text-primary">Main item</span>}
                  {it.collected_at && <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-md bg-emerald-50 text-emerald-700">Collected</span>}
                </div>
                <ItemComplaint value={it.complaint || ""} readOnly={ro} onSave={(v) => call(() => api.patch(`/orders/${orderId}/items/${it.id}`, { complaint: v }))} />
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {OUTCOMES.map(([k, l]) => (
                    <button key={k} type="button" disabled={ro} aria-pressed={it.outcome === k} onClick={() => call(() => api.patch(`/orders/${orderId}/items/${it.id}`, { outcome: k }))}
                      className={`px-2.5 py-1 rounded-full text-[11px] font-semibold ring-1 ring-inset transition-colors disabled:cursor-default ${it.outcome === k ? OUT_TONE[k] : "text-ink-tertiary ring-transparent hover:bg-slate-100"}`}>{l}</button>
                  ))}
                  {!pick && <label className={`ml-2 flex items-center gap-1.5 text-[11px] text-ink-secondary ${ro ? "" : "cursor-pointer"}`}>
                    <input type="checkbox" disabled={ro} checked={!!it.collected_at} onChange={(e) => call(() => api.patch(`/orders/${orderId}/items/${it.id}`, { collected: e.target.checked }))} className="accent-primary" />Collected
                  </label>}
                  <span className="ml-auto text-[11px] text-ink-tertiary tabular-nums">{it.line_count ? `${it.line_count} line${it.line_count === 1 ? "" : "s"} · ${money(it.total)}${it.collected_at && !it.unbilled_lines ? " · invoiced" : ""}` : "No lines yet"}</span>
                  {canSplit && !pick && !it.is_primary && !it.collected_at && st.items.length > 1 && (
                    <button type="button" aria-label={`Split ${itemName(it)} into its own job`} title="Split into its own job" onClick={() => split(it)}
                      className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:text-primary hover:bg-primary/5"><M n="call_split" c="text-sm" /></button>
                  )}
                  {!ro && !pick && !it.is_primary && (
                    <button type="button" aria-label={`Remove ${itemName(it)}`} title="Remove from this job"
                      onClick={async () => { if (await confirmDialog(`Remove ${itemName(it)} from this job? Its services and parts stay on the job, marked "whole job".`, { title: "Remove item", confirmLabel: "Remove", danger: true })) call(() => api.del(`/orders/${orderId}/items/${it.id}`), "Item removed"); }}
                      className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:text-rose-600 hover:bg-rose-50"><M n="delete" c="text-sm" /></button>
                  )}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
      {pick && (
        <div className="px-4 py-3 border-t border-slate-100 bg-slate-50/70 flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <button onClick={collect} disabled={!picked.length || busy} className={btnPrimary}>
              {busy ? "Working…" : picked.length ? `Collect ${picked.length} & invoice ${money(pickTotal)}` : "Tick what they're taking"}
            </button>
            <button onClick={() => setPick(null)} className={btnGhost}>Cancel</button>
          </div>
          {(st.job_unbilled || 0) > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-ink-secondary cursor-pointer">
              <input type="checkbox" checked={withJob} onChange={(e) => setJobLines(e.target.checked)} className="accent-primary" />
              Add whole-job charges ({money(st.job_unbilled)}){lastOut ? "" : " now"}
            </label>
          )}
        </div>
      )}
    </Section>
  );
}

// "Continue as a linked job": the job this one carries on from, and the jobs that carry it on.
function LinkedJobs({ orderId }: { orderId: string }) {
  const nav = useNavigate();
  const [l, setL] = useState<{ continued_from: JobLink | null; continued_in: JobLink[] } | null>(null);
  useEffect(() => { api.get<any>(`/orders/${orderId}/linked`).then(setL).catch(() => setL(null)); }, [orderId]);
  if (!l || (!l.continued_from && !(l.continued_in || []).length)) return null;
  const links = [...(l.continued_from ? [{ ...l.continued_from, rel: "Continued from" }] : []),
                 ...(l.continued_in || []).map((x) => ({ ...x, rel: "Continued in" }))];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-secondary">
      <M n="link" c="text-sm text-ink-tertiary" />
      {links.map((x) => (
        <span key={x.rel + x.id}>{x.rel} <button type="button" onClick={() => nav(`/orders?open=${x.id}`)} className="font-mono font-semibold text-primary hover:underline">{x.order_number}</button></span>
      ))}
    </div>
  );
}

function ItemComplaint({ value, readOnly, onSave }: { value: string; readOnly: boolean; onSave: (v: string) => void }) {
  const [v, setV] = useState(value);
  useEffect(() => { setV(value); }, [value]);
  if (readOnly) return value ? <p className="mt-1 text-xs text-ink-secondary">{value}</p> : null;
  return <input value={v} onChange={(e) => setV(e.target.value)} onBlur={() => { if (v !== value) onSave(v); }} placeholder="What's wrong with it?" aria-label="Problem"
    className="mt-1 w-full text-xs text-ink-secondary bg-transparent border-b border-transparent hover:border-slate-200 focus:border-primary/50 focus:outline-none py-0.5" />;
}

/* ---- field helpers ---- */
// One label style for every field in the drawer's cards.
const fieldLabel = "block text-xs font-medium text-ink-secondary mb-1.5";
function Ro({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className={fieldLabel}>{label}</div>{children}</div>;
}
// J6 — a work-order attribute picker that saves on change.
function Pick({ value, opts, onChange, label, red }: { value: string; opts: { value: string; label: string }[]; onChange: (v: string) => void; label: string; red?: boolean }) {
  return (
    <div className="relative">
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}
        className={`glass-input w-full py-2 px-3 pr-8 text-xs rounded-xl appearance-none ${red ? "font-semibold text-rose-700" : "font-medium text-ink-primary"}`}>
        {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <M n="expand_more" c="absolute right-2.5 top-2.5 text-ink-tertiary pointer-events-none text-sm" />
    </div>
  );
}
// A value the drawer shows but doesn't edit here: plain text, not an input box.
function ReadOnly({ children }: { children: React.ReactNode }) {
  return <div className="text-sm text-ink-primary flex items-center min-h-[20px]">{children}</div>;
}
function SelEmp({ value, emps, onChange, label }: { value: string; emps: Emp[]; onChange: (id: string) => void; label?: string }) {
  return (
    <div className="relative">
      <span className="absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none"><Avatar name={emps.find((e) => e.id === value)?.name} size={22} /></span>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} className="glass-input w-full py-2 pl-9 pr-3 text-xs font-medium text-ink-primary rounded-xl appearance-none">
        <option value="">Unassigned</option>
        {emps.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
      </select>
      <M n="expand_more" c="absolute right-2.5 top-2.5 text-ink-tertiary pointer-events-none text-sm" />
    </div>
  );
}

/* ---- Notes editor ---- */
function NotesEditor({ initTech, initConcl, diagnosis, onSave, onPending }: { initTech: string; initConcl: string; diagnosis: string; onSave: (t: string, c: string) => Promise<boolean>; onPending?: (save: (() => Promise<boolean>) | null) => void }) {
  const [tech, setTech] = useState(initTech);
  const [concl, setConcl] = useState(initConcl);
  useEffect(() => { setTech(initTech); setConcl(initConcl); }, [initTech, initConcl]);
  const dirty = tech !== initTech || concl !== initConcl;
  useEffect(() => { onPending?.(dirty ? () => onSave(tech, concl) : null); }, [dirty, tech, concl]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => onPending?.(null), []);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1">
      <div>
        <div className={fieldLabel}>Manager notes <span className="font-normal text-ink-tertiary">(only your team sees these)</span></div>
        <p className={`text-sm leading-relaxed whitespace-pre-wrap ${diagnosis ? "text-ink-primary" : "text-ink-tertiary"}`}>{diagnosis || "No manager notes."}</p>
      </div>
      <div className="space-y-2">
        <div className={`${fieldLabel} !mb-0`}>Technician bench notes</div>
        <textarea value={tech} onChange={(e) => setTech(e.target.value)} rows={2} aria-label="Technician working notes" className={`${houseInput} w-full resize-none`} placeholder="Working notes…" />
        <textarea value={concl} onChange={(e) => setConcl(e.target.value)} rows={2} aria-label="Conclusion the customer sees" className={`${houseInput} w-full resize-none`} placeholder="Conclusion the customer sees…" />
        <div className="flex"><button onClick={() => onSave(tech, concl)} className={btnSecondary}>Save notes</button></div>
      </div>
    </div>
  );
}

/* ---- Checklist rings ---- */
// A thin progress arc: grey track, the filled share in blue, green once every check is done.
function ProgressRing({ pct, size = 40, label = true }: { pct: number; size?: number; label?: boolean }) {
  const sw = 3; const r = (size - sw) / 2; const len = 2 * Math.PI * r;
  const done = pct >= 100;
  return (
    <span className="relative grid place-items-center flex-shrink-0" style={{ width: size, height: size }} role="img" aria-label={`${pct}% done`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={sw} className="stroke-slate-200" />
        {pct > 0 && <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={sw} strokeLinecap="round"
          strokeDasharray={len} strokeDashoffset={len * (1 - Math.min(pct, 100) / 100)} className={done ? "stroke-emerald-500" : "stroke-primary"} />}
      </svg>
      {(label || done) && <span className={`absolute text-[11px] font-semibold tabular-nums ${done ? "text-emerald-700" : "text-ink-primary"}`}>{done ? <M n="check" c={size < 36 ? "text-sm" : "text-base"} /> : `${pct}%`}</span>}
    </span>
  );
}
function ChecklistRings({ orderId, onOpen }: { orderId: string; onOpen: () => void }) {
  const [p, setP] = useState<{ done: number; total: number; name: string }[] | null>(null);
  useEffect(() => { api.get<any>(`/orders/${orderId}/checklists`).then((r) => {
    const cls = r.items || r || [];
    // The list endpoint sends a summary (items_done / items_total); older shapes carried done/total or the items.
    setP(cls.map((c: any) => ({ done: c.items_done ?? c.done ?? c.items?.filter((i: any) => i.done).length ?? 0, total: c.items_total ?? c.total ?? c.items?.length ?? 0, name: c.title || c.name || "Checklist" })));
  }).catch(() => setP([])); }, [orderId]);
  if (p === null) return <div className={`${sectionCard} p-4 text-xs text-ink-tertiary`}>Loading checklists…</div>;
  if (p.length === 0) return (
    <div className={`${sectionCard} p-4 flex items-center justify-between`}>
      <span className="text-xs text-ink-tertiary">No checklists on this job.</span>
      <button onClick={onOpen} className="text-xs font-semibold text-primary hover:underline">Add one</button>
    </div>
  );
  return (<>{p.slice(0, 2).map((c, i) => {
    const pct = c.total ? Math.round((c.done / c.total) * 100) : 0;
    const done = pct === 100;
    return (
      <div key={i} className={`${sectionCard} p-4 flex items-center justify-between`}>
        <div className="flex items-center gap-3">
          <ProgressRing pct={pct} />
          <div><h4 className="text-sm font-semibold text-ink-primary">{c.name}</h4><p className={`text-xs tabular-nums ${done ? "text-emerald-700 font-medium" : "text-ink-tertiary"}`}>{done ? `All ${c.total} checks done` : `${c.done} of ${c.total} checks done`}</p></div>
        </div>
        <button onClick={onOpen} className="text-xs font-semibold text-primary hover:underline">View</button>
      </div>
    );
  })}</>);
}

/* ---- Watch-out banner — prominent handling warnings for the asset + the customer ---- */
// Shown in the drawer's alert area only when there is a warning, or while editing one
// ("Add watch-out" lives in the header's ⋯ menu).
function WatchOutBanner({ device, client, editing, setEditing, onChanged, onFlash, onPending }: {
  device?: { id?: string; watch_out?: string | null } | null;
  client?: { id?: string; name?: string; watch_out?: string | null } | null;
  editing: boolean; setEditing: (v: boolean) => void;
  onChanged: () => void; onFlash: (m: string) => void;
  onPending?: (save: (() => Promise<boolean>) | null) => void;
}) {
  const [dev, setDev] = useState(device?.watch_out || "");
  const [cli, setCli] = useState(client?.watch_out || "");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setDev(device?.watch_out || ""); setCli(client?.watch_out || ""); }, [device?.watch_out, client?.watch_out, device?.id, client?.id]);
  const hasAny = !!(device?.watch_out || client?.watch_out);
  async function save() {
    setBusy(true);
    try {
      if (client?.id && (cli || "") !== (client.watch_out || "")) await api.patch(`/clients/${client.id}`, { watch_out: cli || null });
      if (device?.id && (dev || "") !== (device.watch_out || "")) await api.put(`/assets/${device.id}`, { watch_out: dev || null });
      onFlash("Warnings saved"); setEditing(false); onChanged(); return true;
    } catch (e: any) { onFlash(e.message || "Could not save"); return false; } finally { setBusy(false); }
  }
  const dirty = editing && ((cli || "") !== (client?.watch_out || "") || (dev || "") !== (device?.watch_out || ""));
  useEffect(() => { onPending?.(dirty ? save : null); }, [dirty, cli, dev]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!hasAny && !editing) return null;
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5">
      {editing ? (
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-amber-900"><M n="warning" c="text-sm" />Watch-outs</div>
          {device?.id && <input value={dev} onChange={(e) => setDev(e.target.value)} aria-label="Asset warning" placeholder="Battery swollen, handle with care" className={`${houseInput} w-full`} />}
          {client?.id && <input value={cli} onChange={(e) => setCli(e.target.value)} aria-label="Customer warning" placeholder={`Customer warning${client.name ? ` for ${client.name}` : ""}`} className={`${houseInput} w-full`} />}
          <div className="flex gap-2"><button onClick={save} disabled={busy} className={btnPrimary}>Save</button><button onClick={() => { setEditing(false); setDev(device?.watch_out || ""); setCli(client?.watch_out || ""); }} className={btnGhost}>Cancel</button></div>
        </div>
      ) : (
        <div className="flex items-start gap-2">
          <M n="warning" c="text-base text-amber-600 mt-0.5" />
          <div className="flex-1 space-y-0.5 text-xs">
            {device?.watch_out && <div className="text-amber-900"><span className="font-semibold">Asset:</span> {device.watch_out}</div>}
            {client?.watch_out && <div className="text-amber-900"><span className="font-semibold">Customer:</span> {client.watch_out}</div>}
          </div>
          <button onClick={() => setEditing(true)} className="text-xs font-semibold text-amber-800 hover:text-amber-950">Edit</button>
        </div>
      )}
    </div>
  );
}

/* ---- Intake condition photos — how the device looked at check-in (dispute protection) ---- */
type IntakePhoto = { id: string; url: string; marked_id?: string; marked_url?: string };
function IntakePhotos({ orderId, photos, onChanged, onFlash }: {
  orderId: string; photos?: IntakePhoto[]; onChanged: () => void; onFlash: (m: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [annotate, setAnnotate] = useState<IntakePhoto | null>(null);
  const [showOrig, setShowOrig] = useState<Record<string, boolean>>({});   // 144dg — per-photo original⇄marked toggle
  const list = photos || [];
  async function save(ids: string[]) {
    await api.patch(`/orders/${orderId}/intake-photos`, { photos: ids }); onChanged();
  }
  async function saveMarkup(origId: string, dataUrl: string) {
    try {
      const r = await api.post<any>("/media", { data: dataUrl, name: "markup.jpg", mime: "image/jpeg" });
      // 144dg — keep the untouched original; attach the marked copy as a linked derivative
      await api.patch(`/orders/${orderId}/intake-markups`, { original_id: origId, marked_id: r.id });
      setAnnotate(null); onChanged(); onFlash("Markup saved");
    } catch (e: any) { onFlash(e.message || "Could not save markup"); }
  }
  async function add(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      const ids = list.map((p) => p.id);
      for (const f of Array.from(files)) {
        const data = await readAsDataUrl(f);
        const r = await api.post<any>("/media", { data, name: f.name, mime: f.type });
        ids.push(r.id);
      }
      await save(ids); onFlash("Photos added");
    } catch (e: any) { onFlash(e.message || "Upload failed (file may be too large)"); }
    finally { setBusy(false); }
  }
  async function remove(id: string) {
    setBusy(true);
    try { await save(list.filter((p) => p.id !== id).map((p) => p.id)); }
    catch (e: any) { onFlash(e.message || "Could not remove"); } finally { setBusy(false); }
  }
  return (
    <Section title="Intake photos" icon="photo_camera" description="How the device looked at check-in."
      actions={
        <label className={`${btnSecondary} cursor-pointer ${busy ? "opacity-50 pointer-events-none" : ""}`}>
          <input type="file" accept="image/*" multiple className="hidden" onChange={(e) => { add(e.target.files); e.currentTarget.value = ""; }} />
          <M n="add" c="text-sm" />{busy ? "Uploading…" : "Add photos"}
        </label>
      }>
      {list.length === 0 ? (
        <p className="text-xs text-ink-tertiary">No photos yet. Snap how the device arrived to protect against disputes.</p>
      ) : (
        <div className="flex flex-wrap gap-3 pt-2 pr-2">
          {list.map((p) => {
            const hasMark = !!p.marked_url;
            const viewUrl = hasMark && !showOrig[p.id] ? p.marked_url! : p.url;
            return (
            <span key={p.id} className="relative group">
              <AuthedImage src={viewUrl} alt="intake" onClick={() => setAnnotate(p)} className="w-16 h-16 rounded-lg object-cover border border-slate-200 bg-slate-100 cursor-pointer" />
              {hasMark && <span className="absolute top-0.5 left-0.5 text-[11px] leading-4 font-semibold bg-white/95 text-ink-primary px-1 rounded shadow-sm">{showOrig[p.id] ? "Original" : "Marked"}</span>}
              {/* mark up always edits the ORIGINAL, so re-marking stays non-destructive */}
              <button onClick={() => setAnnotate(p)} className="absolute bottom-0.5 left-0.5 w-5 h-5 rounded-md bg-ink-primary/85 text-white flex items-center justify-center opacity-0 group-hover:opacity-100" title="Mark up"><M n="edit" c="text-[12px]" /></button>
              {hasMark && <button onClick={() => setShowOrig((s) => ({ ...s, [p.id]: !s[p.id] }))} className="absolute bottom-0.5 right-0.5 w-5 h-5 rounded-md bg-ink-primary/85 text-white flex items-center justify-center opacity-0 group-hover:opacity-100" title={showOrig[p.id] ? "Show marked" : "Show original"}><M n="swap_horiz" c="text-[12px]" /></button>}
              <button onClick={() => remove(p.id)} aria-label="Remove photo" title="Remove" className="absolute -top-2 -right-2 w-6 h-6 rounded-full bg-white text-ink-secondary border border-slate-200 shadow-sm grid place-items-center hover:text-rose-600 hover:border-rose-200"><M n="close" c="text-sm" /></button>
            </span>
            );
          })}
        </div>
      )}
      {annotate && <PhotoAnnotator src={annotate.url} onSave={(d) => saveMarkup(annotate.id, d)} onClose={() => setAnnotate(null)} />}
    </Section>
  );
}

/* ---- Custom line (3): not in the catalog — optionally saved to it as a product or service ---- */
function CustomLineForm({ orderId, forItem, kitId, onFlash, onClose, onAdded }: { orderId: string; forItem: string; kitId?: string; onFlash: (m: string) => void; onClose: () => void; onAdded: () => void }) {
  const { can } = useMe();
  const canProduct = can("can_add_custom_product"), canService = can("can_add_custom_service");
  const [type, setType] = useState<"product" | "service">(canProduct ? "product" : "service");
  const [name, setName] = useState(""); const [qty, setQty] = useState(1);
  const [price, setPrice] = useState(""); const [cost, setCost] = useState("");
  const [save, setSave] = useState(false); const [sku, setSku] = useState(""); const [barcode, setBarcode] = useState("");
  const [busy, setBusy] = useState(false);
  const canSave = type === "product" ? can("can_create_products") : can("can_edit_labors");
  const canCost = can("can_set_cost_price");
  async function submit() {
    if (!name.trim()) { onFlash("Give the item a name"); return; }
    setBusy(true);
    try {
      const r = await api.post<any>(`/orders/${orderId}/lines`, {
        description: name.trim(), kind: "custom", line_type: type === "product" ? "part" : "labour",
        qty, unit_price: Number(price) || 0, ...(canCost ? { cost_price: Number(cost) || 0 } : {}),
        ...(forItem ? { order_item_id: forItem } : {}),
        // only catalog items can go inside a kit, so a one-off line stays an extra
        ...(kitId && save && canSave ? { bundle_group_id: kitId } : {}),
        ...(save && canSave ? { save_to_catalog: true, catalog_kind: type,
          ...(type === "product" ? { catalog_sku: sku.trim() || null, catalog_barcode: barcode.trim() || null } : {}) } : {}),
      });
      onFlash(save && canSave ? `Added and saved to the catalog${r?.margin_warning ? `. ${r.margin_warning}` : ""}` : (r?.margin_warning ? `Added. ${r.margin_warning}` : "Line added"));
      onAdded();
    } catch (e: any) { onFlash(e.message || "Could not add line"); setBusy(false); }
  }
  const lbl = "block text-[11px] font-semibold text-ink-tertiary mb-1";
  const inp = "glass-input w-full px-2 py-2 rounded-lg text-xs";
  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between">
        <div role="radiogroup" aria-label="Item type" className="inline-flex p-0.5 rounded-lg bg-slate-100 border border-slate-200">
          {canProduct && <button type="button" role="radio" aria-checked={type === "product"} onClick={() => setType("product")} className={`px-2.5 py-1 rounded-md text-xs font-semibold ${type === "product" ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary"}`}>Product</button>}
          {canService && <button type="button" role="radio" aria-checked={type === "service"} onClick={() => setType("service")} className={`px-2.5 py-1 rounded-md text-xs font-semibold ${type === "service" ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary"}`}>Service</button>}
        </div>
        <button type="button" aria-label="Close custom item" onClick={onClose} className="text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-sm" /></button>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex-1 min-w-[180px]"><label className={lbl}>Name</label><input autoFocus aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} placeholder={type === "product" ? "Ribbon cable 30 cm" : "Water damage clean"} className={inp} /></div>
        <div className="w-16"><label className={lbl}>Qty</label><input type="number" min={1} aria-label="Qty" value={qty} onChange={(e) => setQty(Math.max(1, Number(e.target.value) || 1))} className={inp} /></div>
        <div className="w-24"><label className={lbl}>Price</label><input type="number" min={0} aria-label="Price" value={price} onChange={(e) => setPrice(e.target.value)} className={`${inp} text-right`} /></div>
        {canCost && <div className="w-24"><label className={lbl}>Cost</label><input type="number" min={0} aria-label="Cost" value={cost} onChange={(e) => setCost(e.target.value)} className={`${inp} text-right`} /></div>}
      </div>
      {canSave && (
        <label className="flex items-center gap-2 text-xs text-ink-secondary cursor-pointer">
          <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />
          Save to catalog so it can be picked next time
        </label>
      )}
      {save && canSave && type === "product" && (
        <div className="flex flex-wrap gap-2">
          <div className="w-40"><label className={lbl}>SKU (optional)</label><input aria-label="SKU" value={sku} onChange={(e) => setSku(e.target.value)} className={`${inp} font-mono`} /></div>
          <div className="w-44"><label className={lbl}>Barcode (optional)</label><input aria-label="Barcode" value={barcode} onChange={(e) => setBarcode(e.target.value)} className={`${inp} font-mono`} /></div>
        </div>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className="px-3 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">Cancel</button>
        <button type="button" onClick={submit} disabled={busy || !name.trim()} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-50">{busy ? "Adding…" : save && canSave ? "Add and save" : "Add"}</button>
      </div>
    </div>
  );
}

/* ---- Services tab ---- */
// A line's kind in plain words: parts are products, labour is a service, a bundle is a kit.
const LINE_KIND: Record<string, string> = { part: "Product", product: "Product", labour: "Service", labor: "Service", service: "Service", custom: "Custom", bundle: "Kit", membership: "Plan" };
// A line inside its kit is stored as "Screen replacement labour (Screen Replacement Kit)". Under the
// kit's header the suffix only repeats the header, so the row shows it without. Display only —
// the description itself (API, prints, aria-labels, the "By technician" view) keeps the full name.
export function displayName(description: string, kitName?: string | null): string {
  if (!kitName) return description;
  const suffix = ` (${kitName})`;
  return description.endsWith(suffix) && description.length > suffix.length ? description.slice(0, -suffix.length) : description;
}
const lineKindLabel = (l: Line) => LINE_KIND[(l.kind || l.line_type || "").toLowerCase()] || humanize(l.kind || l.line_type) || "Item";
export function ServicesTab({ orderId, locationId, deviceId, lines, total, onFlash, onChanged, labor, onClockIn, isEstimate, emps, locked }: { orderId: string; locationId?: string; deviceId?: string; lines?: Line[]; total?: number; onFlash: (m: string) => void; onChanged?: () => void; labor?: any; onClockIn?: (lineId: string) => void; isEstimate?: boolean; emps?: { id: string; name: string }[]; locked?: boolean }) {
  const { can } = useMe();
  const seePrices = can("can_view_item_prices");   // off: the API leaves line prices out
  const [recsVersion, setRecsVersion] = useState(0);   // bumped when a deferred item becomes a draft
  const _runningLine = (lineId: string) => (labor?.entries || []).some((e: any) => e.running && e.order_line_id === lineId);
  // 144av: per-line actual clocked hours (from /labor by_line), employee-name lookup, and the
  // grouping toggle — flat (Orderry default) vs technician swimlanes (council: a lens, not home).
  const empName = (id?: string | null) => (emps || []).find((e) => e.id === id)?.name || "Unassigned";
  const empAvatarName = (id?: string | null) => (id ? (emps || []).find((e) => e.id === id)?.name : undefined);
  const _lineActualH = (lineId: string) => { const bl = (labor?.by_line || []).find((b: any) => b.order_line_id === lineId); return (bl?.total_seconds || 0) / 3600; };
  const [groupBy, setGroupBy] = useState(false);               // false = flat list (default)
  const [assignOpen, setAssignOpen] = useState<string | null>(null);  // line id whose picker is open
  const [qtyOpen, setQtyOpen] = useState<string | null>(null);   // the line whose quantity/discount editor is open
  const [splitRatio, setSplitRatio] = useState(0.5);                   // 144ax G8 — primary's share
  const [splitOpen, setSplitOpen] = useState(false);                   // split choices folded until asked for
  const [techQuery, setTechQuery] = useState("");                      // filter, shown for bigger teams
  async function patchLine(lineId: string, body: any) {
    try { await api.patch(`/orders/${orderId}/lines/${lineId}`, body); onChanged?.(); }
    catch (e: any) { onFlash(e.message || "Could not update line"); }
  }
  // Removing a line or a whole kit goes through the server, which returns stock for parts, re-spreads
  // a fixed kit price over what's left, and refuses lines already on an invoice. Same right as adding.
  const canRemove = (l: Line) => !locked && (l as any).kind !== "membership"
    && (kindOf(l).includes("serv") || kindOf(l).includes("lab") ? can("can_add_services") : can("can_add_products"));
  async function removeLine(l: Line) {
    if (!(await confirmDialog(`Remove "${l.description || "this line"}" from the job?${kindOf(l).includes("prod") || kindOf(l).includes("part") ? " Parts taken from stock go back." : ""}`,
      { title: "Remove line?", confirmLabel: "Remove", danger: true }))) return;
    try { await api.del(`/orders/${orderId}/lines/${l.id}`); onFlash("Line removed"); onChanged?.(); }
    catch (e: any) { onFlash(e.message || "Could not remove the line"); }
  }
  async function removeKit(name: string, kitLines: Line[]) {
    if (!(await confirmDialog(`Remove ${name} and its ${kitLines.length} ${kitLines.length === 1 ? "item" : "items"} from the job? Parts taken from stock go back.`,
      { title: "Remove kit?", confirmLabel: "Remove kit", danger: true }))) return;
    // One call: the whole kit goes or none of it does (never half a kit with its price re-spread).
    const gid = (kitLines[0] as any)?.bundle_group_id;
    try { await api.del(`/orders/${orderId}/kits/${gid}`); onFlash(`${name} removed`); }
    catch (e: any) { onFlash(e.message || `Couldn't remove ${name}. Try again.`); }
    finally { onChanged?.(); }
  }
  // Several items per job: which item a line is for ("" = whole job). Shown only when the shop has it on.
  const [jobItems, setJobItems] = useState<JobItem[]>([]);
  const [itemsOn, setItemsOn] = useState(false);
  const [forItem, setForItem] = useState("");
  useEffect(() => { api.get<any>(`/orders/${orderId}/items`).then((r) => { setItemsOn(!!r.enabled); setJobItems(r.items || []); }).catch(loadFailed("job items")); }, [orderId, lines]);
  const tagging = itemsOn && jobItems.length > 1;
  const [filter, setFilter] = useState("All");
  const [adding, setAdding] = useState(false);
  const [custom, setCustom] = useState(false);     // 3: a line that isn't in the catalog (yet)
  const [intoKit, setIntoKit] = useState<{ id: string; name: string } | null>(null);   // add the pick inside this kit
  const [stock, setStock] = useState<{ total_available: number; items: { location: string; location_id?: string; bin_id?: string | null; bin?: string | null; available: number }[] } | null>(null);
  const [takeBin, setTakeBin] = useState("");    // ii144c: which bin the product comes from ("" = unbinned first, then the fullest bin)
  const [binPick, setBinPick] = useState<CatalogItem | null>(null);   // a product waiting for its "Take from" choice
  // Picking from search adds the line straight away with quantity 1; quantity and discount are
  // changed on the line itself (LineQtyEditor). Stock shows in the search results before picking.
  // ii144c: a product kept in more than one bin at this location first asks which bin it comes from.
  async function pickItem(pick: CatalogItem) {
    if (adding) return;
    setBinPick(null); setStock(null); setTakeBin("");
    if (pick.kind === "product" && pick.ref_id && !isEstimate) {
      let s: typeof stock = null;
      try { s = await api.get<any>(`/inventory/parts/${pick.ref_id}/stock`); } catch { /* no stock detail: add it without a bin choice */ }
      if (s && needsBinChoice(s.items, locationId)) { setStock(s); setBinPick(pick); return; }
    }
    await addLine(pick);
  }
  async function addLine(pick: CatalogItem) {
    if (adding) return;
    setAdding(true);
    try {
      const r = await api.post<any>(`/orders/${orderId}/lines`, {
        description: pick.name, kind: pick.kind,
        line_type: pick.kind === "service" ? "labour" : "part",
        ref_id: pick.ref_id, qty: 1, unit_price: pick.price || 0,
        ...(tagging && forItem ? { order_item_id: forItem } : {}),
        ...(pick.kind === "product" && takeBin ? { bin_id: takeBin } : {}),
        ...(intoKit && pick.kind !== "bundle" ? { bundle_group_id: intoKit.id } : {}),
      });
      const out = pick.kind === "product" && pick.available != null && pick.available <= 0 ? ". It's out of stock: order or transfer it" : "";
      const inKitNow = intoKit && pick.kind !== "bundle";
      onFlash(r?.margin_warning ? `${pick.name} added. ${r.margin_warning}` : `${inKitNow ? `Added to ${intoKit!.name}` : `${pick.name} added`}${out}`);
      setIntoKit(null); setBinPick(null); setStock(null); setTakeBin("");
      onChanged?.();
    } catch (e: any) { onFlash(e.message || "Could not add line"); }
    finally { setAdding(false); }
  }

  const kindOf = (l: Line) => (l.kind || l.line_type || "item").toLowerCase();
  // Which filter tab a line belongs to. Lines carry kind (service/product/custom) and line_type
  // (labour/part): a custom labour line is a service. Kit items are ordinary lines tagged with a kit.
  const lineGroup = (l: Line): "Services" | "Products" => {
    const k = kindOf(l); const t = String((l as any).line_type || "").toLowerCase();
    return k.includes("prod") || k.includes("part") || (!k.includes("serv") && t === "part") ? "Products" : "Services";
  };
  const inKit = (l: Line) => !!(l as any).bundle_group_id || kindOf(l).includes("bundle");
  const matches = (l: Line, f: string) => f === "All" || (f === "Kits" ? inKit(l) : lineGroup(l) === f);
  const countFor = (f: string) => f === "Kits"   // kits, not the items inside them
    ? new Set((lines || []).filter(inKit).map((l) => (l as any).bundle_group_id || l.id)).size
    : (lines || []).filter((l) => matches(l, f)).length;
  const shown = (lines || []).filter((l) => matches(l, filter));
  // 144av #4 — flat by default; "By technician" groups into swimlanes (council: a lens, opt-in).
  const groups: Record<string, Line[]> = {};
  if (groupBy) shown.forEach((l) => { const k = l.technician_id || "Unassigned"; (groups[k] ||= []).push(l); });
  else groups[""] = shown;
  const dot = ["bg-slate-400", "bg-slate-400", "bg-slate-400", "bg-slate-400"];   // neutral: names tell technicians apart, not colours
  const [bookedEdit, setBookedEdit] = useState<string | null>(null);   // line whose booked-hours box is open
  // One column grid for every row (and the kit header) so all amounts line up:
  // icon | name + meta | actions | amount | remove slot (always reserved).
  const rowGrid = "grid grid-cols-[28px_minmax(0,1fr)_auto_88px_40px] items-center gap-x-3";
  const hrs = (h: number) => `${+h.toFixed(2)} h`;

  // 144av #1+#2+#3 — one line row; service/labour lines add assignee and booked-vs-actual.
  // `kit` is set when the row sits under its kit header: the name drops the "(Kit name)" suffix
  // there, and in a fixed-price kit the row amounts go quiet so the kit total reads as the price.
  const renderLine = (l: Line, kit?: { name: string; fixed: boolean }) => {
    const k = kindOf(l);
    const isServ = k.includes("serv") || k.includes("lab");
    const co = l.co_technicians || [];
    const actualH = _lineActualH(l.id);
    const booked = Number(l.booked_hours || 0);
    const eff = booked > 0 && actualH > 0 ? Math.round((booked / actualH) * 100) : null;
    const openPick = assignOpen === l.id;
    const full = l.description || "Item";
    const qtyN = Number(l.qty ?? 1);
    const onPlan = !!(l as any).membership_id;
    const discTxt = l.discount && !onPlan ? (l.discount_type === "pct" ? `−${l.discount}%` : `−${money(Number(l.discount))}`) : "";
    const amountTxt = [seePrices ? `${qtyN} × ${money(l.unit_price)}` : `Qty ${qtyN}`, seePrices ? discTxt : ""].filter(Boolean).join(" · ");
    const editable = !locked && (l as any).kind !== "membership" && !onPlan;
    const icon = isServ ? "build" : k.includes("bundle") ? "package_2" : "inventory_2";
    return (
      <div key={l.id} className={`group/row ${rowGrid} px-4 py-3 min-h-[56px] gap-y-1.5 hover:bg-slate-50/50 transition-colors`}>
        <div className="w-7 h-7 rounded-lg grid place-items-center bg-slate-100 text-ink-secondary"><M n={icon} c="text-[15px]" /></div>
        <div className="min-w-0">
          <h5 className="text-[13px] font-medium text-ink-primary truncate" title={full}>{displayName(full, kit?.name)}</h5>
          <p className="text-xs text-ink-secondary tabular-nums">{lineKindLabel(l)}{" · "}
            {editable ? <span onClick={() => setQtyOpen(l.id)} className="cursor-pointer hover:text-ink-primary">{amountTxt}</span> : amountTxt}
            {onPlan && (l as any).kind !== "membership" && <span className="ml-1.5 inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-primary/10 px-1.5 py-px text-[11px] font-semibold text-primary"><M n="workspace_premium" c="text-[11px]" />{Number(l.discount) >= 100 ? "Covered by plan" : `Plan ${l.discount}% off`}</span>}</p>
          {tagging && (
            <select value={l.order_item_id || ""} onChange={(e) => patchLine(l.id, { order_item_id: e.target.value })} aria-label={`Which item ${l.description || "this line"} is for`}
              className={`mt-1.5 max-w-[240px] ${houseInput}`}>
              <option value="">For: whole job</option>{jobItems.map((it) => <option key={it.id} value={it.id}>For: {itemName(it)}</option>)}
            </select>
          )}
        </div>
        <div className="flex items-center">
          {!isEstimate && onClockIn && isServ && (
            _runningLine(l.id)
              ? <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-700"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />Running</span>
              : <button type="button" onClick={() => onClockIn(l.id)} title={`Clock into ${l.description || "this line"}`}
                  className="h-8 px-2.5 rounded-lg border border-slate-200 text-xs font-medium text-ink-secondary hover:bg-slate-50 flex items-center gap-1 whitespace-nowrap"><M n="play_arrow" c="text-[13px]" />Clock in</button>
          )}
        </div>
        <div className="text-right">
          {/* The price is the big, obvious target for "change quantity or discount" (the grey 1 × €12 works too). */}
          {editable ? (
            <button type="button" data-qty-anchor={l.id} onClick={() => setQtyOpen(qtyOpen === l.id ? null : l.id)} aria-haspopup="dialog" aria-expanded={qtyOpen === l.id}
              aria-label={`Change quantity${can("can_set_discount") && !kit?.fixed ? " or discount" : ""} for ${full}`} title="Change quantity or discount"
              className={`group -my-1 -mr-2 inline-flex min-h-10 min-w-[72px] items-center justify-end gap-1.5 rounded-lg border px-2 py-1 transition-colors group-hover/row:border-slate-200 group-hover/row:bg-white hover:shadow-sm ${qtyOpen === l.id ? "border-primary/40 bg-white shadow-sm" : "border-transparent"}`}>
              <M n="edit" c="text-[13px] text-ink-tertiary opacity-0 transition-opacity group-hover/row:opacity-100" />
              <span>
                {seePrices && onPlan && Number(l.discount) > 0 && <span className="block tabular-nums text-xs text-ink-tertiary line-through">{money(qtyN * (l.unit_price ?? 0))}</span>}
                <span className={`block tabular-nums text-[13px] font-medium ${kit?.fixed ? "text-ink-tertiary" : "text-ink-primary"}`}>{seePrices ? money(l.total) : `Qty ${qtyN}`}</span>
              </span>
            </button>
          ) : seePrices && <>
            {/* A plan-covered line shows what the member would have paid, struck through. */}
            {onPlan && Number(l.discount) > 0 && <p className="tabular-nums text-xs text-ink-tertiary line-through">{money(qtyN * (l.unit_price ?? 0))}</p>}
            <p className={`tabular-nums text-[13px] font-medium ${kit?.fixed ? "text-ink-tertiary" : "text-ink-primary"}`}>{money(l.total)}</p>
          </>}
          {qtyOpen === l.id && (
            <LineQtyEditor lineId={l.id} label={full} qty={qtyN} discount={Number(l.discount || 0)}
              canDiscount={can("can_set_discount") && !kit?.fixed && (l.discount_type || "pct") === "pct"}
              onSave={(body) => patchLine(l.id, body)} onClose={() => setQtyOpen(null)} />
          )}
        </div>
        <div className="w-10 h-10">
          {canRemove(l) && (
            <button type="button" onClick={() => removeLine(l)} aria-label={`Remove ${l.description || "line"}`} title="Remove from job"
              className="w-10 h-10 grid place-items-center rounded-lg text-ink-tertiary hover:text-rose-600 hover:bg-rose-50"><M n="delete" c="text-base" /></button>
          )}
        </div>
        {/* 144av — assignee + booked/actual on service & labour lines only */}
        {isServ && (
          <div className="col-start-2 col-span-4 -mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-secondary">
            {/* #1 assignee chip + #3 any co-techs */}
            <div>
              <button type="button" data-assign-for={l.id} aria-haspopup="menu" aria-expanded={openPick} onClick={() => { setAssignOpen(openPick ? null : l.id); setSplitOpen(false); setTechQuery(""); }}
                className="-ml-1 flex items-center gap-1.5 h-8 pl-1 pr-1.5 rounded-lg hover:bg-slate-100">
                {co.length > 1 ? (
                  <span className="flex -space-x-1.5">{co.map((c) => <span key={c.employee_id} className="rounded-full ring-1 ring-white"><Avatar name={empAvatarName(c.employee_id)} size={18} /></span>)}</span>
                ) : (
                  <Avatar name={empAvatarName(l.technician_id)} size={18} />
                )}
                <span className="font-medium text-ink-secondary">{co.length > 1 ? `${co.length} techs · split` : empName(l.technician_id)}</span>
                <M n="expand_more" c="text-[13px] text-ink-tertiary" />
              </button>
              {openPick && (
                <FloatingMenu anchorSel={`[data-assign-for="${l.id}"]`} onClose={() => setAssignOpen(null)} width={264}>
                  {(() => {
                    const pickTech = (id: string | null) => { patchLine(l.id, { technician_id: id, co_technicians: [] }); setAssignOpen(null); };
                    const q = techQuery.trim().toLowerCase();
                    const list = (emps || []).filter((e) => !q || e.name.toLowerCase().includes(q));
                    const row = "w-full flex items-center gap-2.5 px-2.5 h-9 rounded-lg text-left text-[13px] hover:bg-slate-50 focus-visible:bg-slate-100 outline-none";
                    const check = <M n="check" c="ml-auto text-[16px] text-primary" />;
                    const first = (id?: string | null) => empName(id).split(" ")[0];
                    return (
                      <div className="text-[13px]">
                        <div className="px-2.5 pt-1 pb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Technician for this line</div>
                        {(emps || []).length > 6 && (
                          <input autoFocus value={techQuery} onChange={(e) => setTechQuery(e.target.value)} placeholder="Search technicians"
                            onKeyDown={(e) => { if (e.key === "Enter" && list[0]) pickTech(list[0].id); }}
                            className="mb-1 w-full h-8 px-2.5 rounded-lg border border-slate-200 bg-slate-50 text-[13px] outline-none focus:border-primary/50 focus:bg-white" />
                        )}
                        {!q && (
                          <button type="button" onClick={() => pickTech(null)} className={`${row} text-ink-secondary`}>
                            <span className="w-6 h-6 rounded-full border border-dashed border-slate-300 grid place-items-center flex-none"><M n="person_off" c="text-[14px] text-ink-tertiary" /></span>
                            Unassigned{!l.technician_id && check}
                          </button>
                        )}
                        {list.map((e) => (
                          <button type="button" key={e.id} onClick={() => pickTech(e.id)}
                            className={`${row} ${l.technician_id === e.id && co.length < 2 ? "font-semibold text-ink-primary" : "text-ink-primary"}`}>
                            <Avatar name={e.name} size={24} />
                            <span className="truncate">{e.name}</span>{l.technician_id === e.id && co.length < 2 && check}
                          </button>
                        ))}
                        {q && list.length === 0 && <p className="px-2.5 py-2 text-ink-tertiary">No technician called “{techQuery}”.</p>}
                        {/* #3/#8 split with a second tech at a chosen ratio — folded away until asked for */}
                        {l.technician_id && !q && (emps || []).length > 1 && (
                          <div className="mt-1 pt-1 border-t border-slate-100">
                            {co.length > 1 && (
                              <div className="flex items-center gap-2 px-2.5 h-9 text-ink-secondary">
                                <M n="group" c="text-[16px] text-ink-tertiary" />
                                <span className="truncate">{co.map((c) => `${first(c.employee_id)} ${Math.round((c.share || 0) * 100)}%`).join(" + ")}</span>
                                <button type="button" onClick={() => { patchLine(l.id, { co_technicians: [] }); setAssignOpen(null); }} className="ml-auto text-[12px] font-semibold text-rose-600 hover:underline">Clear</button>
                              </div>
                            )}
                            <button type="button" onClick={() => setSplitOpen((v) => !v)} aria-expanded={splitOpen} className={`${row} text-ink-secondary`}>
                              <M n="group_add" c="text-[18px] text-ink-tertiary" />{co.length > 1 ? "Change the split" : "Split the work"}
                              <M n={splitOpen ? "expand_less" : "expand_more"} c="ml-auto text-[16px] text-ink-tertiary" />
                            </button>
                            {splitOpen && (
                              <div className="pb-1">
                                <div className="flex items-center gap-1 px-2.5 py-1.5">
                                  <span className="text-[12px] text-ink-tertiary mr-1">{first(l.technician_id)} gets</span>
                                  {[0.5, 0.6, 0.7].map((r) => (
                                    <button type="button" key={r} onClick={() => setSplitRatio(r)}
                                      className={`text-[12px] font-semibold px-2 h-7 rounded-md ${splitRatio === r ? "bg-primary text-white" : "bg-slate-100 text-ink-secondary hover:bg-slate-200"}`}>{Math.round(r * 100)}%</button>
                                  ))}
                                </div>
                                {(emps || []).filter((e) => e.id !== l.technician_id).map((e) => (
                                  <button type="button" key={"sp" + e.id} onClick={() => { patchLine(l.id, { co_technicians: [{ employee_id: l.technician_id, share: splitRatio }, { employee_id: e.id, share: Math.round((1 - splitRatio) * 100) / 100 }] }); setAssignOpen(null); }}
                                    className={`${row} text-ink-primary`}>
                                    <Avatar name={e.name} size={24} />
                                    <span className="truncate">+ {e.name}</span><span className="ml-auto text-[12px] tabular-nums text-ink-tertiary">{Math.round((1 - splitRatio) * 100)}%</span>
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                </FloatingMenu>
              )}
            </div>
            {/* #2 booked vs actual — booked reads as text until clicked; the box opens on demand. */}
            {bookedEdit === l.id ? (
              <label className="flex items-center gap-1.5 text-xs text-ink-secondary">Booked
                <span className="glass-input h-8 w-20 rounded-xl flex items-center pr-2.5 focus-within:ring-2 focus-within:ring-primary/30">
                  <input type="number" min={0} step={0.25} defaultValue={booked || ""} placeholder="0" aria-label={`Booked hours for ${l.description || "this line"}`} autoFocus
                    onBlur={(e) => { const v = Number(e.target.value || 0); setBookedEdit(null); if (v !== booked) patchLine(l.id, { booked_hours: v }); }}
                    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    className="w-full min-w-0 bg-transparent pl-2.5 text-xs font-medium tabular-nums text-ink-primary outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none" />
                  <span className="text-xs text-ink-tertiary shrink-0">h</span>
                </span>
              </label>
            ) : booked > 0 ? (
              <button type="button" onClick={() => setBookedEdit(l.id)} aria-label={`Booked ${hrs(booked)} for ${l.description || "this line"}, change`}
                className="h-8 px-1.5 -mx-1.5 rounded-lg tabular-nums hover:bg-slate-100 hover:text-ink-primary">Booked {hrs(booked)}</button>
            ) : (
              <button type="button" onClick={() => setBookedEdit(l.id)} aria-label={`Set booked hours for ${l.description || "this line"}`}
                className="h-8 px-1.5 -mx-1.5 rounded-lg text-ink-secondary hover:bg-slate-100 hover:text-ink-primary">+ Booked hours</button>
            )}
            {actualH > 0 && <span className="tabular-nums">Actual {hrs(actualH)}</span>}
            {eff !== null && <span className={`tabular-nums text-[11px] font-semibold px-1.5 py-0.5 rounded-md ${eff >= 100 ? "text-emerald-700 bg-emerald-50" : "text-amber-700 bg-amber-50"}`}>{eff}%</span>}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-5">
      {!isEstimate && <PlanBanner orderId={orderId} refreshKey={lines?.length} onChanged={onChanged} />}
      <div className={`${sectionCard} flex flex-wrap items-center gap-3 p-3`}>
        <div role="group" aria-label="Show" className="inline-flex items-center gap-0.5 rounded-xl bg-slate-100 p-0.5">
          {["All", "Services", "Products", "Kits"].map((f) => (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}
              className={`h-7 px-2.5 rounded-[10px] text-xs font-medium transition ${filter === f ? "bg-white text-ink-primary shadow-sm ring-1 ring-slate-200/70" : "text-ink-secondary hover:text-ink-primary"}`}>{f}{(lines || []).length > 0 && <span className="ml-1 tabular-nums text-ink-tertiary">{countFor(f)}</span>}</button>
          ))}
        </div>
        {/* #4 swimlane toggle */}
        <button type="button" onClick={() => setGroupBy((v) => !v)} title="Group line items by technician"
          aria-pressed={groupBy} className={`ml-auto flex h-8 items-center gap-1.5 px-2.5 rounded-xl text-xs font-medium border ${groupBy ? "bg-slate-100 text-ink-primary border-slate-300" : "bg-white text-ink-secondary border-slate-200 hover:text-ink-primary"}`}>
          <M n="groups" c="text-[14px]" />By technician</button>
      </div>

      {/* Time on the job shows in the header timer and General › Time on this job; clock into a line below. */}
      {/* Add a line — margin guardrail (block/warn) applies on save */}
      <div className={`${sectionCard} p-4 space-y-3`}>
        <h4 className="text-sm font-semibold text-ink-primary flex items-center gap-2">{intoKit ? <>Add to kit: {intoKit.name}<button type="button" onClick={() => setIntoKit(null)} className="text-xs font-semibold text-primary hover:underline">Add as a separate item</button></> : "Add service or product"}</h4>
        {binPick && stock && !locked ? (
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[160px]"><label className="block text-xs font-medium text-ink-secondary mb-1">Item</label>
              <div className={`${houseInput} text-xs font-semibold flex items-center justify-between`}><span className="truncate">{binPick.name}</span><button type="button" aria-label="Cancel" onClick={() => { setBinPick(null); setStock(null); }} className="text-ink-tertiary hover:text-rose-600 ml-2"><M n="close" c="text-sm" /></button></div></div>
            <div className="min-w-[180px]"><label className="block text-xs font-medium text-ink-secondary mb-1">Take from</label>
              <select value={takeBin} onChange={(e) => setTakeBin(e.target.value)} aria-label="Which bin to take it from" className={`${houseInput} w-full text-xs font-semibold text-ink-primary`}>
                <option value="">Any (unbinned stock first)</option>{binChoices(stock.items, locationId).map((b) => <option key={b.bin_id} value={b.bin_id}>{b.label}</option>)}
              </select></div>
            <button type="button" onClick={() => addLine(binPick)} disabled={adding} className={btnPrimary}>{adding ? "Adding…" : "Add"}</button>
            <div className="w-full text-xs text-ink-secondary">In stock: <b className="text-emerald-600">{stock.total_available}</b> · {byLocation(stock.items).filter((i) => i.available > 0).map((i) => `${i.location} ${i.available}`).join(" · ") || "—"}</div>
          </div>
        ) : locked ? (
          <p className="text-xs text-ink-tertiary">This job is closed. Your role can't change a closed job.</p>
        ) : custom ? (
          <CustomLineForm orderId={orderId} forItem={tagging ? forItem : ""} kitId={intoKit?.id} onFlash={onFlash} onClose={() => setCustom(false)} onAdded={() => { setCustom(false); setIntoKit(null); onChanged?.(); }} />
        ) : (
          <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0"><CatalogPicker key={filter} deviceId={deviceId} onPick={pickItem}
              type={filter === "Services" ? "service" : filter === "Products" ? "product" : filter === "Kits" ? "bundle" : "all"}
              placeholder={filter === "Services" ? "Search services…" : filter === "Products" ? "Search products…" : filter === "Kits" ? "Search kits…" : "Search services, products and kits…"} /></div>
            {tagging && !intoKit && (
              <select value={forItem} onChange={(e) => setForItem(e.target.value)} aria-label="Which item new lines are for"
                className="glass-input max-w-[180px] px-2 py-2 rounded-lg text-xs font-semibold text-ink-primary">
                <option value="">For: whole job</option>{jobItems.map((it) => <option key={it.id} value={it.id}>For: {itemName(it)}</option>)}
              </select>
            )}
            {(can("can_add_custom_product") || can("can_add_custom_service")) && (
              <button type="button" onClick={() => setCustom(true)} className="h-9 px-2.5 rounded-xl text-xs font-semibold text-primary hover:bg-primary/5 flex items-center gap-1.5 whitespace-nowrap"><M n="add" c="text-sm" />Custom line</button>
            )}
          </div>
        )}
      </div>

      {!locked && <TogetherChips orderId={orderId} linesKey={(lines || []).map((l) => l.description).join("|")} onFlash={onFlash} onChanged={onChanged} />}
      {shown.length === 0 && <p className="text-xs text-ink-tertiary">{(lines || []).length === 0 ? "No line items yet. Add a service or product above."
        : filter === "Kits" ? "No kits on this job." : `No ${filter.toLowerCase()} on this job.`}</p>}
      {Object.entries(groups).map(([tech, ls], gi) => (
        <div key={tech || "all"} className={`${sectionCard} overflow-hidden`}>
          {groupBy && (
            <div className="bg-slate-50/90 px-4 py-2.5 border-b border-slate-200 flex items-center justify-between">
              <div className="flex items-center gap-2"><span className={`w-2 h-2 rounded-full ${dot[gi % dot.length]}`} /><span className="text-xs font-semibold text-ink-primary">{tech === "Unassigned" || !tech ? "Unassigned" : empName(tech)}</span><span className="text-[11px] text-ink-tertiary">({ls.length} line items)</span></div>
              {seePrices && <span className="tabular-nums text-xs font-semibold text-ink-primary">Subtotal: {money(ls.reduce((s, l) => s + (l.total || 0), 0))}</span>}
            </div>
          )}
          <div className="divide-y divide-slate-100">
            {groupBy ? ls.map((l) => renderLine(l)) : groupKitLines(ls).map((seg, si) => (
              seg.type === "line" ? renderLine(seg.line)
                : seg.type === "extras" ? <h4 key={`extras-${si}`} className="px-4 pt-4 pb-1.5 text-xs font-medium text-ink-secondary">Other items</h4>
                : <KitGroupHeader key={seg.groupId} orderId={orderId} groupId={seg.groupId} name={seg.name} qty={seg.qty} mode={seg.mode}
                    locked={seg.locked} total={seg.total} count={seg.lines.length} seePrices={seePrices} closed={locked}
                    onChanged={onChanged} onFlash={onFlash} onAddInto={(id) => setIntoKit({ id, name: seg.name })}
                    onRemove={seg.lines.every(canRemove) ? () => removeKit(seg.name, seg.lines) : undefined}>
                    {seg.lines.map((l) => renderLine(l, { name: seg.name, fixed: seg.locked != null }))}
                  </KitGroupHeader>
            ))}
          </div>
        </div>
      ))}

      <DeferredWork orderId={orderId} onFlash={onFlash} onChanged={() => { setRecsVersion((v) => v + 1); onChanged?.(); }} />
      <ServiceDue orderId={orderId} onFlash={onFlash} onChanged={() => { setRecsVersion((v) => v + 1); onChanged?.(); }} />
      <RecommendationsPanel key={recsVersion} orderId={orderId} items={tagging ? jobItems.map((it) => ({ id: it.id, name: itemName(it) })) : []} onFlash={onFlash} onChanged={onChanged} />

      <MarginNote orderId={orderId} refreshKey={total} />
      <div className={`${sectionCard} p-4 space-y-2`}>
        {/* Totals are for the whole job, whatever filter is on. No lines yet but a price on the job
            (agreed at check-in) reads as the quoted price, not a $0 subtotal next to a real total. */}
        {seePrices && (lines || []).length > 0 && <div className="flex justify-between text-xs text-ink-secondary"><span>Subtotal ({(lines || []).length} {(lines || []).length === 1 ? "line" : "lines"})</span><span className="tabular-nums font-semibold text-ink-primary">{money((lines || []).reduce((s, l) => s + (l.total || 0), 0))}</span></div>}
        <div className={`${(lines || []).length ? "pt-2 border-t border-slate-200 " : ""}flex justify-between items-baseline`}><span className="text-sm font-semibold text-ink-primary">{(lines || []).length ? "Total" : "Quoted price"}</span><span className="text-xl font-semibold tracking-tight tabular-nums text-ink-primary">{money(total)}</span></div>
        {!(lines || []).length && !!total && <div className="text-[11px] text-ink-tertiary">Agreed when the job came in. Add services and parts to build the invoice.</div>}
      </div>
    </div>
  );
}

/* ---- Checklists tab ---- */
function ChecklistsTab({ orderId, typeId }: { orderId: string; typeId?: string }) {
  const [cls, setCls] = useState<any[] | null>(null);
  const [templates, setTemplates] = useState<any[]>([]);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.get<any>(`/orders/${orderId}/checklists`).then(async (r) => {
    const heads = r.items || r || [];
    // list endpoint returns a summary only; pull each checklist's items for the cards
    const full = await Promise.all(heads.map((h: any) =>
      api.get<any>(`/orders/${orderId}/checklists/${h.id}`).then((d) => ({ ...h, ...d })).catch(() => h)));
    setCls(full);
  }).catch(() => setCls([]));
  useEffect(() => { load(); }, [orderId]);
  useEffect(() => {
    const q = typeId ? `?type_id=${typeId}` : "";
    api.get<any>(`/checklist-templates${q}`).then((r) => setTemplates(r.items || [])).catch(() => setTemplates([]));
  }, [typeId]);

  async function apply() {
    if (!pick) return; setBusy(true);
    try { await api.post(`/orders/${orderId}/checklists`, { template_id: pick }); setPick(""); await load(); }
    catch (e: any) { await alertDialog(e.message || "Could not apply checklist"); }
    finally { setBusy(false); }
  }
  async function fill(cid: string, iid: string, value: any) {
    try { await api.patch(`/orders/${orderId}/checklists/${cid}/items/${iid}`, { value }); await load(); }
    catch (e: any) { await alertDialog(e.message || "Could not save"); }
  }
  async function attach(cid: string, item: any, files: FileList | null) {
    if (!files?.length) return;
    try {
      const ids: string[] = [...(item.file_ids || [])];
      for (const f of Array.from(files)) {
        const data = await readAsDataUrl(f);
        const r = await api.post<any>("/media", { data, name: f.name, mime: f.type });
        ids.push(r.id);
      }
      await api.patch(`/orders/${orderId}/checklists/${cid}/items/${item.id}`, { file_ids: ids });
      await load();
    } catch (e: any) { await alertDialog(e.message || "Upload failed (file may be too large)"); }
  }
  async function detach(cid: string, item: any, id: string) {
    try { await api.patch(`/orders/${orderId}/checklists/${cid}/items/${item.id}`, { file_ids: (item.file_ids || []).filter((x: string) => x !== id) }); await load(); }
    catch { /* ignore */ }
  }
  async function removeCl(cid: string) {
    if (!(await confirmDialog("Remove this checklist from the job? Ticks on it are lost.", { title: "Remove checklist?", danger: true, confirmLabel: "Remove checklist" }))) return;
    try { await api.del(`/orders/${orderId}/checklists/${cid}`); await load(); } catch { /* ignore */ }
  }

  // Result choices as a soft-tint segmented control: the chosen value takes its own tint.
  const CHECK = [["pass", "Pass", "bg-emerald-50 text-emerald-700 ring-emerald-200"], ["fail", "Fail", "bg-rose-50 text-rose-700 ring-rose-200"], ["caution", "Caution", "bg-amber-50 text-amber-700 ring-amber-200"], ["unknown", "Unknown", "bg-slate-100 text-ink-secondary ring-slate-200"]] as const;
  const segCls = (on: boolean, tone: string) => `px-2.5 py-1 rounded-md text-[11px] font-semibold transition-colors ${on ? `${tone} ring-1 ring-inset shadow-2xs` : "text-ink-secondary hover:text-ink-primary"}`;

  return (
    <div className="space-y-5">
      {/* Apply a template */}
      <Section title="Apply a checklist" icon="fact_check">
        {templates.length ? (
          <div className="flex flex-wrap items-center gap-2">
            <select value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Checklist template" className={`${houseInput} flex-1 min-w-[200px]`}>
              <option value="">Choose a template…</option>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <button onClick={apply} disabled={!pick || busy} className={btnPrimary}>Apply</button>
          </div>
        ) : (
          <p className="text-xs text-ink-tertiary">
            No checklist templates yet. <Link to="/settings?section=checklists" className="font-semibold text-primary hover:underline">Create one in Settings › Checklists</Link>
          </p>
        )}
      </Section>

      {cls === null && <p className="text-xs text-ink-tertiary">Loading checklists…</p>}
      {cls?.length === 0 && <p className="text-xs text-ink-tertiary">No checklists on this job yet.</p>}
      {cls?.map((c: any) => {
        const items = c.items || []; const done = items.filter((i: any) => i.done).length; const pct = items.length ? Math.round((done / items.length) * 100) : 0;
        return (
          <div key={c.id} className={`${sectionCard} p-4 space-y-3.5`}>
            <div className="flex items-center justify-between border-b border-slate-100 pb-2">
              <div className="flex items-center gap-2.5 min-w-0"><ProgressRing pct={pct} size={28} label={false} /><div className="min-w-0"><h4 className="text-sm font-semibold text-ink-primary truncate">{c.title || c.name || "Checklist"}</h4><p className="text-xs text-ink-tertiary tabular-nums">{done} of {items.length} done</p></div></div>
              <div className="flex items-center gap-2"><span className={`text-xs font-semibold tabular-nums ${pct === 100 ? "text-emerald-700" : "text-ink-tertiary"}`}>{pct}%</span><button aria-label="Remove" onClick={() => removeCl(c.id)} title="Remove" className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:text-rose-600 hover:bg-rose-50"><M n="delete" c="text-sm" /></button></div>
            </div>
            <div className="space-y-2 text-xs font-medium text-ink-primary">
              {items.map((i: any) => {
                const v = i.value;
                return (
                  <div key={i.id} className={`p-2.5 rounded-lg border ${i.done ? "bg-emerald-50/60 border-emerald-100" : "bg-slate-50 border-slate-200"}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5">{i.label}{i.required && <span className="text-rose-500">*</span>}</span>
                      {i.field_kind === "check_result" && (
                        <div role="group" aria-label={`${i.label} result`} className="inline-flex gap-0.5 p-0.5 rounded-lg bg-white border border-slate-200 flex-shrink-0">{CHECK.map(([val, lbl, tone]) => (
                          <button key={val} aria-pressed={String(v) === val} onClick={() => fill(c.id, i.id, val)} className={segCls(String(v) === val, tone)}>{lbl}</button>
                        ))}</div>
                      )}
                      {i.field_kind === "yes_no" && (
                        <div role="group" aria-label={`${i.label} answer`} className="inline-flex gap-0.5 p-0.5 rounded-lg bg-white border border-slate-200 flex-shrink-0">{[["yes", "Yes"], ["no", "No"]].map(([val, lbl]) => (
                          <button key={val} aria-pressed={String(v) === val} onClick={() => fill(c.id, i.id, val)} className={segCls(String(v) === val, "bg-primary/10 text-primary ring-primary/30")}>{lbl}</button>
                        ))}</div>
                      )}
                    </div>
                    {i.field_kind === "date" && (
                      <DateField className="mt-2 w-48" value={String(v ?? "")} onChange={(d) => d !== (v ?? "") && fill(c.id, i.id, d)} label={i.label || "Date"} />
                    )}
                    {["text", "textarea", "number"].includes(i.field_kind) && (
                      <input type={i.field_kind === "number" ? "number" : "text"} defaultValue={v ?? ""} onBlur={(e) => e.target.value !== (v ?? "") && fill(c.id, i.id, e.target.value)}
                        aria-label={i.label} className={`${houseInput} w-full mt-2`} placeholder="Enter value…" />
                    )}
                    {/* Templates save "dropdown" (pick one) and "multiselect" (pick several); older ones "select". */}
                    {(i.field_kind === "select" || i.field_kind === "dropdown") && (
                      <select aria-label={i.label} value={v ?? ""} onChange={(e) => fill(c.id, i.id, e.target.value)} className={`${houseInput} w-full mt-2`}>
                        <option value="">Choose…</option>{(i.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    )}
                    {i.field_kind === "multiselect" && (
                      <div role="group" aria-label={`${i.label} options`} className="flex flex-wrap gap-1.5 mt-2">
                        {(i.options || []).map((o: string) => {
                          const picked: string[] = Array.isArray(v) ? v : [];
                          const on = picked.includes(o);
                          return (
                            <button key={o} type="button" aria-pressed={on}
                              onClick={() => fill(c.id, i.id, on ? picked.filter((x) => x !== o) : [...picked, o])}
                              className={`inline-flex items-center gap-1 whitespace-nowrap px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${on ? "bg-primary/10 text-primary border-primary/30" : "bg-white text-ink-secondary border-slate-200 hover:text-ink-primary"}`}>
                              {on && <M n="check" c="text-xs" />}{o}
                            </button>
                          );
                        })}
                      </div>
                    )}
                    {/* Attachments — photos / files / videos (like Orderry) */}
                    <div className="flex flex-wrap items-center gap-3 mt-2 pt-1">
                      {(i.file_ids || []).map((fid: string) => (
                        <span key={fid} className="relative group">
                          <AuthedImage src={`/api/media/${fid}`} alt="attachment" onClick={() => openAuthedDoc(`/api/media/${fid}`)} className="w-12 h-12 rounded-lg object-cover border border-slate-200 bg-slate-100 cursor-pointer" />
                          <button onClick={() => detach(c.id, i, fid)} aria-label="Remove attachment" title="Remove" className="absolute -top-2 -right-2 w-6 h-6 rounded-full bg-white text-ink-secondary border border-slate-200 shadow-sm grid place-items-center hover:text-rose-600 hover:border-rose-200"><M n="close" c="text-sm" /></button>
                        </span>
                      ))}
                      <label className="flex items-center gap-1 text-[11px] font-semibold text-primary cursor-pointer px-2 py-1 rounded-lg border border-dashed border-slate-300 hover:bg-slate-50">
                        <M n="attach_file" c="text-[13px]" />Attach
                        <input type="file" multiple accept="image/*,video/*,application/pdf" className="hidden" onChange={(e) => { attach(c.id, i, e.target.files); e.currentTarget.value = ""; }} />
                      </label>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ---- Invoices tab ---- */
// Plain words for the payment-link state and for each payment's kind and method.
const LINK_STATUS: Record<string, string> = {
  created: "ready", open: "waiting for payment", pending: "waiting for payment", requires_payment_method: "waiting for payment",
  processing: "processing", paid: "paid", succeeded: "paid", expired: "expired", revoked: "revoked", canceled: "cancelled", cancelled: "cancelled",
};
const linkStatusLabel = (st?: string) => (st ? LINK_STATUS[st] || humanize(st).toLowerCase() : "ready");
const PAY_KIND: Record<string, string> = { payment: "Payment", pay: "Payment", prepayment: "Prepayment", prepay: "Prepayment", deposit: "Deposit", refund: "Refund" };
const PAY_METHOD: Record<string, string> = { cash: "cash", cashless: "cashless / UPI", upi: "UPI", card: "card", bank: "bank transfer", bank_transfer: "bank transfer", transfer: "bank transfer", gift_card: "gift card", giftcard: "gift card", loyalty: "loyalty points", coupon: "coupon", online: "online", link: "payment link" };
const payLabel = (kind?: string, method?: string) => {
  const k = (kind && (PAY_KIND[kind] || humanize(kind))) || "Payment";
  const m = method ? PAY_METHOD[method] || humanize(method).toLowerCase() : "";
  return m ? `${k} · ${m}` : k;
};
type PayCtx = { order_total: number; already_paid: number; prepaid: number; remaining: number; location_id?: string | null; accounts_by_method: Record<string, PayAccount[]> };
// Every account from the payment context in one list, each tagged with the method it sits under.
const ctxAccounts = (c: PayCtx | null): PayAccount[] =>
  c ? Object.entries(c.accounts_by_method || {}).flatMap(([k, list]) => (list || []).map((a) => ({ ...a, payment_option: a.payment_option || k }))) : [];
export function InvoicesTab({ orderId, total, writtenOff = 0, deposit, currencyCode, fxRate, currencySymbol, onCurrencyChange, onFlash, onChanged }: { orderId: string; total?: number; writtenOff?: number; deposit?: number; currencyCode?: string; fxRate?: number; currencySymbol?: string; onCurrencyChange?: (code: string, rate: number, symbol: string) => void; onFlash: Flash; onChanged?: () => void }) {
  const { base, opts: curOpts, multi: multiCurrency } = useCurrencies();
  const { can } = useMe();
  const changeCurrency = async (code: string) => {
    try {
      const r = await api.patch<any>(`/orders/${orderId}/currency`, { currency_code: code || (base?.code ?? "") });
      onCurrencyChange?.(r.currency_code, r.fx_rate, r.symbol);
      onFlash(`Document currency: ${r.currency_code}`);
    } catch (e: any) { onFlash(e.message || "That didn't go through. Try again."); }
  };
  // fxRate is display-per-base (order snapshot); converted display = base amount × fxRate.
  const isForeign = !!(currencyCode && base && currencyCode !== base.code && fxRate);
  const conv = (amt: number) => (isForeign && fxRate ? amt * fxRate : amt);
  const [data, setData] = useState<{ invoices: any[]; payments: any[] } | null>(null);
  const [ctx, setCtx] = useState<PayCtx | null>(null);
  const [modal, setModal] = useState<null | "pay" | "prepay">(null);
  const [amount, setAmount] = useState(""); const [account, setAccount] = useState(""); const [method, setMethod] = useState("cash");
  const load = () => api.get<any>(`/cash/orders/${orderId}/invoices`).then(setData).catch(() => setData({ invoices: [], payments: [] }));
  useEffect(() => { load(); }, [orderId]);
  // I6 — email or text the invoice to the customer; show what really happened.
  const [sendingInv, setSendingInv] = useState<string | null>(null);
  async function sendInv(id: string, way?: SendWay) {
    setSendingInv(id);
    try { const r = await sendInvoice(id, way); onFlash(r.text, r.tone); load(); } catch (e: any) { onFlash(e.message || "Couldn't send the invoice", "warn"); }
    finally { setSendingInv(null); }
  }
  // Rewards & credits — redeem loyalty points / coupon / gift card against this order.
  const [rwPts, setRwPts] = useState(""); const [rwCoupon, setRwCoupon] = useState(""); const [rwGift, setRwGift] = useState(""); const [rwBusy, setRwBusy] = useState(false);
  async function redeem(kind: "loyalty" | "coupon" | "gift") {
    setRwBusy(true);
    try {
      if (kind === "loyalty") { if (!rwPts) return; await api.post(`/orders/${orderId}/redeem-loyalty`, { points: Number(rwPts) }); setRwPts(""); onFlash("Points redeemed"); }
      else if (kind === "coupon") { if (!rwCoupon.trim()) return; await api.post(`/orders/${orderId}/apply-coupon`, { code: rwCoupon.trim() }); setRwCoupon(""); onFlash("Coupon applied"); }
      else { if (!rwGift.trim()) return; await api.post(`/orders/${orderId}/redeem-giftcard`, { code: rwGift.trim() }); setRwGift(""); onFlash("Gift card redeemed"); }
      load();
    } catch (e: any) { onFlash(e.message || "Redeem failed"); } finally { setRwBusy(false); }
  }

  // Requested deposit — PATCH /orders/{id}/deposit (amount OR % of total). The customer pays
  // it through the normal pay-link / portal; this just records what to ask for.
  const [dep, setDep] = useState(deposit || 0);
  const [depInput, setDepInput] = useState("");
  const [depBusy, setDepBusy] = useState(false);
  async function requestDeposit(body: { percent?: number; amount?: number }) {
    setDepBusy(true);
    try { const r = await api.patch<{ deposit_amount: number }>(`/orders/${orderId}/deposit`, body); setDep(r.deposit_amount); setDepInput(""); onFlash(r.deposit_amount > 0 ? `Deposit set: ${money(r.deposit_amount)}` : "Deposit cleared"); }
    catch (e: any) { onFlash(e.message || "Could not set deposit"); } finally { setDepBusy(false); }
  }

  // A2 pay-link
  const [link, setLink] = useState<null | { intent_id: string; url: string; status?: string }>(null);
  const createLink = async (regen = false) => {
    try {
      const r = await api.post<any>(`/orders/${orderId}/payment-link${regen ? "?regenerate=true" : ""}`);
      setLink({ intent_id: r.intent_id, url: r.url, status: r.status });
      onFlash(regen ? "New link made. The old one no longer works." : "Payment link ready");
    } catch (e: any) { onFlash(`Cannot create link: ${e.message}`); }
  };
  const copyLink = () => { if (link) { navigator.clipboard?.writeText(link.url).then(() => onFlash("Link copied")).catch(saveFailed("copy the link")); } };
  const revokeLink = async () => {
    if (!link) return;
    try { await api.post(`/payments/intents/${link.intent_id}/revoke`, {}); setLink(null); onFlash("Link revoked"); }
    catch (e: any) { onFlash(e.message || "That didn't go through. Try again."); }
  };

  const openPay = async (kind: "pay" | "prepay") => {
    try { const c = await api.get<PayCtx>(`/cash/orders/${orderId}/payment-context`); setCtx(c);
      setAccount(pickDefaultAccount(accountsFor(ctxAccounts(c), method as PaymentOption), c.location_id)); setAmount(String(kind === "pay" ? c.remaining : "")); setModal(kind);
    } catch (e: any) { onFlash(`Cannot open payment: ${e.message}`); }
  };
  // Busy guard: the ref blocks a second click landing before React re-renders the disabled button,
  // so a double-click can never post the same payment twice.
  const [payBusy, setPayBusy] = useState(false); const payBusyRef = useRef(false);
  const [onReader, setOnReader] = useState(false);   // F2/F3: card taken on a reader instead of recorded by hand
  const submit = async () => {
    if (payBusyRef.current) return;
    payBusyRef.current = true; setPayBusy(true);
    const amt = parseFloat(amount);
    try {
      if (modal === "prepay") await api.post(`/cash/orders/${orderId}/prepay`, { amount: amt, account_id: account });
      else await api.post(`/cash/orders/${orderId}/pay`, { amount: amt, account_id: account, method });
      setModal(null); onFlash("Payment recorded"); load();
    } catch (e: any) { onFlash(e.message || "That didn't go through. Try again."); } finally { payBusyRef.current = false; setPayBusy(false); }
  };

  // Net of refunds, like the server's balance (services/order_money), so a refunded job isn't "Settled".
  const paid = (data?.payments || []).reduce((s, p) => s + (p.kind === "refund" ? -Math.abs(p.amount || 0) : (p.amount || 0)), 0);
  // A dropped job's unpaid part was written off (orders.written_off), so it isn't owed any more.
  const t = total ?? 0; const due = Math.max(t - paid - (writtenOff || 0), 0);
  const settled = t > 0 && due <= 0;   // fully paid: no payment or deposit actions to offer
  const allAccts = ctxAccounts(ctx);
  // A cashbox made from the dialog's inline "Set up a cashbox" form joins its method's list.
  const addAccount = (a: PayAccount) => setCtx((c) => c && ({ ...c, accounts_by_method: { ...c.accounts_by_method, [a.payment_option || method]: [...(c.accounts_by_method[a.payment_option || method] || []), a] } }));

  return (
    <div className="space-y-5">
      {/* Balance hero (design council, option C): the figure stays ink and large; the state shows in a
          thin top edge, a pill with words, and a bar of how much is collected — never in the digits. */}
      {(() => {
        const state = t <= 0 ? "empty" : !settled ? "due" : writtenOff > 0 ? "written_off" : "settled";
        const tone = {
          due: { edge: "bg-amber-400", pill: "bg-amber-50 text-amber-800 ring-amber-200", dot: "bg-amber-500", label: "Balance due" },
          settled: { edge: "bg-emerald-500", pill: "bg-emerald-50 text-emerald-700 ring-emerald-200", dot: "bg-emerald-500", label: "Settled" },
          written_off: { edge: "bg-slate-300", pill: "bg-slate-100 text-ink-secondary ring-slate-200", dot: "bg-slate-400", label: "Written off" },
          empty: { edge: "bg-slate-200", pill: "bg-slate-100 text-ink-secondary ring-slate-200", dot: "bg-slate-400", label: "Nothing to pay yet" },
        }[state];
        const shown = money(due);
        const m = /^([^\d-]+)(.*)$/.exec(shown);   // "€9,000" -> "€" + "9,000"; leave suffix-style currencies whole
        const pct = (n: number) => (t > 0 ? Math.min(100, Math.max(0, (n / t) * 100)) : 0);
        return (
          <section aria-label="Balance" className={`${sectionCard} relative overflow-hidden p-6`}>
            <div aria-hidden="true" className={`absolute inset-x-0 top-0 h-0.5 ${tone.edge}`} />
            <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-ink-tertiary">Still to pay</span>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-2">
              <h2 className={`flex items-start tabular-nums ${state === "due" ? "text-ink-primary" : "text-ink-tertiary"}`} aria-label={`Still to pay ${shown}`}>
                {m ? <><span className="mt-1.5 mr-0.5 text-xl font-medium text-ink-tertiary">{m[1].trim()}</span><span className="text-[44px] leading-none font-medium tracking-[-0.035em]">{m[2]}</span></>
                  : <span className="text-[44px] leading-none font-medium tracking-[-0.035em]">{shown}</span>}
              </h2>
              <span className={`inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full text-xs font-semibold ring-1 ring-inset ${tone.pill}`}>
                <span aria-hidden="true" className={`size-1.5 rounded-full ${tone.dot}`} />{tone.label}
              </span>
            </div>
            {t > 0 && (
              <div className="mt-4 h-1.5 rounded-full bg-slate-100 overflow-hidden flex" role="img"
                aria-label={`${money(paid)} of ${money(t)} collected${writtenOff > 0 ? `, ${money(writtenOff)} written off` : ""}`}>
                <div className="h-full bg-emerald-500" style={{ width: `${pct(paid)}%` }} />
                {writtenOff > 0 && <div className="h-full bg-slate-300" style={{ width: `${pct(writtenOff)}%` }} />}
              </div>
            )}
            <p className="mt-2 text-xs text-ink-tertiary tabular-nums">
              <span className="font-medium text-ink-secondary">{money(paid)}</span> of <span className="font-medium text-ink-secondary">{money(t)}</span> collected
              {writtenOff > 0 && <> · <span className="font-medium text-ink-secondary">{money(writtenOff)}</span> written off</>}
            </p>
        {isForeign && (
          <p className="text-xs text-ink-secondary mt-1 tabular-nums">≈ {currencySymbol}{conv(due).toFixed(2)} {currencyCode} <span className="text-ink-tertiary">(1 {currencyCode} = {fxRate && (1 / fxRate).toFixed(4)} {base?.code})</span></p>
        )}
        {multiCurrency && (
          <div className="mt-3 flex items-center gap-2">
            <label htmlFor={`wo-currency-${orderId}`} className="text-xs font-medium text-ink-secondary">Currency</label>
            <select id={`wo-currency-${orderId}`} value={currencyCode || base?.code || ""} onChange={(e) => changeCurrency(e.target.value)} className={houseInput}>
              {curOpts.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
            </select>
          </div>
        )}
        {((!settled && t > 0) || link) && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {!settled && (can("can_take_payments") || can("can_view_finance")) && <button onClick={() => openPay("pay")} className={btnPrimary}><M n="credit_card" c="text-sm" />Take payment</button>}
            {!settled && (can("can_take_payments") || can("can_view_finance")) && <button onClick={() => openPay("prepay")} className={btnSecondary}><M n="savings" c="text-sm" />Take prepayment</button>}
            {!link ? !settled && can("can_create_payment_links") && (
              <button onClick={() => createLink(false)} className={btnSecondary}><M n="link" c="text-sm" />Payment link</button>
            ) : (
              <div className="flex flex-wrap items-center gap-1 pl-2.5 pr-1 h-9 rounded-xl border border-slate-200 bg-slate-50 text-xs">
                <span className="font-semibold text-ink-secondary mr-1">Payment link · {linkStatusLabel(link.status)}</span>
                <button onClick={copyLink} title="Copy the payment link" className="h-7 px-2 rounded-lg font-semibold text-ink-primary hover:bg-white">Copy</button>
                <button onClick={() => createLink(true)} title="Make a new link (the old one stops working)" className="h-7 px-2 rounded-lg font-semibold text-ink-primary hover:bg-white">New link</button>
                <button onClick={revokeLink} title="Stop this link working" className="h-7 px-2 rounded-lg font-semibold text-rose-600 hover:bg-white">Revoke</button>
              </div>
            )}
          </div>
        )}
          </section>
        );
      })()}

      {!settled && <Section title="Requested deposit" meta={dep > 0 ? <span className="font-semibold text-emerald-700">{money(conv(dep))} requested</span> : null}
        description={`The customer pays this through the payment link or portal.${total ? ` Total ${money(conv(total))}.` : ""}`}>
        <div className="flex flex-wrap items-center gap-2">
          <input value={depInput} onChange={(e) => setDepInput(e.target.value)} inputMode="decimal" placeholder="Amount" aria-label="Deposit amount"
            className={`${houseInput} w-28 tabular-nums`} />
          <button type="button" disabled={depBusy || !depInput || isNaN(Number(depInput))} onClick={() => requestDeposit({ amount: Number(depInput) })} className={btnPrimary}>Set</button>
          {[25, 50, 100].map((pct) => (
            <button key={pct} type="button" disabled={depBusy} onClick={() => requestDeposit({ percent: pct })} className={btnSecondary}>{pct}%</button>
          ))}
          {dep > 0 && <button type="button" disabled={depBusy} onClick={() => requestDeposit({ amount: 0 })} className="h-9 px-3 rounded-xl text-xs font-semibold text-rose-600 hover:bg-rose-50 disabled:opacity-50">Clear</button>}
        </div>
      </Section>}

      {due > 0 && (
        <Section title="Rewards & credits">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
            <div className="flex gap-1.5"><input value={rwPts} onChange={(e) => setRwPts(e.target.value)} type="number" placeholder="Points" aria-label="Loyalty points" className={`${houseInput} flex-1 min-w-0 tabular-nums`} /><button disabled={rwBusy} onClick={() => redeem("loyalty")} className={btnSecondary}>Redeem</button></div>
            <div className="flex gap-1.5"><input value={rwCoupon} onChange={(e) => setRwCoupon(e.target.value)} placeholder="Coupon code" aria-label="Coupon code" className={`${houseInput} flex-1 min-w-0 font-mono`} /><button disabled={rwBusy} onClick={() => redeem("coupon")} className={btnSecondary}>Apply</button></div>
            <div className="flex gap-1.5"><input value={rwGift} onChange={(e) => setRwGift(e.target.value)} placeholder="Gift card" aria-label="Gift card" className={`${houseInput} flex-1 min-w-0 tabular-nums`} /><button disabled={rwBusy} onClick={() => redeem("gift")} className={btnSecondary}>Redeem</button></div>
          </div>
        </Section>
      )}

      {data && data.invoices.length > 0 && (
        <Section flush title="Invoices" meta={data.invoices.length}>
          <table className="w-full text-xs text-left">
            <thead className="bg-slate-50 text-xs text-ink-tertiary font-medium"><tr><th className="py-2.5 px-4 font-medium">Invoice</th><th className="py-2.5 px-4 font-medium">Status</th><th className="py-2.5 px-4 font-medium text-right">Amount</th><th className="py-2.5 px-4"><span className="sr-only">Send</span></th></tr></thead>
            <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
              {data.invoices.map((i) => (
                <tr key={i.id} className="hover:bg-slate-50/50"><td className="py-2.5 px-4 font-mono font-semibold text-primary">{i.invoice_number}</td>
                  <td className="py-2.5 px-4"><span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${/(paid|settled)/i.test(i.status) ? "bg-emerald-100 text-emerald-800" : "bg-blue-100 text-blue-800"}`}>{humanize(i.status)}</span></td>
                  <td className="py-2.5 px-4 text-right tabular-nums font-semibold text-ink-primary">{money(i.total)}</td>
                  <td className="py-2 px-4 text-right">{!/(cancel|void)/i.test(i.status || "") && <SendMenu small primary={false} label="Send invoice" busy={sendingInv === i.id} onSend={(w) => sendInv(i.id, w)} />}</td></tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {data && data.payments.length > 0 && (
        <Section flush title="Payments" meta={`${money(paid)} collected`}>
          <div className="p-3 space-y-2">
            {data.payments.map((p) => (
              <div key={p.id} className="flex items-center justify-between p-2.5 rounded-xl bg-slate-50 border border-slate-200 text-xs">
                <div className="flex items-center gap-2.5"><M n={p.kind === "refund" ? "undo" : p.method === "cash" ? "payments" : "contactless"} c={`text-sm ${p.kind === "refund" ? "text-rose-600" : "text-emerald-600"}`} /><div><p className="font-semibold text-ink-primary">{payLabel(p.kind, p.method)}</p></div></div>
                <span className={`tabular-nums font-semibold ${p.kind === "refund" ? "text-rose-600" : "text-ink-primary"}`}>{p.kind === "refund" ? "−" : ""}{money(p.amount)}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      <CardPaymentsList orderId={orderId} reloadKey={data?.payments.length} onChanged={load} />

      {data && data.invoices.length === 0 && data.payments.length === 0 && <p className="text-xs text-ink-tertiary">No invoices or payments yet.</p>}

      {/* Portaled to <body>: inside the drawer the header (z-20) painted over the scrim as a white strip. */}
      {modal && ctx && createPortal(
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-ink-primary/40" aria-hidden="true" onClick={() => setModal(null)} />
          <div className="relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-md p-5 space-y-4">
            <div className="flex items-center justify-between"><h4 className="text-base font-semibold text-ink-primary">{modal === "prepay" ? "Take prepayment" : "Take payment"}</h4><button aria-label="Close" onClick={() => setModal(null)} className="text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-lg" /></button></div>
            {modal === "pay" && <CheckoutOffer orderId={orderId} onJoined={() => { onChanged?.(); openPay("pay"); onFlash("Plan added to this job"); }} />}
            <div className="text-xs text-ink-secondary">Remaining balance: <b className="tabular-nums">{money(ctx.remaining)}</b></div>
            <div><label className="block text-xs font-medium text-ink-secondary mb-1">Amount <span className="text-ink-tertiary font-normal normal-case">({base?.code || "base"})</span></label><input value={amount} onChange={(e) => setAmount(e.target.value)} type="number" className="glass-input w-full h-10 px-3 text-sm font-semibold tabular-nums rounded-xl" />
              {isForeign && amount && !isNaN(parseFloat(amount)) && (
                <p className="mt-1 text-[11px] text-ink-tertiary tabular-nums">≈ {currencySymbol}{conv(parseFloat(amount)).toFixed(2)} {currencyCode} <span className="text-ink-placeholder">at the document rate</span></p>
              )}</div>
            <div className="grid grid-cols-2 gap-3 items-start">
              <div><label className="block text-xs font-medium text-ink-secondary mb-1">Method</label>
                <select value={method} onChange={(e) => { setMethod(e.target.value); setAccount(pickDefaultAccount(accountsFor(allAccts, e.target.value as PaymentOption), ctx.location_id)); }} className="glass-input w-full py-2 px-3 text-xs font-semibold rounded-xl"><option value="cash">Cash</option><option value="cashless">Cashless / UPI</option><option value="card">Card</option></select></div>
              {accountsFor(allAccts, method as PaymentOption).length > 0 && <div><label htmlFor="wo-pay-account" className="block text-xs font-medium text-ink-secondary mb-1">Account</label>
                <AccountPicker id="wo-pay-account" accounts={allAccts} paymentOption={method as PaymentOption} locationId={ctx.location_id} value={account} onChange={setAccount} onCreated={addAccount} /></div>}
            </div>
            {accountsFor(allAccts, method as PaymentOption).length === 0 && (
              <AccountPicker accounts={allAccts} paymentOption={method as PaymentOption} locationId={ctx.location_id} value={account} onChange={setAccount} onCreated={addAccount} />
            )}
            {modal === "pay" && method === "card" && can("can_take_card_payments") && (
              <button onClick={() => setOnReader(true)} disabled={!(parseFloat(amount) > 0)} className="w-full py-2 rounded-xl border border-primary/30 bg-white text-primary text-xs font-semibold hover:bg-blue-50 disabled:opacity-50 flex items-center justify-center gap-2"><M n="contactless" c="text-base" />Charge on reader</button>
            )}
            {/* Like Orderry: what prints by itself is chosen where it happens. Shop-wide, kept until changed. */}
            {modal === "pay" && (
              <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2">
                <span className="text-xs text-ink-secondary">Print automatically when any job closes <span className="text-ink-tertiary">(shop setting)</span></span>
                <AutoPrintPicker event="close" />
              </div>
            )}
            <div className="flex items-center gap-2 pt-1"><button onClick={submit} disabled={payBusy || !account} className={btnPrimary}>{payBusy ? "Recording…" : `Record ${modal === "prepay" ? "prepayment" : "payment"}`}</button><button onClick={() => setModal(null)} className={btnSecondary}>Cancel</button></div>
          </div>
        </div>
      , document.body)}
      {onReader && <ReaderCharge orderId={orderId} amount={parseFloat(amount) || 0} accountId={account || undefined}
        onApproved={() => { onFlash("Card payment approved"); load(); }}
        onClose={(c) => { setOnReader(false); if (c?.status === "succeeded") setModal(null); }} />}
    </div>
  );
}

/* ---- Activity tab ---- */
type FeedItem = { id: string; item_type: string; body?: string | null; media_data?: string | null; media_url?: string | null; media_name?: string | null; media_mime?: string | null;
  visibility?: string | null; author_name?: string | null; created_at?: string };
type Vis = "private" | "client";
const FEED_FILTER: Record<string, (i: FeedItem) => boolean> = {
  "Status changes": (i) => i.item_type === "system" || i.item_type === "status",
  "Messages": (i) => i.item_type === "client_message",
  "Notes": (i) => i.item_type === "comment",
  "Files": (i) => ["photo", "file", "signature"].includes(i.item_type),
};

/** J2 — who can see a note or file: only the team, or the customer too (on their tracking page). */
export function VisibilitySwitch({ value, onChange }: { value: Vis; onChange: (v: Vis) => void }) {
  const opts: [Vis, string, string][] = [["private", "Only staff", "lock"], ["client", "Customer can see", "visibility"]];
  return (
    <div role="radiogroup" aria-label="Who can see this" className="inline-flex gap-0.5 rounded-lg bg-slate-100 p-0.5 shrink-0">
      {opts.map(([k, l, icon]) => (
        <button key={k} type="button" role="radio" aria-checked={value === k} onClick={() => onChange(k)}
          className={`px-2 py-1 rounded-md text-[11px] font-semibold flex items-center gap-1 ${value === k ? (k === "client" ? "bg-white text-amber-800 shadow-sm" : "bg-white text-ink-primary shadow-sm") : "text-ink-tertiary hover:text-ink-primary"}`}>
          <M n={icon} c="text-[13px]" />{l}
        </button>
      ))}
    </div>
  );
}

/** J2 — the job's notes & files: add a note or attach a photo/file, choosing who can see it. */
export function JobNotes({ orderId, filter, onFlash }: { orderId: string; filter: string; onFlash: Flash }) {
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [text, setText] = useState("");
  const [vis, setVis] = useState<Vis>("private");
  const [busy, setBusy] = useState(false);
  const load = () => api.get<{ items: FeedItem[] }>(`/feed/order/${orderId}`).then((r) => setItems(r.items || [])).catch(() => setItems([]));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [orderId]);
  async function addNote() {
    const body = text.trim(); if (!body || busy) return;
    setBusy(true);
    try { await api.post(`/feed/order/${orderId}`, { item_type: "comment", body, visibility: vis }); setText(""); onFlash(vis === "client" ? "Note added. The customer can see it." : "Note added for staff only"); load(); }
    catch (e: any) { onFlash(e.message || "Couldn't add the note", "warn"); }
    finally { setBusy(false); }
  }
  async function attach(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        const data = await readAsDataUrl(f);
        await api.post(`/feed/order/${orderId}`, { item_type: f.type.startsWith("image/") ? "photo" : "file", media_data: data, media_name: f.name, media_mime: f.type || undefined, body: text.trim() || undefined, visibility: vis });
      }
      setText(""); onFlash(files.length === 1 ? "File added" : `${files.length} files added`); load();
    } catch (e: any) { onFlash(e.message || "Upload failed (the file may be too large)", "warn"); }
    finally { setBusy(false); }
  }
  const pick = FEED_FILTER[filter];
  const shown = (items || []).filter((i) => (pick ? pick(i) : true));
  const icon = (t: string) => t === "photo" ? "image" : t === "file" ? "attach_file" : t === "signature" ? "draw" : t === "client_message" ? "forum" : t === "comment" ? "sticky_note_2" : "bolt";
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 p-4 shadow-sm space-y-3">
      <div className="flex items-center gap-2"><M n="sticky_note_2" c="text-primary text-base" /><h5 className="text-xs font-bold text-ink-primary">Notes &amp; files</h5></div>
      <div className="space-y-2">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} aria-label="Add a note" placeholder="Add a note, e.g. ordered the screen, back Thursday"
          className="glass-input w-full py-2 px-3 text-xs rounded-xl resize-none" />
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={addNote} disabled={busy || !text.trim()} className="px-3.5 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-40">Add note</button>
          <label className={`px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50 flex items-center gap-1 cursor-pointer ${busy ? "opacity-50 pointer-events-none" : ""}`}>
            <input type="file" multiple className="hidden" aria-label="Attach photo or file" onChange={(e) => { attach(e.target.files); e.currentTarget.value = ""; }} />
            <M n="attach_file" c="text-sm" />Attach photo or file
          </label>
          <span className="ml-auto"><VisibilitySwitch value={vis} onChange={setVis} /></span>
        </div>
        {vis === "client" && <p className="text-[11px] text-amber-700">The customer will see this on their tracking page.</p>}
      </div>
      {!items && <div className="text-[11px] text-ink-tertiary">Loading…</div>}
      {items && shown.length === 0 && <div className="text-[11px] text-ink-tertiary">{items.length ? "Nothing of this kind yet." : "No notes or files yet."}</div>}
      {shown.length > 0 && (
        <ol className="space-y-2.5">
          {shown.map((i) => {
            const isImg = i.item_type === "photo" || i.item_type === "signature" || (i.media_mime || "").startsWith("image/");
            return (
              <li key={i.id} className="flex gap-2.5">
                <span className="mt-0.5 w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 bg-slate-100 text-ink-tertiary"><M n={icon(i.item_type)} c="text-[13px]" /></span>
                <div className="min-w-0 flex-1">
                  {i.body && <p className="text-xs text-ink-primary whitespace-pre-line break-words">{i.body}</p>}
                  {isImg && (i.media_url || i.media_data) && (i.media_url
                    ? <AuthedImage src={i.media_url} alt={i.media_name || "attachment"} className="mt-1 h-20 max-w-[200px] object-cover rounded-lg border border-slate-200" />
                    : <img src={i.media_data!} alt={i.media_name || "attachment"} className="mt-1 h-20 max-w-[200px] object-cover rounded-lg border border-slate-200" />)}
                  {!isImg && i.media_name && <p className="text-xs text-ink-secondary flex items-center gap-1"><M n="description" c="text-sm" />{i.media_name}</p>}
                  <p className="text-[11px] text-ink-tertiary flex flex-wrap items-center gap-1.5">
                    <span>{i.item_type === "system" ? "Automatic" : i.author_name || "Staff"} · {fmtServerTime(i.created_at)}</span>
                    {i.visibility === "client" && <span className="px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200 font-semibold flex items-center gap-0.5"><M n="visibility" c="text-[11px]" />Customer can see</span>}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

const CHANNEL: Record<string, string> = { sms: "SMS", email: "Email", whatsapp: "WhatsApp", push: "Push", telegram: "Telegram", viber: "Viber", voice: "Call" };
const channelLabel = (c: string) => CHANNEL[c.toLowerCase()] || humanize(c);
function ActivityTab({ orderId, clientPhone, onFlash, threadKey, onSent }: { orderId: string; clientName?: string; clientPhone?: string; onFlash: Flash; threadKey?: number; onSent?: () => void }) {
  const [msg, setMsg] = useState("");
  const [channel, setChannel] = useState<SendChannel>("auto");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone?: "warn" | "error" } | null>(null);
  // G3 — the same customer thread the conversation box above shows.
  const { msgs, load: loadThread } = useThread(orderId, threadKey);
  const [f, setF] = useState("All");
  const [aiSum, setAiSum] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  async function aiSummary() {
    setAiBusy(true);
    try { const r = await api.post<any>(`/ai/orders/${orderId}/summary`, {}); setAiSum(r.summary || r.text || "No summary."); }
    catch (e: any) { onFlash(e.message || "Couldn't summarize this job"); } finally { setAiBusy(false); }
  }
  // 144jc — real notification delivery log for this order
  const [logs, setLogs] = useState<any[]>([]);
  const loadLogs = () => api.get<any>(`/notifications/log?order_id=${orderId}`).then((r) => setLogs(r.items || [])).catch(() => setLogs([]));
  useEffect(() => { loadLogs(); /* eslint-disable-next-line */ }, [orderId, threadKey]);
  const send = async () => {
    const body = msg.trim(); if (!body || busy) return;
    setBusy(true);
    try {
      const r = await api.post<ThreadSent>(`/orders/${orderId}/customer-reply`, { body, channel });
      setMsg("");
      const text = deliveryText(r?.delivery, "Saved on the job.");
      const tone = deliveryTone(r?.delivery);
      setNote({ text, tone }); onFlash(text, tone);
      await loadThread(); onSent?.();
    } catch (e: any) {
      const text = e?.message || "Couldn't send that message";
      setNote({ text, tone: "error" }); onFlash(text, "warn");
    } finally { setBusy(false); }
  };
  const statusStyle = (s: string) => s === "sent" ? "bg-emerald-500" : s === "failed" ? "bg-rose-500" : s === "scheduled" ? "bg-amber-500" : "bg-slate-400";
  const fmtWhen = (iso?: string) => fmtServerTime(iso) || iso || "";
  const fmtShort = (iso?: string) => fmtServerTime(iso);
  const showLogs = f === "All" || f === "Messages";
  const showThread = f === "All" || f === "Messages";
  return (
    <div className="space-y-5">
      <Section title="Job summary" icon="summarize" description={aiSum ? undefined : "What has happened on this job so far, in a few lines."}
        actions={<button onClick={aiSummary} disabled={aiBusy} className={btnSecondary}>{aiBusy ? "Summarizing…" : aiSum ? "Regenerate" : "Summarize"}</button>}>
        {aiSum ? <p className="text-sm text-ink-secondary leading-relaxed">{aiSum}</p> : null}
      </Section>
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        {["All", "Status changes", "Payments", "Messages", "Notes", "Files"].map((c) => (
          <button key={c} onClick={() => setF(c)} aria-pressed={f === c} className={`px-2.5 py-1 rounded-full text-[11px] ${f === c ? "bg-primary text-white font-semibold" : "bg-white border border-slate-200 text-ink-secondary hover:bg-slate-50"}`}>{c}</button>
        ))}
      </div>
      {f !== "Payments" && <JobNotes orderId={orderId} filter={f} onFlash={onFlash} />}
      {f === "Payments" && <p className="text-xs text-ink-tertiary">Payments for this job are on the Invoices &amp; payments tab.</p>}
      {showLogs && <Section title="Notifications & delivery" icon="notifications">
        {logs.length === 0 ? (
          <div className="text-xs text-ink-tertiary">No messages sent for this job yet.</div>
        ) : (
          <div className="relative pl-6 space-y-4 before:absolute before:left-2 before:top-2 before:bottom-2 before:w-[2px] before:bg-slate-200 text-xs">
            {logs.map((l) => (
              <div key={l.id} className="relative">
                <span className={`absolute -left-6 top-1 w-2.5 h-2.5 rounded-full ring-4 ring-white ${statusStyle(l.status)}`} />
                <div>
                  <p className="font-semibold text-ink-primary flex flex-wrap items-center gap-2">
                    {(l.subject || humanize(l.trigger_event) || "Notification")}
                    {l.channel && <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-full bg-slate-100 text-ink-secondary">{channelLabel(l.channel)}</span>}
                    <span className={`text-[11px] font-semibold ${l.status === "failed" ? "text-rose-600" : l.status === "sent" ? "text-emerald-600" : "text-amber-600"}`}>{humanize(l.status)}</span>
                  </p>
                  {l.body && <p className="text-xs text-ink-secondary line-clamp-2">{l.body}</p>}
                  <span className="text-[11px] text-ink-tertiary tabular-nums">{l.recipient ? `${l.recipient} · ` : ""}{fmtWhen(l.created_at)}{l.error ? ` · ${l.error}` : ""}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>}
      {showThread && <Section title="Messages with the customer" icon="chat"
        meta={<span className="inline-flex items-center gap-1"><M n="call" c="text-[13px]" />{clientPhone || "No phone number"}</span>}>
        <div className="space-y-3">
          {msgs && msgs.length > 0 && (
            <div className="space-y-3 p-2 bg-slate-50/70 rounded-xl max-h-48 overflow-y-auto">
              {msgs.map((m) => {
                const staff = m.from === "staff";
                return (
                  <div key={m.id} className={`flex flex-col max-w-[80%] ${staff ? "items-end ml-auto" : "items-start"}`}>
                    <div className={`p-2.5 rounded-2xl shadow-2xs text-xs whitespace-pre-wrap break-words ${staff ? "bg-primary text-white rounded-tr-none" : "bg-white border border-slate-200 text-ink-primary rounded-tl-none"}`}>{m.body}</div>
                    <span className="text-[11px] text-slate-400 mx-1 mt-0.5 tabular-nums">{m.who || (staff ? "Staff" : "Customer")} · {fmtShort(m.at)}</span>
                  </div>
                );
              })}
            </div>
          )}
          <div className="flex items-center gap-2 pt-1">
            <ChannelPicker id={`act-ch-${orderId}`} value={channel} onChange={setChannel} />
            <input value={msg} onChange={(e) => setMsg(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} aria-label="Message to the customer" className="glass-input flex-1 py-2 px-3 text-xs rounded-xl" placeholder="Send the customer an update…" />
            <button onClick={send} disabled={busy || !msg.trim()} className="px-3.5 py-2 rounded-xl bg-primary text-white text-xs font-bold hover:brightness-110 flex items-center gap-1 disabled:opacity-40"><span>{busy ? "Sending…" : "Send"}</span><M n="send" c="text-xs" /></button>
          </div>
          <DeliveryNote note={note} />
        </div>
      </Section>}
    </div>
  );
}

