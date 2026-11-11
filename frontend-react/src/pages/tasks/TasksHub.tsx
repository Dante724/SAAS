import SkeletonRows from "../../components/SkeletonRows";
import TeklySelect from "../../components/TeklySelect";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import Tabs from "../../components/Tabs";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import Pager from "../../components/Pager";
import JobPicker from "../../components/JobPicker";
import { loadFailed } from "../../lib/notice";
import { clickableRow } from "../../lib/clickable";
import { buttonClass } from "../../components/Button";
import { Badge } from "../../components/Card";
import { theadRowClass } from "../../components/Table";
import { wallDate } from "../../lib/serverTime";
import { type Task, type StatusFilter, normStatus, statusLabel, normPriority, PRIORITY_LABEL, isAuto, applyFilters } from "./taskModel";
import TaskPanel, { type Emp } from "./TaskPanel";
import MyDay from "./MyDay";

type ListReply = { items?: Task[]; statuses?: string[]; total?: number };
type Toast = { msg: string; undo?: () => void; label?: string };
type View = "day" | "list";

// Due dates are shop wall-clock times.
const fmtWall = (iso?: string | null) => { const d = wallDate(iso); return d ? d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—"; };
const TASK_PAGE = 50;
const STATUS_PILLS: [StatusFilter, string][] = [["all", "All"], ["todo", "To do"], ["in_progress", "In progress"], ["overdue", "Late"], ["done", "Done"]];
const pill = (on: boolean) => `px-3 py-1.5 rounded-full text-2xs font-semibold disabled:opacity-40 disabled:cursor-not-allowed ${on ? "bg-primary text-white shadow-xs" : "bg-slate-100 text-ink-secondary hover:bg-slate-200"}`;
// Which view this browser last used (a per-viewer convenience; storage may be off).
const VIEW_KEY = "tekly.tasks.view";
const savedView = (): View => { try { return localStorage.getItem(VIEW_KEY) === "list" ? "list" : "day"; } catch { return "day"; } };

function PriorityBadge({ p }: { p: Task["priority"] }) {
  const n = normPriority(p);
  return n ? <Badge tone={n === 2 ? "danger" : "warning"} size="sm">{PRIORITY_LABEL[n]}</Badge> : null;
}

export default function TasksHub() {
  const { me, can } = useMe();
  const isManager = can("can_assign_staff");      // seeing everyone's tasks is a manager view
  const [view, setViewState] = useState<View>(savedView);
  const [dayMissing, setDayMissing] = useState(false);   // server has no /tasks/my-day yet
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
  const [seed, setSeed] = useState<Task | null>(null);
  const [dayReload, setDayReload] = useState(0);
  const [create, setCreate] = useState(false);
  const [form, setForm] = useState<any>({ title: "", assignee_ids: [] as string[], priority: 0, due_at: "", order_id: "" });
  const [toast, setToast] = useState<Toast | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  // A toast, optionally with one action button: "Undo" by default, or another label ("Try again").
  const flash = useCallback((msg: string, undo?: () => void, label?: string) => {
    clearTimeout(toastTimer.current);
    setToast({ msg, undo, label });
    toastTimer.current = setTimeout(() => setToast(null), undo ? 6000 : 2400);
  }, []);
  const paged = total !== null;
  const reqId = useRef(0);
  const shownView: View = dayMissing ? "list" : view;
  function setView(v: View) { setViewState(v); try { localStorage.setItem(VIEW_KEY, v); } catch { /* per-viewer convenience only */ } }
  const onDayMissing = useCallback(() => setDayMissing(true), []);

  // Deep link: /tasks?task=<id> opens that task's panel (on load, from the bell, or from another
  // page); closing the panel takes the parameter off again. Opening a row sets it too, so the URL
  // of an open task can be copied and shared.
  const [sp, setSp] = useSearchParams();
  const openId = sp.get("task");
  // Whose day (managers only): kept in the URL so the view can be shared. Until we know who is
  // signed in, and for anyone who isn't a manager, it is always "me" (the server refuses others').
  const askedFor = sp.get("assignee") || "me";
  const dayPerson = me && isManager ? askedFor : "me";
  const setDayPerson = useCallback((v: string) => { setSp((p) => { if (v === "me") p.delete("assignee"); else p.set("assignee", v); return p; }, { replace: true }); }, [setSp]);
  // A link to a task you can't open (gone, never existed, or not yours to see) gets one neutral
  // message, so it never confirms that a hidden task exists.
  const taskUnavailable = useCallback(() => { setSp((p) => { p.delete("task"); return p; }, { replace: true }); flash("This task isn't available"); }, [setSp, flash]);
  function openTask(t: Task) {
    setSeed(t);
    setSp((p) => { p.set("task", t.id); return p; });
  }
  const closeTask = useCallback(() => { setSp((p) => { p.delete("task"); return p; }, { replace: true }); }, [setSp]);

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
    if (shownView !== "list") return;
    const id = ++reqId.current;
    api.get<ListReply>(listUrl()).then((r) => {
      if (id !== reqId.current) return;
      setTasks(r.items || []);
      setTotal(typeof r.total === "number" ? r.total : null);
    }).catch(() => { if (id === reqId.current) setTasks([]); });
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [scope, qDeb, person, serverStatus, taskPage, shownView]);
  useEffect(() => { api.get<any>("/staff/employees").then((r) => setEmps(r.items || r || [])).catch(loadFailed("the staff list")); }, []);
  const changed = () => { load(); setDayReload((n) => n + 1); };

  async function patch(id: string, body: any, note?: string) {
    try { await api.patch(`/tasks/${id}`, body); if (note) flash(note); changed(); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  async function toggleDone(t: Task) { patch(t.id, { status: t.completed_at ? "todo" : "done" }); }
  async function createTask() {
    if (!form.title.trim()) { flash("Title required"); return; }
    try { await api.post("/tasks", { title: form.title.trim(), assignee_ids: form.assignee_ids, assigned_to_id: form.assignee_ids[0] || null, priority: Number(form.priority), due_at: form.due_at || null, order_id: form.order_id || null }); setCreate(false); setForm({ title: "", assignee_ids: [], priority: 0, due_at: "", order_id: "" }); changed(); flash("Task created"); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
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
      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-4">
        <Tabs label="Tasks views" value={shownView} onChange={setView} className="-mt-2"
          tabs={[{ id: "day", label: "My day", disabled: dayMissing, title: dayMissing ? "My day needs the latest server update. The list shows every task meanwhile." : undefined }, { id: "list", label: "List" }]} />
        {shownView === "day" && (
          <MyDay meId={me?.id} emps={emps} isManager={isManager} person={dayPerson} onPerson={setDayPerson} onOpen={openTask} flash={flash} onUnavailable={onDayMissing} onShowAll={() => setView("list")} reloadKey={dayReload} />
        )}
        {shownView === "list" && (<>
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
        </>)}
      </div>

      {openId && (
        <TaskPanel taskId={openId} seed={seed} emps={emps} onClose={closeTask} onUnavailable={taskUnavailable} onOpen={openTask} onChanged={changed} flash={flash}
          softDelete={(r) => paged || shownView === "day" || !!(r && (r.archived || r.archived_at || r.deleted_at || r.restorable))} />
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
        {toast.undo && <button onClick={() => { const u = toast.undo!; setToast(null); u(); }} className="px-2.5 py-1 rounded-lg text-blue-200 font-bold hover:bg-white/10">{toast.label || "Undo"}</button>}
      </div>}
    </Shell>
  );
}
