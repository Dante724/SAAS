import SkeletonRows from "../../components/SkeletonRows";
import TeklySelect from "../../components/TeklySelect";
import { useEffect, useRef, useState } from "react";
import Shell, { M } from "../../components/Shell";
import EmptyState from "../../components/EmptyState";
import DateField from "../../components/DateField";
import { api } from "../../api/client";
import { useMe, hasFeature } from "../../context/Me";
import Pager from "../../components/Pager";
import JobPicker from "../../components/JobPicker";
import { loadFailed } from "../../lib/notice";
import { clickableRow } from "../../lib/clickable";
import { buttonClass } from "../../components/Button";
import { Badge } from "../../components/Card";
import { theadRowClass } from "../../components/Table";
import { serverDate, wallDate } from "../../lib/serverTime";
import { type Task, type StatusFilter, STATUS_LABEL, normStatus, statusLabel, normPriority, PRIORITY_LABEL, isAuto, applyFilters } from "./taskModel";

type Emp = { id: string; name: string };
type Feed = { id: string; body?: string; kind?: string; author_name?: string; created_at?: string };
type ListReply = { items?: Task[]; statuses?: string[]; total?: number };
type Toast = { msg: string; undo?: () => void };

// Due dates are shop wall-clock times; feed/completion stamps are instants.
const fmtWall = (iso?: string | null) => { const d = wallDate(iso); return d ? d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—"; };
const fmtAt = (iso?: string | null) => { const d = serverDate(iso); return d ? d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—"; };
const TASK_PAGE = 50;
const STATUS_PILLS: [StatusFilter, string][] = [["all", "All"], ["todo", "To do"], ["in_progress", "In progress"], ["overdue", "Late"], ["done", "Done"]];
const pill = (on: boolean) => `px-3 py-1.5 rounded-full text-2xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed ${on ? "bg-primary text-white shadow-xs" : "bg-slate-100 text-ink-secondary hover:bg-slate-200"}`;

function PriorityBadge({ p }: { p: Task["priority"] }) {
  const n = normPriority(p);
  return n ? <Badge tone={n === 2 ? "danger" : "warning"} size="sm">{PRIORITY_LABEL[n]}</Badge> : null;
}

export default function TasksHub() {
  const { me, can } = useMe();
  const clockOk = hasFeature(me, "time_clock");   // task timer: Shop plan
  const isManager = can("can_assign_staff");      // seeing everyone's tasks is a manager view
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);   // set only by the paged (newer) API
  const [scope, setScope] = useState<"mine" | "everyone">("mine");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [q, setQ] = useState("");
  const [qDeb, setQDeb] = useState("");
  const [person, setPerson] = useState("");
  const [prio, setPrio] = useState("");
  const [taskPage, setTaskPage] = useState(1);
  const [emps, setEmps] = useState<Emp[]>([]);
  const [sel, setSel] = useState<Task | null>(null);
  const [draft, setDraft] = useState({ title: "", description: "" });
  const [confirmDel, setConfirmDel] = useState(false);
  const [feed, setFeed] = useState<Feed[]>([]);
  const [comment, setComment] = useState("");
  const [create, setCreate] = useState(false);
  const [form, setForm] = useState<any>({ title: "", assignee_ids: [] as string[], priority: 0, due_at: "", order_id: "" });
  const [toast, setToast] = useState<Toast | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  const flash = (msg: string, undo?: () => void) => {
    clearTimeout(toastTimer.current);
    setToast({ msg, undo });
    toastTimer.current = setTimeout(() => setToast(null), undo ? 6000 : 2400);
  };
  const paged = total !== null;
  const reqId = useRef(0);

  useEffect(() => { const h = setTimeout(() => setQDeb(q.trim()), 250); return () => clearTimeout(h); }, [q]);
  useEffect(() => { setTaskPage(1); }, [filter, scope, qDeb, person, prio]);

  // The server filter for status only once we know the server pages (older API reads `status` literally).
  const serverStatus = paged ? filter : "all";
  function listUrl() {
    const p = new URLSearchParams();
    if (qDeb) p.set("q", qDeb);
    if (scope === "mine") p.set("mine", "true");                  // older API: assigned to me or made by me
    if (person) p.set("assignee", person); else if (scope === "mine") p.set("assignee", "me");
    if (serverStatus === "overdue") p.set("overdue", "true");
    else if (serverStatus !== "all") p.set("status", serverStatus);
    p.set("limit", String(TASK_PAGE));                             // ignored by the older API (full list)
    p.set("offset", String((taskPage - 1) * TASK_PAGE));
    return `/tasks?${p.toString()}`;
  }
  function load() {
    const id = ++reqId.current;
    api.get<ListReply>(listUrl()).then((r) => {
      if (id !== reqId.current) return;
      setTasks(r.items || []);
      setTotal(typeof r.total === "number" ? r.total : null);
    }).catch(() => { if (id === reqId.current) setTasks([]); });
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [scope, qDeb, person, serverStatus, taskPage]);
  useEffect(() => { api.get<any>("/staff/employees").then((r) => setEmps(r.items || r || [])).catch(loadFailed("the staff list")); }, []);

  function show(d: Task) { setSel(d); setDraft({ title: d.title || "", description: d.description || "" }); }
  function openTask(t: Task) {
    setConfirmDel(false);
    api.get<Task>(`/tasks/${t.id}`).then(show).catch(() => show(t));
    api.get<{ items: Feed[] }>(`/tasks/${t.id}/feed`).then((r) => setFeed(r.items || [])).catch(() => setFeed([]));
  }
  async function patch(id: string, body: any, note?: string) {
    try { await api.patch(`/tasks/${id}`, body); if (note) flash(note); load(); if (sel?.id === id) openTask({ id } as Task); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  async function toggleDone(t: Task) { patch(t.id, { status: t.completed_at ? "todo" : "done" }); }
  function saveText(field: "title" | "description") {
    if (!sel) return;
    const v = field === "title" ? draft.title.trim() : draft.description;
    if (field === "title" && !v) { setDraft((d) => ({ ...d, title: sel.title || "" })); flash("Title required"); return; }
    if (v === ((sel[field] as string | null | undefined) || "")) return;
    patch(sel.id, { [field]: v }, field === "title" ? "Title saved" : "Description saved");
  }
  async function restore(id: string) {
    try { await api.post(`/tasks/${id}/restore`, {}); flash("Task restored"); load(); }
    catch { flash("This task can't be brought back"); }
  }
  async function deleteTask(t: Task) {
    try {
      const r = await api.del<any>(`/tasks/${t.id}`);
      setSel(null); setConfirmDel(false); load();
      // Undo needs the soft-delete API: it pages (`total`) or says the task was archived.
      const soft = paged || !!(r && (r.archived || r.archived_at || r.deleted_at || r.restorable));
      flash("Task deleted", soft ? () => restore(t.id) : undefined);
    } catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  async function postComment(id: string) {
    const b = comment.trim(); if (!b) return;
    try { await api.post(`/tasks/${id}/comments`, { body: b }); setComment(""); api.get<{ items: Feed[] }>(`/tasks/${id}/feed`).then((r) => setFeed(r.items || [])); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  async function startTimer(id: string) {
    try { await api.post(`/timeclock/start?switch=true`, { task_id: id, category: "task" }); flash("Timer started on this task"); }
    catch (e: any) { flash(e.message?.includes("turned off") ? "Work-order timer is off in Settings" : `Could not start: ${e.message}`); }
  }
  async function createTask() {
    if (!form.title.trim()) { flash("Title required"); return; }
    try { await api.post("/tasks", { title: form.title.trim(), assignee_ids: form.assignee_ids, assigned_to_id: form.assignee_ids[0] || null, priority: Number(form.priority), due_at: form.due_at || null, order_id: form.order_id || null }); setCreate(false); setForm({ title: "", assignee_ids: [], priority: 0, due_at: "", order_id: "" }); load(); flash("Task created"); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  const selIds = (sel?.assignee_ids && sel.assignee_ids.length ? sel.assignee_ids : (sel?.assigned_to_id ? [sel.assigned_to_id] : []));
  function toggleAssignee(id: string) {
    if (!sel) return;
    const next = selIds.includes(id) ? selIds.filter((x) => x !== id) : [...selIds, id];
    patch(sel.id, { assignee_ids: next }, "Assignees updated");
  }

  // Idempotent client-side pass: filters the full list from the older API, changes nothing the newer one already filtered.
  const shown = applyFilters(tasks || [], { me: me?.id, scope, status: filter, q: qDeb, assignee: person, priority: prio });
  const count = paged ? (total as number) : shown.length;
  const taskPages = Math.max(1, Math.ceil(count / TASK_PAGE));
  const page = Math.min(taskPage, taskPages);
  const pageTasks = paged ? shown : shown.slice((page - 1) * TASK_PAGE, page * TASK_PAGE);
  const narrowed = filter !== "all" || !!qDeb || !!person || prio !== "";
  const clearFilters = () => { setFilter("all"); setQ(""); setPerson(""); setPrio(""); };
  const inp = "glass-input w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg";
  const nameOf = (id: string) => emps.find((x) => x.id === id)?.name || "?";

  return (
    <Shell title="Tasks">
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={() => setCreate(true)} className="h-[38px] px-4 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-sm" />New task</button>
          <div role="group" aria-label="Whose tasks" className="flex items-center p-0.5 rounded-full bg-slate-100 ml-1">
            <button onClick={() => setScope("mine")} aria-pressed={scope === "mine"} className={`px-3 py-1 rounded-full text-2xs font-semibold ${scope === "mine" ? "bg-white text-ink-primary shadow-xs" : "text-ink-secondary hover:text-ink-primary"}`}>Mine</button>
            <button onClick={() => setScope("everyone")} aria-pressed={scope === "everyone"} disabled={!isManager}
              title={isManager ? undefined : "Only managers can see everyone's tasks"}
              className={`px-3 py-1 rounded-full text-2xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed ${scope === "everyone" ? "bg-white text-ink-primary shadow-xs" : "text-ink-secondary hover:text-ink-primary"}`}>Everyone</button>
          </div>
          <div className="flex items-center gap-1.5">
            {STATUS_PILLS.map(([id, label]) => (
              <button key={id} onClick={() => setFilter(id)} aria-pressed={filter === id} className={pill(filter === id)}>{label}</button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-2 flex-wrap">
            <div className="relative w-full sm:w-64"><M n="search" c="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" />
              <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search tasks" placeholder="Search task, work order or client…" className="glass-input w-full h-9 rounded-xl pl-8 pr-3 py-2 text-xs text-ink-primary" /></div>
            <TeklySelect value={person} onChange={(e) => setPerson(e.target.value)} aria-label="Filter by person" className="glass-input h-9 py-2 px-3 text-xs rounded-xl min-w-[140px]">
              <option value="">Anyone</option>
              {emps.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </TeklySelect>
            <TeklySelect value={prio} onChange={(e) => setPrio(e.target.value)} aria-label="Filter by priority" className="glass-input h-9 py-2 px-3 text-xs rounded-xl min-w-[120px]">
              <option value="">Any priority</option>
              <option value="0">Normal</option><option value="1">High</option><option value="2">Urgent</option>
            </TeklySelect>
          </div>
        </div>
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          {/* Fixed column widths on desktop: switching filters never re-sizes the columns. */}
          <table className="w-full text-left border-collapse text-xs xl:table-fixed">
            <thead><tr className={theadRowClass}>
              <th className="py-3 pl-5 pr-2 w-10 xl:w-12"></th><th className="py-3 px-3">Task</th><th className="py-3 px-3 xl:w-32">Status</th><th className="py-3 px-3 xl:w-56">Links</th><th className="py-3 px-3 xl:w-40">Assigned to</th><th className="py-3 px-3 xl:w-32">Due</th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
              {!tasks && <SkeletonRows cols={6} />}
              {shown.length === 0 && tasks && <tr><td colSpan={6} className="py-10 px-4 text-center text-ink-tertiary">
                <p>{narrowed ? "No tasks match these filters." : scope === "mine" ? "No tasks for you." : "No tasks here."}</p>
                <div className="mt-3 flex items-center justify-center gap-2">
                  <button onClick={() => setCreate(true)} className="h-9 px-4 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 inline-flex items-center gap-1.5"><M n="add" c="text-sm" />New task</button>
                  {narrowed && <button onClick={clearFilters} className="h-9 px-4 rounded-xl bg-white border border-slate-200 text-xs font-bold text-ink-secondary hover:bg-slate-50">Clear filters</button>}
                </div>
              </td></tr>}
              {pageTasks.map((t) => (
                <tr key={t.id} className="hover:bg-blue-50/30 cursor-pointer" {...clickableRow(() => openTask(t))}>
                  <td className="py-3 pl-5 pr-2" onClick={(e) => e.stopPropagation()}><button onClick={() => toggleDone(t)} aria-label={t.completed_at ? `Mark "${t.title}" not done` : `Mark "${t.title}" done`} aria-pressed={!!t.completed_at} className={`w-6 h-6 rounded-md border-2 flex items-center justify-center ${t.completed_at ? "bg-emerald-500 border-emerald-500 text-white" : "border-slate-300 hover:border-primary"}`}>{t.completed_at && <M n="check" c="text-xs" />}</button></td>
                  <td className="py-3 px-3"><span className="inline-flex items-center gap-1.5 flex-wrap">
                    <span className={`font-bold ${t.completed_at ? "text-ink-tertiary line-through" : "text-ink-primary"}`}>{t.title}</span>
                    {isAuto(t) && <Badge tone="neutral" size="sm" className="gap-0.5"><M n="bolt" c="text-2xs" />Auto</Badge>}
                    {!t.completed_at && <PriorityBadge p={t.priority} />}
                  </span></td>
                  <td className="py-3 px-3"><Badge tone={t.completed_at ? "success" : normStatus(t.status) === "in_progress" ? "warning" : "neutral"} size="sm">{statusLabel(t)}</Badge></td>
                  <td className="py-3 px-3">{t.order_id || t.client_name ? <span className="inline-flex items-center gap-1.5 min-w-0 max-w-full">
                    {t.order_id && <Badge tone="info" size="sm" className="gap-1 flex-shrink-0"><M n="handyman" c="text-2xs" />{t.order_number || "WO"}</Badge>}
                    {t.client_name && <span className="truncate text-ink-secondary" title={t.client_name}>{t.client_name}</span>}
                  </span> : "—"}</td>
                  <td className="py-3 px-3">{(t.assignee_names && t.assignee_names.length) ? (t.assignee_names.length > 2 ? `${t.assignee_names.slice(0, 2).join(", ")} +${t.assignee_names.length - 2}` : t.assignee_names.join(", ")) : (t.assignee_name || "—")}</td>
                  <td className={`py-3 px-3 ${t.overdue && !t.completed_at ? "text-rose-600 font-bold" : ""}`}>{fmtWall(t.due_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {tasks && count > 0 && <Pager total={count} page={page} pageSize={TASK_PAGE} onPage={setTaskPage} noun="tasks" />}
        </div>
      </div>

      {/* task detail drawer */}
      {sel && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
          <div className="absolute inset-0 bg-ink-primary/35" aria-hidden="true" onClick={() => setSel(null)} />
          <div role="dialog" aria-label={`Task: ${sel.title || ""}`} className="relative h-full w-full max-w-[480px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
            <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
            <header className="px-6 py-4 border-b border-slate-100 flex items-start justify-between gap-2 bg-slate-50/70">
              <div className="flex-1 min-w-0">
                <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} onBlur={() => saveText("title")}
                  onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") setDraft({ ...draft, title: sel.title || "" }); }}
                  aria-label="Task title" className="w-full bg-transparent text-base font-bold text-ink-primary rounded-md px-1 -mx-1 hover:bg-white focus:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40" />
                <p className="text-2xs text-ink-tertiary mt-0.5 flex items-center gap-1.5">{sel.completed_at ? `Completed ${fmtAt(sel.completed_at)}` : `Due ${fmtWall(sel.due_at)}`}{isAuto(sel) && <Badge tone="neutral" size="sm" className="gap-0.5"><M n="bolt" c="text-2xs" />Auto</Badge>}</p>
              </div>
              <button aria-label="Close" onClick={() => setSel(null)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary flex-shrink-0"><M n="close" c="text-base" /></button>
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
                  <DateField mode="datetime" value={sel.due_at ? sel.due_at.slice(0, 16) : ""} onChange={(v) => patch(sel.id, { due_at: v || "" }, v ? "Due date saved" : "Due date cleared")} /></div>
                <div><label htmlFor="task-prio" className="block font-semibold text-ink-tertiary mb-1">Priority</label>
                  <TeklySelect id="task-prio" aria-label="Priority" value={normPriority(sel.priority)} onChange={(e) => patch(sel.id, { priority: Number(e.target.value) }, "Priority saved")} className={inp}>
                    <option value={0}>Normal</option><option value={1}>High</option><option value={2}>Urgent</option>
                  </TeklySelect></div>
              </div>
              <div>
                <label className="block font-semibold text-ink-tertiary mb-1">Assigned technicians</label>
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
                      <li key={s.id}><button onClick={() => openTask(s)} className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-slate-50">
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
                  <button onClick={() => postComment(sel.id)} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:brightness-110">Post</button>
                </div>
                <div className="space-y-2.5">
                  {feed.length === 0 && <EmptyState size="inline" title="No activity yet." />}
                  {feed.map((f) => (
                    <div key={f.id} className="flex gap-2.5">
                      <div className={`w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center text-white text-2xs font-bold ${f.kind === "comment" ? "bg-primary" : "bg-slate-400"}`}>{f.kind === "comment" ? (f.author_name || "?").slice(0, 1).toUpperCase() : <M n="bolt" c="text-xs" />}</div>
                      <div className="flex-1 min-w-0"><div className="text-xs text-ink-primary">{f.body}</div><div className="text-2xs text-ink-tertiary">{f.author_name || "System"}{f.kind !== "comment" ? " · system" : ""} · {fmtAt(f.created_at)}</div></div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="p-4 border-t border-slate-100 bg-slate-50/70 flex items-center gap-2">
              {confirmDel ? (<>
                <span className="text-xs font-semibold text-ink-primary">Delete this task?</span>
                <button autoFocus onClick={() => deleteTask(sel)} className={buttonClass("dangerSolid", "md")}>Delete</button>
                <button onClick={() => setConfirmDel(false)} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs">Cancel</button>
              </>) : (
                <button onClick={() => setConfirmDel(true)} className={buttonClass("danger", "md")}><M n="delete" c="text-sm" />Delete task</button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* new task drawer */}
      {create && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 55 }}>
          <div className="absolute inset-0 bg-ink-primary/35" aria-hidden="true" onClick={() => setCreate(false)} />
          <div role="dialog" aria-label="New task" className="relative h-full w-full max-w-[440px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
            <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70"><h3 className="text-base font-bold text-ink-primary">New task</h3><button aria-label="Close" onClick={() => setCreate(false)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
            <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
              <div><label htmlFor="new-task-title" className="block font-semibold text-ink-secondary mb-1">Title <span className="text-rose-500">*</span></label><input id="new-task-title" autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="glass-input w-full px-3 py-2.5 rounded-lg text-sm font-semibold" /></div>
              <div>
                <label className="block font-semibold text-ink-secondary mb-1">Assign to <span className="text-ink-tertiary font-normal">(one or more)</span></label>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {form.assignee_ids.length === 0 && <span className="text-ink-tertiary text-xs">Unassigned</span>}
                  {form.assignee_ids.map((id: string) => <span key={id} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-50 text-primary border border-blue-200 text-2xs font-semibold">{nameOf(id)}<button aria-label={`Remove ${nameOf(id)}`} onClick={() => setForm({ ...form, assignee_ids: form.assignee_ids.filter((x: string) => x !== id) })} className="rounded-full hover:text-rose-600"><M n="close" c="text-2xs" /></button></span>)}
                </div>
                <div className="max-h-32 overflow-y-auto rounded-lg border border-slate-200 divide-y divide-slate-100">
                  {emps.map((e) => (
                    <label key={e.id} className="flex items-center gap-2 px-2.5 py-1.5 cursor-pointer hover:bg-slate-50 text-xs">
                      <input type="checkbox" checked={form.assignee_ids.includes(e.id)} onChange={() => setForm({ ...form, assignee_ids: form.assignee_ids.includes(e.id) ? form.assignee_ids.filter((x: string) => x !== e.id) : [...form.assignee_ids, e.id] })} className="w-3.5 h-3.5 rounded text-primary border-slate-300" />
                      <span className="text-ink-primary font-medium">{e.name}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div><label className="block font-semibold text-ink-secondary mb-1">Priority</label><TeklySelect aria-label="New task priority" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className={inp}><option value={0}>Normal</option><option value={1}>High</option><option value={2}>Urgent</option></TeklySelect></div>
              <div><label className="block font-semibold text-ink-secondary mb-1">Link to work order <span className="text-ink-tertiary font-normal">(optional)</span></label><JobPicker value={form.order_id ? { id: form.order_id, order_number: form.order_number } : null} onChange={(j) => setForm({ ...form, order_id: j?.id || "", order_number: j?.order_number || "" })} /></div>
              <div><label className="block font-semibold text-ink-secondary mb-1">Due</label><DateField mode="datetime" value={form.due_at} onChange={(v) => setForm({ ...form, due_at: v })} /></div>
            </div>
            <div className="p-4 border-t border-slate-100 bg-slate-50/70 flex items-center gap-2"><button onClick={createTask} className={buttonClass("primary", "md")}>Create task</button><button onClick={() => setCreate(false)} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs">Cancel</button></div>
          </div>
        </div>
      )}
      {toast && <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold pl-4 pr-2 py-2 rounded-xl shadow-clay-drawer z-[70] flex items-center gap-3">
        <span className="py-0.5">{toast.msg}</span>
        {toast.undo && <button onClick={() => { const u = toast.undo!; setToast(null); u(); }} className="px-2.5 py-1 rounded-lg text-blue-200 font-bold hover:bg-white/10">Undo</button>}
      </div>}
    </Shell>
  );
}
