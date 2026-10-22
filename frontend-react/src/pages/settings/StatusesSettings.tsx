import { useEffect, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { confirmDialog, selectDialog } from "../../lib/dialog";
import { CHANNEL_LABEL, cellRules, channelPhrase, firesOnStatus, firingLabel, statusEvent, toggleCell, withOverrides, type NotifRule } from "../../lib/notifRules";

/* Settings › Statuses — the list (grouped by stage, live job counts, icon badges) and the edit panel
   (plain-English summary, example text, everyday switches up front, the rest under Advanced).
   Same data and endpoints as before: /statuses, /statuses/reorder, /statuses/{id}?move_to=.
   Customer messages are notification rules (/notifications/rules), not a status flag: the editor
   reads out what fires and flips this status's own rule, like Settings › Notifications does. */

const RECORD_TYPES: [string, string][] = [["work_order", "Work orders"], ["estimate", "Estimates"], ["inquiry", "Inquiries"],
  ["invoice", "Invoices"], ["transfer", "Transfers"], ["purchase_order", "Purchase orders"]];

// Stages shown as section headings. Waiting (pending) reads as part of the work, so it shares a heading.
const STAGE_OF: Record<string, string> = { new: "arrived", in_progress: "working", pending: "working", ready: "ready", closed: "finished", dropped: "finished" };
const STAGES: Record<string, { label: string; icon: string; plain: string }> = {
  arrived: { label: "Just arrived", icon: "add_circle", plain: "New" },
  working: { label: "Being worked on", icon: "build", plain: "In progress" },
  ready: { label: "Ready for pickup", icon: "notifications", plain: "Ready" },
  finished: { label: "Finished", icon: "check_circle", plain: "Closed" },
};
const STAGE_COLOR: Record<string, string> = { arrived: "#378ADD", working: "#7F77DD", ready: "#1D9E75", finished: "#639922" };

export const GROUP_OPTIONS: [string, string][] = [["new", "Just arrived"], ["in_progress", "Being worked on"], ["pending", "Waiting (parts, approval…)"],
  ["ready", "Ready for pickup"], ["closed", "Finished"], ["dropped", "Not collected / cancelled"]];

export const SWATCHES = ["#378ADD", "#7F77DD", "#D4537E", "#EF9F27", "#D85A30", "#1D9E75", "#639922", "#888780"];

const LIMITS: [number | null, string][] = [[null, "Off"], [30, "30 minutes"], [60, "1 hour"], [240, "4 hours"], [1440, "1 day"], [2880, "2 days"], [4320, "3 days"], [10080, "1 week"]];
const limitLabel = (m?: number | null) => {
  if (!m) return "";
  const hit = LIMITS.find(([v]) => v === m); if (hit) return hit[1];
  return m % 1440 === 0 ? `${m / 1440} days` : m % 60 === 0 ? `${m / 60} hours` : `${m} minutes`;
};

const hasCounts = (rt: string) => rt === "work_order" || rt === "estimate";

function badges(s: any, ownMessages: string[]): { icon: string; title: string }[] {
  const out: { icon: string; title: string }[] = [];
  if (ownMessages.length) out.push({ icon: "chat", title: `Messages the customer (${channelPhrase(ownMessages)})` });
  if (s.triggers_invoice) out.push({ icon: "receipt_long", title: "Creates an invoice" });
  if (s.triggers_payment) out.push({ icon: "payments", title: "Asks for payment" });
  if (s.time_limit_min) out.push({ icon: "timer", title: `Alerts if a job is here longer than ${limitLabel(s.time_limit_min)}` });
  if (s.locks_edits) out.push({ icon: "lock", title: "Locks the job" });
  if (s.require_comment || s.require_payment || s.require_checklist) out.push({ icon: "fact_check", title: "Needs something done first" });
  if (s.allow_messages === false) out.push({ icon: "block", title: "Customers can't message the shop" });
  return out;
}

export function StatusList({ statuses, recordType, onRecordType, onOpen, onAdd, onMove, onDeleted, onFlash }: {
  statuses: any[]; recordType: string; onRecordType: (rt: string) => void; onOpen: (s: any) => void; onAdd: () => void;
  onMove: (from: number, to: number) => void; onDeleted: () => void; onFlash: (m: string) => void;
}) {
  const drag = useRef<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const [rules, setRules] = useState<NotifRule[]>([]);
  useEffect(() => { api.get<any>("/notifications/rules").then((r) => setRules(r.items || [])).catch(() => setRules([])); }, [statuses]);
  // Only a status's own rules get a badge: the every-status rules apply to all of them alike.
  const ownMessages = (s: any) => firesOnStatus(rules, s.record_type || recordType, statusEvent(s), s.key).filter((f) => f.own).map((f) => f.channel);
  const plainStages = recordType === "work_order";
  const counts = hasCounts(recordType) && statuses.some((s) => s.job_count !== undefined);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menu]);

  const stageTotals: Record<string, number> = { arrived: 0, working: 0, ready: 0, finished: 0 };
  statuses.forEach((s) => { stageTotals[STAGE_OF[s.status_group] || "working"] += s.job_count || 0; });
  const totalJobs = Object.values(stageTotals).reduce((a, b) => a + b, 0);

  async function remove(s: any) {
    setMenu(null);
    try {
      if (s.in_use > 0) {
        const others = statuses.filter((o) => o.id !== s.id);
        const pick = await selectDialog(`${s.in_use} ${s.in_use === 1 ? "job is" : "jobs are"} in "${s.name}". Move ${s.in_use === 1 ? "it" : "them"} to:`,
          others.map((o) => o.name), { title: `Delete "${s.name}"?`, confirmLabel: "Move and delete", danger: true });
        if (!pick) return;
        const target = others.find((o) => o.name === pick);
        if (!target) return;
        await api.del(`/statuses/${s.id}?move_to=${encodeURIComponent(target.key)}`);
        onFlash(`Deleted. ${s.in_use} moved to ${target.name}`);
      } else {
        if (!(await confirmDialog(`Delete "${s.name}"? No jobs use it right now.`, { title: "Delete status", confirmLabel: "Delete", danger: true }))) return;
        await api.del(`/statuses/${s.id}`);
        onFlash("Status deleted");
      }
      onDeleted();
    } catch (e: any) { onFlash(e?.message || "Couldn't delete"); }
  }

  const more = RECORD_TYPES.slice(2);
  const inMore = more.some(([rt]) => rt === recordType);

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center gap-2">
        <div className="inline-flex items-center bg-slate-100 rounded-xl p-1">
          {RECORD_TYPES.slice(0, 2).map(([rt, label]) => (
            <button key={rt} onClick={() => onRecordType(rt)}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${recordType === rt ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{label}</button>
          ))}
          <select value={inMore ? recordType : ""} onChange={(e) => e.target.value && onRecordType(e.target.value)} aria-label="More status lists"
            className={`bg-transparent pl-3 pr-1 py-1.5 rounded-lg text-xs font-semibold cursor-pointer outline-none ${inMore ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary"}`}>
            <option value="">More…</option>
            {more.map(([rt, label]) => <option key={rt} value={rt}>{label}</option>)}
          </select>
        </div>
        <button onClick={onAdd} className="ml-auto inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-primary hover:border-slate-300 hover:bg-slate-50"><M n="add" c="text-sm" />Add status</button>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-3">
        {counts && (
          <div className="px-2 pt-1 pb-3 border-b border-slate-100">
            <div className="flex gap-1 h-1.5">
              {(["arrived", "working", "ready", "finished"] as const).map((k) => (
                <div key={k} className="rounded-full transition-all" style={{ flex: totalJobs ? Math.max(stageTotals[k], totalJobs * 0.04) : 1, background: STAGE_COLOR[k], opacity: stageTotals[k] ? 1 : 0.25 }} />
              ))}
            </div>
            <div className="flex justify-between mt-1.5 text-[11px] text-ink-tertiary">
              <span>{stageTotals.arrived} just arrived</span><span>{stageTotals.working} in the shop</span><span>{stageTotals.ready} ready</span><span>{stageTotals.finished} finished in 30 days</span>
            </div>
          </div>
        )}

        {statuses.length === 0 && <div className="p-5 text-xs text-ink-tertiary">Loading statuses…</div>}
        {statuses.map((s, idx) => {
          const stage = STAGE_OF[s.status_group] || "working";
          const head = idx === 0 || (STAGE_OF[statuses[idx - 1].status_group] || "working") !== stage;
          const terminal = s.status_group === "closed" || s.status_group === "dropped";
          return (
            <div key={s.id}>
              {head && (
                <div className="flex items-center gap-1.5 px-3 pt-4 pb-1 text-[11px] font-semibold text-ink-tertiary">
                  <M n={STAGES[stage].icon} c="text-sm" />{plainStages ? STAGES[stage].label : STAGES[stage].plain}
                </div>
              )}
              <div draggable onDragStart={() => { drag.current = idx; }} onDragEnd={() => { drag.current = null; setOver(null); }}
                onDragOver={(e) => { e.preventDefault(); setOver(idx); }} onDragLeave={() => setOver((d) => (d === idx ? null : d))}
                onDrop={(e) => { e.preventDefault(); const f = drag.current; setOver(null); if (f !== null) onMove(f, idx); }}
                onClick={() => onOpen(s)}
                className={`group relative flex items-center gap-3 pl-1.5 pr-2 py-2.5 rounded-xl cursor-pointer hover:bg-slate-50 transition-colors ${over === idx ? "ring-2 ring-primary/30" : ""}`}>
                <div className="flex items-center opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
                  <M n="drag_indicator" c="text-ink-tertiary cursor-grab text-base" />
                  <div className="flex flex-col">
                    <button aria-label={`Move ${s.name} up`} disabled={idx === 0} onClick={() => onMove(idx, idx - 1)} className="text-ink-tertiary hover:text-primary disabled:opacity-20 leading-none"><M n="expand_less" c="text-sm" /></button>
                    <button aria-label={`Move ${s.name} down`} disabled={idx === statuses.length - 1} onClick={() => onMove(idx, idx + 1)} className="text-ink-tertiary hover:text-primary disabled:opacity-20 leading-none"><M n="expand_more" c="text-sm" /></button>
                  </div>
                </div>
                <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ background: s.color || "#94a3b8", boxShadow: `0 0 0 3px #fff, 0 0 0 4px ${(s.color || "#94a3b8")}55` }} />
                <span className="text-sm font-medium text-ink-primary truncate">{s.name}</span>
                {s.is_default && <span className="text-[11px] text-ink-tertiary">starts every job</span>}
                <span className="ml-auto flex items-center gap-1.5">
                  {counts && <span className="text-[11px] text-ink-tertiary mr-1 whitespace-nowrap">{terminal ? `${s.job_count || 0} in 30 days` : `${s.job_count || 0} ${s.job_count === 1 ? "job" : "jobs"}`}</span>}
                  {badges(s, ownMessages(s)).map((b) => (
                    <span key={b.icon} title={b.title} aria-label={b.title} className="w-6 h-6 rounded-md bg-slate-100 text-ink-secondary grid place-items-center"><M n={b.icon} c="text-[14px]" /></span>
                  ))}
                  <button aria-label={`More actions for ${s.name}`} onClick={(e) => { e.stopPropagation(); setMenu(menu === s.id ? null : s.id); }}
                    className="w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:bg-slate-100 hover:text-ink-primary opacity-0 group-hover:opacity-100 focus:opacity-100"><M n="more_horiz" c="text-base" /></button>
                </span>
                {menu === s.id && (
                  <div className="absolute right-2 top-11 z-20 w-40 bg-white rounded-xl border border-slate-200 shadow-clay-drawer py-1 text-xs" onClick={(e) => e.stopPropagation()}>
                    <button onClick={() => { setMenu(null); onOpen(s); }} className="w-full text-left px-3 py-2 hover:bg-slate-50 flex items-center gap-2"><M n="edit" c="text-sm" />Edit</button>
                    {s.is_default
                      ? <div className="px-3 py-2 text-ink-tertiary">The starting status can't be deleted</div>
                      : <button onClick={() => remove(s)} className="w-full text-left px-3 py-2 hover:bg-rose-50 text-rose-600 flex items-center gap-2"><M n="delete" c="text-sm" />Delete</button>}
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {hasCounts(recordType) && statuses.length > 1 && <MovesPanel statuses={statuses} onFlash={onFlash} />}
      </div>
    </div>
  );
}

/* Which status may follow which (/statuses/transitions, org-wide rules only — role-specific rules are
   set per role). A status with no rules is open: a job can go anywhere from it. */
function MovesPanel({ statuses, onFlash }: { statuses: any[]; onFlash: (m: string) => void }) {
  const [open, setOpen] = useState(false);
  const [edges, setEdges] = useState<any[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get<any>("/statuses/transitions").then((r) => setEdges((r.items || []).filter((e: any) => !e.role_id))).catch(() => setEdges([]));
  useEffect(() => { load(); }, []);
  const ids = new Set(statuses.map((s) => s.id));
  const outs = (id: string) => edges.filter((e) => e.from_status_id === id && ids.has(e.to_status_id));
  const limited = statuses.filter((s) => outs(s.id).length > 0).length;

  async function toggle(fromId: string, toId: string) {
    if (busy) return; setBusy(true);
    const hit = edges.find((e) => e.from_status_id === fromId && e.to_status_id === toId);
    try {
      if (hit) await api.del(`/statuses/transitions/${hit.id}`);
      else await api.post("/statuses/transitions", { from_status_id: fromId, to_status_id: toId });
      await load();
    } catch (e: any) { onFlash(e?.message || "Couldn't save the rule"); } finally { setBusy(false); }
  }
  async function reset(fromId: string) {
    if (busy) return; setBusy(true);
    try { for (const e of outs(fromId)) await api.del(`/statuses/transitions/${e.id}`); await load(); onFlash("Open again: any status"); }
    catch (e: any) { onFlash(e?.message || "Couldn't reset"); } finally { setBusy(false); }
  }

  const Dot = ({ c }: { c?: string }) => <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: c || "#94a3b8" }} />;
  return (
    <div className="mt-2 border-t border-slate-100">
      <button onClick={() => { setOpen((v) => !v); setEditing(null); }} aria-expanded={open} className="w-full flex items-center gap-3 px-3 py-3 text-left">
        <span className="w-8 h-8 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n="swap_horiz" c="text-base" /></span>
        <span className="min-w-0">
          <span className="block text-sm text-ink-primary">Which status can follow which</span>
          <span className="block text-[11px] text-ink-tertiary">{limited ? `${limited} ${limited === 1 ? "status has" : "statuses have"} limits. The rest can move anywhere.` : "Jobs can move to any status"}</span>
        </span>
        <span className="ml-auto text-xs font-semibold text-primary">{open ? "Done" : "Set rules"}</span>
      </button>
      {open && (
        <div className="px-3 pb-3">
          <p className="text-[11px] text-ink-tertiary mb-2 pl-11">Choose where a job can go next. A status left open lets a job go anywhere.</p>
          <div className="space-y-1">
            {statuses.map((fr) => {
              const mine = outs(fr.id).map((e) => e.to_status_id);
              const isEdit = editing === fr.id;
              return (
                <div key={fr.id} className={`rounded-xl px-3 py-2.5 transition-colors ${isEdit ? "bg-slate-50 ring-1 ring-slate-200" : "hover:bg-slate-50"}`}>
                  <div className="flex items-center gap-2.5 flex-wrap">
                    <span className="flex items-center gap-2 w-36 flex-shrink-0 min-w-0">
                      <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: fr.color, boxShadow: `0 0 0 2px #fff, 0 0 0 3px ${fr.color}55` }} />
                      <span className="text-sm text-ink-primary truncate">{fr.name}</span>
                    </span>
                    <M n="arrow_forward" c="text-sm text-ink-tertiary" />
                    {!isEdit && (mine.length === 0
                      ? <span className="text-xs text-ink-tertiary">Any status</span>
                      : <span className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-[11px] text-ink-tertiary">Only</span>
                          {mine.map((tid) => { const t = statuses.find((x) => x.id === tid); return t && (
                            <span key={tid} className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-white border border-slate-200 text-xs text-ink-primary"><Dot c={t.color} />{t.name}</span>
                          ); })}
                        </span>)}
                    {isEdit && <span className="text-[11px] text-ink-tertiary">{mine.length ? "Only the ones you pick" : "Pick where a job can go next"}</span>}
                    <span className="ml-auto flex items-center gap-2">
                      {isEdit && mine.length > 0 && <button onClick={() => reset(fr.id)} disabled={busy} className="text-[11px] text-ink-secondary hover:text-ink-primary">Allow any</button>}
                      <button onClick={() => setEditing(isEdit ? null : fr.id)} className="text-xs font-semibold text-primary">{isEdit ? "Done" : mine.length ? "Change" : "Limit"}</button>
                    </span>
                  </div>
                  {isEdit && (
                    <div className="flex flex-wrap gap-1.5 mt-2.5 pl-[11.5rem] max-sm:pl-0">
                      {statuses.filter((t) => t.id !== fr.id).map((t) => {
                        const on = mine.includes(t.id);
                        return (
                          <button key={t.id} onClick={() => toggle(fr.id, t.id)} disabled={busy} aria-pressed={on}
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-colors disabled:opacity-60 ${on ? "bg-white border-primary/40 text-ink-primary shadow-sm" : "border-dashed border-slate-300 text-ink-tertiary hover:border-slate-400 hover:text-ink-secondary"}`}>
                            {on ? <M n="check" c="text-[12px] text-primary" /> : <Dot c={t.color} />}{t.name}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function StatusEditor({ status, recordType = "work_order", statuses, onClose, onSaved, onOpenNotifications }: {
  status: any | null; recordType?: string; statuses: any[]; onClose: () => void; onSaved: (m?: string) => void;
  onOpenNotifications?: () => void;
}) {
  const isNew = !status?.id;
  const rt = status?.record_type || recordType;
  const [f, setF] = useState<any>(status || {
    name: "", color: SWATCHES[0], status_group: "in_progress", is_default: false,
    triggers_invoice: false, triggers_payment: false, locks_edits: false,
    moves_asset_to: "", time_limit_min: null, require_comment: false, require_payment: false, require_checklist: false,
    shows_accept: false, converts_to_order: false, allow_messages: null,
  });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [warehouses, setWarehouses] = useState<any[]>([]);
  const advCount = [f.is_default, f.locks_edits, f.moves_asset_to, f.require_comment, f.require_payment, f.require_checklist].filter(Boolean).length;
  const [adv, setAdv] = useState(false);
  const [customLimit, setCustomLimit] = useState(() => !!f.time_limit_min && !LIMITS.some(([v]) => v === Number(f.time_limit_min)));
  useEffect(() => { api.get<any>("/settings/warehouses").then((r) => setWarehouses(r.items || r || [])).catch(() => setWarehouses([])); }, []);
  const [rules, setRules] = useState<NotifRule[] | null>(null);
  const [providers, setProviders] = useState<Record<string, string>>({});
  const [msgBusy, setMsgBusy] = useState<string | null>(null);
  const loadRules = () => api.get<any>("/notifications/rules").then((r) => setRules(r.items || [])).catch(() => setRules([]));
  useEffect(() => { loadRules(); api.get<any>("/notifications/providers").then((r) => setProviders(r.channels || {})).catch(() => setProviders({})); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  const terminal = f.status_group === "closed" || f.status_group === "dropped";
  const jobs = hasCounts(rt);

  async function save() {
    if (!f.name?.trim()) { setErr("Give the status a name"); return; }
    setBusy(true); setErr(null);
    const body = {
      name: f.name.trim(), color: f.color, status_group: f.status_group, is_default: !!f.is_default,
      triggers_invoice: !!f.triggers_invoice,
      triggers_payment: !!f.triggers_payment, locks_edits: !!f.locks_edits,
      moves_asset_to: f.moves_asset_to || null, time_limit_min: f.time_limit_min ? Number(f.time_limit_min) : null,
      require_comment: !!f.require_comment, require_payment: !!f.require_payment, require_checklist: !!f.require_checklist,
      shows_accept: !!f.shows_accept, converts_to_order: !!f.converts_to_order,
      record_type: rt, allow_messages: f.allow_messages ?? null,
    };
    try {
      if (isNew) await api.post("/statuses", body); else await api.put(`/statuses/${status.id}`, body);
      onSaved(isNew ? "Status added" : "Status saved");
    } catch (e: any) { setErr(e?.message || "Couldn't save"); setBusy(false); }
  }

  // Customer messages, from the notification rules. Every-status rules follow the stage picked
  // here; this status's own rules sit on its saved stage (the server moves them when it changes).
  const key: string | undefined = status?.key;
  const formEvent = statusEvent({ status_group: f.status_group });
  const ownEvent = status?.id ? statusEvent(status) : formEvent;
  const fires = rules ? withOverrides([...firesOnStatus(rules, rt, formEvent).filter((x) => !x.own),
    ...(key ? firesOnStatus(rules, rt, ownEvent, key).filter((x) => x.own) : [])]) : [];
  const msgChannels = ["sms", "email", ...(providers.whatsapp === "live" || (rules || []).some((r) => r.channel === "whatsapp") ? ["whatsapp"] : [])];
  async function flipMessage(ch: string) {
    if (!rules || !key || msgBusy) return; setMsgBusy(ch);
    try { await toggleCell(api, rules, rt, ownEvent, key, ch); await loadRules(); }
    catch (e: any) { setErr(e?.message || "Couldn't change that message"); } finally { setMsgBusy(null); }
  }
  const openNotifications = () => { if (onOpenNotifications) onOpenNotifications(); else window.location.assign("/settings?section=notifications"); };

  // Plain-English summary of everything this status does.
  const acts: string[] = [];
  if (jobs && fires.length) acts.push(`messages the customer (${channelPhrase(fires.map((x) => x.channel))})`);
  if (f.triggers_invoice) acts.push("creates an invoice");
  if (f.triggers_payment) acts.push("asks for payment");
  if (f.locks_edits) acts.push("locks the job");
  if (f.moves_asset_to) acts.push(`moves the item to ${f.moves_asset_to}`);
  if (f.converts_to_order) acts.push("turns the estimate into a work order");
  const reqs: string[] = [];
  if (f.require_comment) reqs.push("a comment");
  if (f.require_payment) reqs.push("full payment");
  if (f.require_checklist) reqs.push("a finished checklist");
  let summary = acts.length ? `Moving a job here ${acts.join(", ").replace(/, ([^,]*)$/, " and $1")}.` : "Moving a job here doesn't do anything automatically.";
  if (f.time_limit_min) summary += ` You're alerted if a job stays longer than ${limitLabel(Number(f.time_limit_min))}.`;
  if (reqs.length) summary += ` It needs ${reqs.join(" and ")} first.`;
  if (terminal) summary += " The job counts as finished.";
  if (jobs) summary += f.allow_messages === false ? " Customers can't message the shop." : f.allow_messages === true ? " Customers can always message the shop."
    : terminal ? " Customers can still message for a few days." : " Customers can message the shop.";

  const order = [...statuses.filter((s) => s.id !== status?.id)];
  const lbl = "block text-[11px] font-semibold text-ink-tertiary mb-1.5";
  const Switch = ({ k, icon, label, hint }: { k: string; icon: string; label: string; hint?: string }) => (
    <label className="flex items-center gap-3 py-2.5 border-t border-slate-100 first:border-0 cursor-pointer">
      <span className="w-8 h-8 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n={icon} c="text-base" /></span>
      <span className="min-w-0"><span className="block text-sm text-ink-primary">{label}</span>{hint && <span className="block text-[11px] text-ink-tertiary">{hint}</span>}</span>
      <button type="button" role="switch" aria-checked={!!f[k]} aria-label={label} onClick={() => set(k, !f[k])}
        className={`ml-auto w-10 h-6 rounded-full flex-shrink-0 relative transition-colors ${f[k] ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${f[k] ? "left-[18px]" : "left-0.5"}`} /></button>
    </label>
  );
  const Pill = ({ k, label }: { k: string; label: string }) => (
    <button type="button" onClick={() => set(k, !f[k])} aria-pressed={!!f[k]}
      className={`text-xs px-3 py-1.5 rounded-full font-semibold border transition-colors ${f[k] ? "bg-primary/10 text-primary border-primary/30" : "border-slate-200 text-ink-secondary hover:border-slate-300"}`}>{f[k] && <M n="check" c="text-[12px] mr-0.5" />}{label}</button>
  );

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/30 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-label={isNew ? "New status" : `Edit ${f.name}`} className="relative w-[460px] max-w-full bg-white h-full shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.28s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <div className="flex items-center gap-3 px-5 pt-5 pb-3">
          <span className="w-4 h-4 rounded-full flex-shrink-0" style={{ background: f.color, boxShadow: `0 0 0 3px #fff, 0 0 0 5px ${f.color}55` }} />
          <input value={f.name || ""} onChange={(e) => set("name", e.target.value)} placeholder="Status name, like Ready for pickup" autoFocus={isNew}
            aria-label="Status name" className="flex-1 min-w-0 text-lg font-semibold text-ink-primary bg-transparent outline-none placeholder:text-ink-tertiary placeholder:font-normal border-b border-transparent focus:border-slate-200 py-0.5" />
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary flex-shrink-0"><M n="close" c="text-base" /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pb-5 space-y-5">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

          <div className="flex items-center gap-2 flex-wrap">
            {SWATCHES.map((c) => (
              <button key={c} type="button" onClick={() => set("color", c)} aria-label={`Colour ${c}`}
                className={`w-6 h-6 rounded-full flex-shrink-0 grid place-items-center transition-transform hover:scale-110 ${f.color === c ? "ring-2 ring-offset-2 ring-slate-400" : ""}`} style={{ background: c }}>
                {f.color === c && <M n="check" c="text-[11px] text-white" />}
              </button>
            ))}
            <label className="w-6 h-6 rounded-full border border-dashed border-slate-300 grid place-items-center cursor-pointer text-ink-tertiary hover:border-primary hover:text-primary" title="Custom colour">
              <M n="colorize" c="text-xs" /><input type="color" value={f.color || "#888888"} onChange={(e) => set("color", e.target.value)} className="sr-only" />
            </label>
            {order.length > 0 && (
              <span className="ml-auto flex items-center gap-1" title="Your other statuses, for comparison">
                {order.slice(0, 8).map((s) => <span key={s.id} className="w-2.5 h-2.5 rounded-full" style={{ background: s.color }} />)}
              </span>
            )}
          </div>

          <div>
            <label className={lbl}>Stage</label>
            <select value={f.status_group} onChange={(e) => set("status_group", e.target.value)} className="glass-input w-full py-2 px-3 text-sm text-ink-primary rounded-lg">
              {GROUP_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>

          <div className="rounded-xl bg-slate-50 border border-slate-100 px-4 py-3">
            <div className="text-[11px] font-semibold text-ink-tertiary mb-1">What happens</div>
            <p className="text-sm text-ink-primary leading-relaxed">{summary}</p>
          </div>

          {jobs && fires.length > 0 && (
            <div className="flex items-start gap-2.5">
              <span className="w-8 h-8 rounded-full bg-slate-100 grid place-items-center flex-shrink-0 text-ink-secondary"><M n="smartphone" c="text-sm" /></span>
              <div>
                <div className="bg-primary/10 text-ink-primary rounded-2xl rounded-bl-md px-3.5 py-2.5 text-[13px] leading-snug">Hi Sam, your iPhone 14 is now: <span className="font-semibold">{f.name?.trim() || "…"}</span>. Track it here: shop.link/t/8F2K</div>
                <div className="text-[11px] text-ink-tertiary mt-1">Example. The exact wording is set in Notifications.</div>
              </div>
            </div>
          )}

          <div>
            <div className="text-[11px] font-semibold text-ink-tertiary mb-1">When a job moves here</div>
            {jobs && (
              <div className="py-2.5" data-testid="status-messages">
                <div className="flex items-start gap-3">
                  <span className="w-8 h-8 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n="chat" c="text-base" /></span>
                  <span className="min-w-0">
                    <span className="block text-sm text-ink-primary">Message the customer</span>
                    <span className="block text-[11px] text-ink-tertiary">
                      {rules === null ? "Checking…" : fires.length ? fires.map((x) => firingLabel(x, rt)).join(", ") : "Nothing is sent"}
                    </span>
                  </span>
                  <button type="button" onClick={openNotifications} className="ml-auto text-xs font-semibold text-primary whitespace-nowrap">Edit message</button>
                </div>
                <div className="flex items-center gap-4 mt-2 pl-11 flex-wrap">
                  {msgChannels.map((ch) => {
                    const on = !!rules && !!key && cellRules(rules, rt, ownEvent, key, ch).some((r) => r.active);
                    const cov = !on && fires.some((x) => !x.own && x.channel === ch);
                    const label = CHANNEL_LABEL[ch] || ch;
                    return (
                      <span key={ch} className="flex items-center gap-1.5">
                        <button type="button" role="switch" aria-checked={on || cov} aria-label={`${label} when a job moves here`}
                          disabled={!key || !rules || cov || msgBusy === ch} onClick={() => flipMessage(ch)}
                          title={cov ? "Already sent to every status. Change it in Notifications." : undefined}
                          className={`w-9 h-5 rounded-full relative transition-colors disabled:cursor-default ${on ? "bg-primary" : cov ? "bg-primary/30" : "bg-slate-300"}`}>
                          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${on || cov ? "left-[18px]" : "left-0.5"}`} />
                        </button>
                        <span className="text-xs text-ink-secondary">{label}</span>
                      </span>
                    );
                  })}
                </div>
                <p className="text-[11px] text-ink-tertiary mt-1.5 pl-11">{key ? "Switches save right away." : "Save the status first to give it its own message."}</p>
              </div>
            )}
            <Switch k="triggers_invoice" icon="receipt_long" label="Create an invoice" />
            <Switch k="triggers_payment" icon="payments" label="Ask for payment" />
            <div className="flex items-center gap-3 py-2.5 border-t border-slate-100">
              <span className="w-8 h-8 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n="timer" c="text-base" /></span>
              <span className="text-sm text-ink-primary">Alert if a job is stuck here</span>
              {customLimit ? (
                <span className="ml-auto flex items-center gap-1.5">
                  <input type="number" min={1} value={f.time_limit_min ?? ""} onChange={(e) => set("time_limit_min", e.target.value)} aria-label="Minutes"
                    className="glass-input w-20 py-1.5 px-2 text-xs text-right rounded-lg" /><span className="text-xs text-ink-secondary">min</span>
                </span>
              ) : (
                <select value={f.time_limit_min ?? ""} aria-label="Alert after"
                  onChange={(e) => { if (e.target.value === "custom") { setCustomLimit(true); return; } set("time_limit_min", e.target.value ? Number(e.target.value) : null); }}
                  className="ml-auto glass-input py-1.5 px-2 text-xs rounded-lg">
                  {LIMITS.map(([v, l]) => <option key={l} value={v ?? ""}>{l}</option>)}
                  <option value="custom">Custom…</option>
                </select>
              )}
            </div>
            {rt === "estimate" && <>
              <Switch k="shows_accept" icon="verified" label="Let the customer accept or decline" hint="Shows Accept / Decline on their estimate page" />
              <Switch k="converts_to_order" icon="autorenew" label="Turn into a work order" />
            </>}
          </div>

          {jobs && (
            <div>
              <div className="text-[11px] font-semibold text-ink-tertiary mb-1.5">Customer messages</div>
              <div className="inline-flex rounded-xl bg-slate-100 p-1">
                {([[null, "Automatic"], [true, "Allow"], [false, "Block"]] as [boolean | null, string][]).map(([v, l]) => (
                  <button key={l} type="button" onClick={() => set("allow_messages", v)} aria-pressed={(f.allow_messages ?? null) === v}
                    className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${(f.allow_messages ?? null) === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
                ))}
              </div>
              <p className="text-[11px] text-ink-tertiary mt-1.5">
                {f.allow_messages === true ? "Customers can always message the shop from their tracking link."
                  : f.allow_messages === false ? "The message box is hidden. Customers see \"Start a new request\" instead."
                  : terminal ? "Open for a few days after the job finishes (Public pages), then \"Start a new request\"."
                  : "Customers can message the shop while the job is here."}
              </p>
            </div>
          )}

          <div className="rounded-xl border border-slate-200">
            <button type="button" onClick={() => setAdv((v) => !v)} aria-expanded={adv} className="w-full flex items-center gap-2 px-4 py-3 text-sm text-ink-secondary hover:text-ink-primary">
              <M n="tune" c="text-base" />Advanced
              {advCount > 0 && <span className="text-[11px] px-1.5 py-0.5 rounded-md bg-primary/10 text-primary font-semibold">{advCount} on</span>}
              <M n={adv ? "expand_less" : "expand_more"} c="ml-auto text-base" />
            </button>
            {adv && (
              <div className="px-4 pb-4 space-y-4">
                <div>
                  <Switch k="is_default" icon="flag" label="Start every new job here" />
                  <Switch k="locks_edits" icon="lock" label="Lock the job" hint="No edits once a job is here" />
                  <div className="flex items-center gap-3 py-2.5 border-t border-slate-100">
                    <span className="w-8 h-8 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n="inventory_2" c="text-base" /></span>
                    <span className="text-sm text-ink-primary">Move the item to</span>
                    <select value={f.moves_asset_to || ""} onChange={(e) => set("moves_asset_to", e.target.value)} className="ml-auto glass-input py-1.5 px-2 text-xs rounded-lg w-40">
                      <option value="">Don't move</option>
                      {warehouses.filter((w) => w.kind !== "product").map((w) => <option key={w.id} value={w.name}>{w.name}</option>)}
                      {f.moves_asset_to && !warehouses.some((w) => w.name === f.moves_asset_to) && <option value={f.moves_asset_to}>{f.moves_asset_to}</option>}
                    </select>
                  </div>
                </div>
                <div>
                  <div className={lbl}>Needs before a job can move here</div>
                  <div className="flex gap-2 flex-wrap"><Pill k="require_comment" label="A comment" /><Pill k="require_payment" label="Full payment" /><Pill k="require_checklist" label="Checklist done" /></div>
                </div>
                {!isNew && <div className="text-[11px] text-ink-tertiary">Code used in reports and integrations: <span className="font-mono text-ink-secondary">{status.key}</span></div>}
              </div>
            )}
          </div>
        </div>

        <div className="px-5 py-4 border-t border-slate-100 flex items-center gap-2">
          <button onClick={onClose} className="ml-auto px-4 py-2 rounded-xl border border-slate-200 text-ink-secondary hover:bg-slate-50 font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-5 py-2 rounded-xl bg-ink-primary text-white font-semibold text-xs hover:opacity-90 disabled:opacity-60">{busy ? "Saving…" : isNew ? "Add status" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
