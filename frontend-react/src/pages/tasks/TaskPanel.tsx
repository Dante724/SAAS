import { useEffect, useRef, useState } from "react";
import TeklySelect from "../../components/TeklySelect";
import { M } from "../../components/Shell";
import EmptyState, { ErrorState } from "../../components/EmptyState";
import DateField from "../../components/DateField";
import JobPicker from "../../components/JobPicker";
import { api, ApiError } from "../../api/client";
import { useMe, hasFeature } from "../../context/Me";
import { buttonClass } from "../../components/Button";
import { Badge } from "../../components/Card";
import { useShopClock, fmtShop, toShopWall, fromShopWall } from "../../lib/shopTime";
import { type Task, STATUS_LABEL, normStatus, statusLabel, normPriority, isAuto, AUTO_LABEL, AUTO_HINT } from "./taskModel";

// The task panel (a right-hand drawer), shared by My day and the List view. It loads the task by
// id, so `/tasks?task=<id>` can open it straight from a link or a bell notification.

export type Emp = { id: string; name: string; position?: string | null; active?: boolean };
type Feed = { id: string; body?: string; kind?: string; author_name?: string; created_at?: string };

// Due dates are shop wall-clock times; feed/completion stamps are instants.
// Due and completion times are UTC instants from the server; the panel shows and edits them on
// the shop's clock (a picked time is sent with the shop's offset).
const atShop = (iso: string | null | undefined, tz: string) => fmtShop(iso, tz) || "—";

export default function TaskPanel({ taskId, seed, emps, onClose, onUnavailable, onOpen, onChanged, flash, softDelete }: {
  taskId: string;
  seed?: Task | null;                       // the row that was clicked, shown while the full task loads
  emps: Emp[];
  onClose: () => void;
  onUnavailable: () => void;                // the task is missing or not the viewer's to see

  onOpen: (t: Task) => void;                // open another task (a subtask)
  onChanged: () => void;                    // reload the list behind the panel
  flash: (msg: string, undo?: () => void) => void;
  softDelete: (reply: any) => boolean;      // whether this API can bring a deleted task back
}) {
  const { me } = useMe();
  const clockOk = hasFeature(me, "time_clock");   // task timer: Shop plan
  const { tz: shopTz, failed: tzFailed, retry: retryTz } = useShopClock();
  const tz = shopTz || "UTC";
  const [sel, setSel] = useState<Task | null>(seed && seed.id === taskId ? seed : null);

  const [draft, setDraft] = useState({ title: seed?.title || "", description: seed?.description || "" });
  const [confirmDel, setConfirmDel] = useState(false);
  const [feed, setFeed] = useState<Feed[]>([]);
  const [comment, setComment] = useState("");
  // Keyboard and screen-reader users land in the panel when it opens, and back where they were
  // when it closes.
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    box.current?.focus();
    return () => { if (back && document.contains(back)) back.focus(); };
  }, []);

  function show(d: Task) { setSel(d); setDraft({ title: d.title || "", description: d.description || "" }); }
  function load(id = taskId, first = false) {
    api.get<Task>(`/tasks/${id}`).then(show).catch((e) => {
      if (!first) return;
      // Missing and not-allowed read the same, and nothing about the task is shown (a deep link
      // never shows list data before the server says yes).
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) onUnavailable();
      else { flash("Couldn't open the task. Check your connection and try again."); onClose(); }
    });
    api.get<{ items: Feed[] }>(`/tasks/${id}/feed`).then((r) => setFeed(r.items || [])).catch(() => setFeed([]));
  }
  useEffect(() => {
    setConfirmDel(false); setComment(""); setFeed([]);
    if (seed && seed.id === taskId) show(seed); else setSel(null);
    load(taskId, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);
  useEffect(() => {
    const keys = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !confirmDel) { onClose(); return; }
      // Keep Tab inside the panel while it is open (it is a modal dialog).
      if (e.key !== "Tab" || !box.current) return;
      const f = [...box.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')].filter((el) => !el.closest("[hidden]"));
      if (!f.length) return;
      const firstEl = f[0], lastEl = f[f.length - 1];
      if (e.shiftKey && (document.activeElement === firstEl || document.activeElement === box.current)) { e.preventDefault(); lastEl.focus(); }
      else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [onClose, confirmDel]);

  async function patch(id: string, body: any, note?: string) {
    try { await api.patch(`/tasks/${id}`, body); if (note) flash(note); onChanged(); load(id); }
    catch { flash("Couldn't save the change. Try again."); }
  }
  function saveText(field: "title" | "description") {
    if (!sel) return;
    const v = field === "title" ? draft.title.trim() : draft.description;
    if (field === "title" && !v) { setDraft((d) => ({ ...d, title: sel.title || "" })); flash("Title required"); return; }
    if (v === ((sel[field] as string | null | undefined) || "")) return;
    patch(sel.id, { [field]: v }, field === "title" ? "Title saved" : "Description saved");
  }
  async function restore(id: string) {
    try { await api.post(`/tasks/${id}/restore`, {}); flash("Task restored"); onChanged(); }
    catch { flash("This task can't be brought back"); }
  }
  async function deleteTask(t: Task) {
    try {
      const r = await api.del<any>(`/tasks/${t.id}`);
      onClose(); onChanged();
      flash("Task deleted", softDelete(r) ? () => restore(t.id) : undefined);
    } catch { flash("Couldn't delete the task. Try again."); }
  }
  async function postComment(id: string) {
    const b = comment.trim(); if (!b) return;
    try { await api.post(`/tasks/${id}/comments`, { body: b }); setComment(""); api.get<{ items: Feed[] }>(`/tasks/${id}/feed`).then((r) => setFeed(r.items || [])).catch(() => setFeed([])); }
    catch { flash("Couldn't post the comment. Try again."); }
  }
  async function startTimer(id: string) {
    try { await api.post(`/timeclock/start?switch=true`, { task_id: id, category: "task" }); flash("Timer started on this task"); }
    catch (e: any) { flash(e.message?.includes("turned off") ? "Work-order timer is off in Settings" : "Couldn't start the timer. Try again."); }
  }
  const selIds = (sel?.assignee_ids && sel.assignee_ids.length ? sel.assignee_ids : (sel?.assigned_to_id ? [sel.assigned_to_id] : []));
  function toggleAssignee(id: string) {
    if (!sel) return;
    const next = selIds.includes(id) ? selIds.filter((x) => x !== id) : [...selIds, id];
    patch(sel.id, { assignee_ids: next }, "Assignees updated");
  }
  const inp = "glass-input w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg";
  const nameOf = (id: string) => emps.find((x) => x.id === id)?.name || sel?.assignee_names?.[selIds.indexOf(id)] || "?";

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/35" aria-hidden="true" onClick={onClose} />
      <div ref={box} tabIndex={-1} role="dialog" aria-modal="true" aria-label={`Task: ${sel?.title || ""}`} className="outline-none relative h-full w-full max-w-[480px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        {!sel || !shopTz ? (<>
          <header className="px-6 py-4 border-b border-slate-100 flex items-start justify-between gap-2 bg-slate-50/70">
            <span className="sk block h-5 w-48 rounded mt-1" aria-hidden="true" />
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary flex-shrink-0"><M n="close" c="text-base" /></button>
          </header>
          {tzFailed
            ? <ErrorState title="Couldn't reach your shop's clock" body="Due times can't be shown or changed until it loads." onRetry={retryTz} />
            : <div className="p-6 space-y-3" role="status"><span className="sr-only">Loading…</span><span className="sk block h-8 w-64 rounded" /><span className="sk block h-20 w-full rounded" /><span className="sk block h-8 w-full rounded" /></div>}
        </>) : (<>
          <header className="px-6 py-4 border-b border-slate-100 flex items-start justify-between gap-2 bg-slate-50/70">
            <div className="flex-1 min-w-0">
              <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} onBlur={() => saveText("title")}
                onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { e.stopPropagation(); setDraft({ ...draft, title: sel.title || "" }); } }}
                aria-label="Task title" className="w-full bg-transparent text-base font-bold text-ink-primary rounded-md px-1 -mx-1 hover:bg-white focus:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40" />
              <p className="text-2xs text-ink-tertiary mt-0.5 flex items-center gap-1.5">{sel.completed_at ? `Completed ${atShop(sel.completed_at, tz)}` : `Due ${atShop(sel.due_at, tz)}`}{isAuto(sel) && <span title={AUTO_HINT}><Badge tone="neutral" size="sm" className="gap-0.5"><M n="bolt" c="text-2xs" />{AUTO_LABEL}<span className="sr-only">: {AUTO_HINT}</span></Badge></span>}</p>
            </div>
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary flex-shrink-0"><M n="close" c="text-base" /></button>
          </header>
          <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
            {/* status buttons */}
            <div className="flex items-center gap-1.5">
              {(["todo", "in_progress", "done"] as const).map((s) => (
                <button key={s} onClick={() => patch(sel.id, { status: s }, `Marked ${STATUS_LABEL[s]}`)} aria-pressed={(sel.completed_at ? "done" : normStatus(sel.status)) === s} className={`px-3 py-1.5 rounded-lg text-2xs font-bold ${(sel.completed_at ? "done" : normStatus(sel.status)) === s ? "bg-primary text-white shadow-clay-sm" : "bg-slate-100 text-ink-secondary hover:bg-slate-200"}`}>{STATUS_LABEL[s]}</button>
              ))}
              {clockOk && <button onClick={() => startTimer(sel.id)} className="ml-auto px-3 py-1.5 rounded-lg bg-emerald-50 text-emerald-700 border border-emerald-200 text-2xs font-bold flex items-center gap-1 hover:bg-emerald-100"><M n="play_arrow" c="text-sm" />Start timer</button>}
            </div>
            <div><label htmlFor="task-desc" className="block font-semibold text-ink-tertiary mb-1">Description</label>
              <textarea id="task-desc" rows={3} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} onBlur={() => saveText("description")}
                placeholder="Add details…" className="glass-input w-full py-2 px-3 text-xs text-ink-primary rounded-lg leading-relaxed resize-y" /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="block font-semibold text-ink-tertiary mb-1">Due</label>
                <DateField mode="datetime" value={(toShopWall(sel.due_at, tz) || "").slice(0, 16)} onChange={(v) => patch(sel.id, { due_at: v ? fromShopWall(v, tz) : "" }, v ? "Due date saved" : "Due date cleared")} /></div>
              <div><label htmlFor="task-prio" className="block font-semibold text-ink-tertiary mb-1">Priority</label>
                <TeklySelect id="task-prio" aria-label="Priority" value={normPriority(sel.priority)} onChange={(e) => patch(sel.id, { priority: Number(e.target.value) }, "Priority saved")} className={inp}>
                  <option value={0}>Normal</option><option value={1}>High</option><option value={2}>Urgent</option>
                </TeklySelect></div>
            </div>
            <div>
              <label className="block font-semibold text-ink-tertiary mb-1">Assigned to</label>
              <div className="flex flex-wrap gap-1.5 mb-2">
                {selIds.length === 0 && <span className="text-ink-tertiary">Unassigned</span>}
                {selIds.map((id) => <span key={id} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-50 text-primary border border-blue-200 text-2xs font-semibold">{nameOf(id)}<button aria-label={`Remove ${nameOf(id)}`} onClick={() => toggleAssignee(id)} className="rounded-full hover:text-rose-600"><M n="close" c="text-2xs" /></button></span>)}
              </div>
              <div className="max-h-32 overflow-y-auto rounded-lg border border-slate-200 divide-y divide-slate-100">
                {emps.map((e) => (
                  <label key={e.id} className="flex items-center gap-2 px-2.5 py-1.5 cursor-pointer hover:bg-slate-50 text-xs">
                    <input type="checkbox" checked={selIds.includes(e.id)} onChange={() => toggleAssignee(e.id)} className="w-3.5 h-3.5 rounded text-primary border-slate-300" />
                    <span className="text-ink-primary font-medium">{e.name}</span>
                  </label>
                ))}
              </div>
            </div>
            <div><label className="block font-semibold text-ink-tertiary mb-1">Linked work order</label>
              <JobPicker value={sel.order_id ? { id: sel.order_id, order_number: sel.order_number } : null}
                onChange={(j) => patch(sel.id, { order_id: j ? j.id : "" }, j ? "Work order linked" : "Work order unlinked")} />
            </div>
            {sel.client_name && <div className="text-2xs text-ink-tertiary"><b className="text-ink-secondary">Client:</b> {sel.client_name}</div>}

            {/* subtasks */}
            {sel.subtasks && sel.subtasks.length > 0 && (
              <div>
                <div className="font-semibold text-ink-tertiary mb-1">Subtasks <span className="font-normal">({sel.subtasks.filter((s) => s.completed_at).length}/{sel.subtasks.length} done)</span></div>
                <ul className="rounded-lg border border-slate-200 divide-y divide-slate-100">
                  {sel.subtasks.map((s) => (
                    <li key={s.id}><button onClick={() => onOpen(s)} className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-slate-50">
                      <M n={s.completed_at ? "check_circle" : "radio_button_unchecked"} c={`text-sm ${s.completed_at ? "text-emerald-600" : "text-ink-tertiary"}`} />
                      <span className={`flex-1 min-w-0 truncate ${s.completed_at ? "line-through text-ink-tertiary" : "text-ink-primary font-medium"}`}>{s.title}</span>
                      <Badge tone={s.completed_at ? "success" : normStatus(s.status) === "in_progress" ? "warning" : "neutral"} size="sm">{statusLabel(s)}</Badge>
                    </button></li>
                  ))}
                </ul>
              </div>
            )}

            {/* activity + comments */}
            <div className="pt-2 border-t border-slate-100">
              <div className="text-2xs font-bold text-ink-tertiary mb-2">Activity &amp; comments</div>
              <div className="flex items-center gap-2 mb-3">
                <input value={comment} onChange={(e) => setComment(e.target.value)} onKeyDown={(e) => e.key === "Enter" && postComment(sel.id)} className={inp} aria-label="Comment" placeholder="Add a comment…" />
                <button onClick={() => postComment(sel.id)} className={buttonClass("primary", "md")}>Post</button>
              </div>
              <div className="space-y-2.5">
                {feed.length === 0 && <EmptyState size="inline" title="No activity yet." />}
                {feed.map((f) => (
                  <div key={f.id} className="flex gap-2.5">
                    <div className={`w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center text-white text-2xs font-bold ${f.kind === "comment" ? "bg-primary" : "bg-slate-400"}`}>{f.kind === "comment" ? (f.author_name || "?").slice(0, 1).toUpperCase() : <M n="bolt" c="text-xs" />}</div>
                    <div className="flex-1 min-w-0"><div className="text-xs text-ink-primary">{f.body}</div><div className="text-2xs text-ink-tertiary">{f.author_name || "System"}{f.kind !== "comment" ? " · system" : ""} · {atShop(f.created_at, tz)}</div></div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="p-4 border-t border-slate-100 bg-slate-50/70 flex items-center gap-2">
            {confirmDel ? (<>
              <span className="text-xs font-semibold text-ink-primary">Delete this task?</span>
              <button autoFocus onClick={() => deleteTask(sel)} className={buttonClass("dangerSolid", "md")}>Delete</button>
              <button onClick={() => setConfirmDel(false)} className={buttonClass("secondary", "md")}>Cancel</button>
            </>) : (
              <button onClick={() => setConfirmDel(true)} className={buttonClass("danger", "md")}><M n="delete" c="text-sm" />Delete task</button>
            )}
          </div>
        </>)}
      </div>
    </div>
  );
}
