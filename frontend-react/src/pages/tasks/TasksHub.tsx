import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api } from "../../api/client";
import { useMe, hasFeature } from "../../context/Me";
import Pager from "../../components/Pager";
import JobPicker from "../../components/JobPicker";
import { loadFailed } from "../../lib/notice";
import { clickableRow } from "../../lib/clickable";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Button from "../../components/Button";
import Tabs from "../../components/Tabs";
import Card, { Badge, type BadgeTone } from "../../components/Card";
import Drawer from "../../components/Drawer";
import EmptyState, { TableEmpty } from "../../components/EmptyState";
import { Field, Input, Select, labelClass } from "../../components/Field";
import { tableClass, theadRowClass, Th, Td } from "../../components/Table";

type Task = { id: string; title?: string; description?: string; status?: string; priority?: number; assigned_to_id?: string | null; assignee_name?: string; assignee_ids?: string[]; assignee_names?: string[]; due_at?: string | null; completed_at?: string | null; overdue?: boolean; order_id?: string | null; order_number?: string | null; client_name?: string };
type Emp = { id: string; name: string };
type Feed = { id: string; body?: string; kind?: string; author_name?: string; created_at?: string };

const fmt = (iso?: string | null) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");
const STATUS_LABEL: Record<string, string> = { todo: "To do", in_progress: "In progress", done: "Done" };

export default function TasksHub() {
  const clockOk = hasFeature(useMe().me, "time_clock");   // task timer: Shop plan
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [filter, setFilter] = useState("all");
  const [emps, setEmps] = useState<Emp[]>([]);
  const [sel, setSel] = useState<Task | null>(null);
  const [feed, setFeed] = useState<Feed[]>([]);
  const [comment, setComment] = useState("");
  const [create, setCreate] = useState(false);
  const [form, setForm] = useState<any>({ title: "", assignee_ids: [] as string[], priority: 0, due_at: "", order_id: "" });
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2400); };

  function load() { api.get<{ items: Task[]; statuses: string[] }>("/tasks").then((r) => { setTasks(r.items || []); setStatuses(r.statuses || []); }).catch(() => setTasks([])); }
  useEffect(() => { load(); api.get<any>("/staff/employees").then((r) => setEmps(r.items || r || [])).catch(loadFailed("the staff list")); }, []);

  function openTask(t: Task) {
    api.get<Task>(`/tasks/${t.id}`).then((d) => { setSel(d); }).catch(() => setSel(t));
    api.get<{ items: Feed[] }>(`/tasks/${t.id}/feed`).then((r) => setFeed(r.items || [])).catch(() => setFeed([]));
  }
  async function patch(id: string, body: any, note?: string) {
    try { await api.patch(`/tasks/${id}`, body); if (note) flash(note); load(); if (sel?.id === id) openTask({ id } as Task); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  async function toggleDone(t: Task) { patch(t.id, { status: t.completed_at ? "todo" : "done" }); }
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

  const shown = (tasks || []).filter((t) => filter === "all" || (filter === "overdue" ? t.overdue : filter === "done" ? !!t.completed_at : !t.completed_at && t.status === filter));
  const TASK_PAGE = 50;
  const [taskPage, setTaskPage] = useState(1);
  useEffect(() => { setTaskPage(1); }, [filter]);
  const taskPages = Math.max(1, Math.ceil(shown.length / TASK_PAGE));
  const pageTasks = shown.slice((Math.min(taskPage, taskPages) - 1) * TASK_PAGE, Math.min(taskPage, taskPages) * TASK_PAGE);

  const STATUS_TONE = (t: Task): BadgeTone => t.completed_at ? "success" : t.status === "in_progress" ? "warning" : "neutral";
  // Until the list arrives, assume the standard statuses so the filter chips never shift when it does.
  const filterStatuses = tasks ? statuses : ["todo", "in_progress"];
  const filterTabs = [["all", "All"], ...filterStatuses.filter((s) => s !== "done").map((s) => [s, STATUS_LABEL[s] || humanize(s)] as [string, string]), ["done", "Done"], ["overdue", "Late"]]
    .map(([id, label]) => ({ id, label }));
  const assigneePill = (id: string, onRemove: () => void) => {
    const e = emps.find((x) => x.id === id);
    return <span key={id} className="inline-flex items-center gap-1 h-6 pl-2 pr-1 rounded-full bg-blue-50 text-primary text-2xs font-semibold">{e?.name || "?"}
      <button type="button" aria-label="Close" onClick={onRemove} className="hit44 rounded-full p-0.5 hover:text-rose-600"><M n="close" c="text-2xs" /></button></span>;
  };

  return (
    <Shell title="Tasks">
      <PageLayout>
        <Toolbar
          actions={<Button variant="primary" onClick={() => setCreate(true)} icon={<M n="add" c="text-sm" />}>New task</Button>}
          filters={<Tabs look="chips" label="Show tasks" value={filter} onChange={setFilter} tabs={filterTabs} />}
        />
        <Card padded={false}>
          {/* Fixed column widths on desktop: switching filters never re-sizes the columns. */}
          <table className={`${tableClass} xl:table-fixed`}>
            <thead><tr className={theadRowClass}>
              <Th className="w-10 xl:w-12 !pl-5 !pr-2"><span className="sr-only">Done</span></Th><Th className="!px-3">Task</Th><Th className="!px-3 xl:w-32">Status</Th><Th className="!px-3 xl:w-48">Links</Th><Th className="!px-3 xl:w-40">Assigned to</Th><Th className="!px-3 xl:w-32">Due</Th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
              {!tasks && <SkeletonRows cols={6} />}
              {shown.length === 0 && tasks && <TableEmpty cols={6} icon={<M n="task_alt" c="text-lg" />}
                title={filter === "all" ? "No tasks yet" : "No tasks here"}
                body={filter === "all" ? "Tasks you create or assign show up here." : "Nothing matches this filter."}
                action={filter !== "all" ? <Button onClick={() => setFilter("all")}>Show all tasks</Button> : undefined} />}
              {pageTasks.map((t) => (
                <tr key={t.id} className="hover:bg-blue-50/30 cursor-pointer" {...clickableRow(() => openTask(t))}>
                  <td className="py-3 pl-5 pr-2" onClick={(e) => e.stopPropagation()}><button type="button" onClick={() => toggleDone(t)} aria-label={t.completed_at ? `Mark "${t.title}" not done` : `Mark "${t.title}" done`} aria-pressed={!!t.completed_at} className={`hit44 w-6 h-6 rounded-md border-2 flex items-center justify-center ${t.completed_at ? "bg-emerald-500 border-emerald-500 text-white" : "border-slate-300 hover:border-primary"}`}>{t.completed_at && <M n="check" c="text-xs" />}</button></td>
                  <Td className="!px-3"><span className={`font-semibold ${t.completed_at ? "text-ink-tertiary line-through" : "text-ink-primary"}`}>{t.title}</span></Td>
                  <Td className="!px-3"><Badge tone={STATUS_TONE(t)}>{t.completed_at ? "Done" : STATUS_LABEL[t.status || "todo"]}</Badge></Td>
                  <Td className="!px-3">{t.order_id ? <Badge tone="info"><M n="handyman" c="text-2xs" />{t.order_number || "WO"}</Badge> : "—"}</Td>
                  <Td className="!px-3">{(t.assignee_names && t.assignee_names.length) ? (t.assignee_names.length > 2 ? `${t.assignee_names.slice(0, 2).join(", ")} +${t.assignee_names.length - 2}` : t.assignee_names.join(", ")) : (t.assignee_name || "—")}</Td>
                  <Td className={`!px-3 ${t.overdue ? "text-rose-600 font-semibold" : ""}`}>{fmt(t.due_at)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
          {tasks && shown.length > 0 && <Pager total={shown.length} page={Math.min(taskPage, taskPages)} pageSize={TASK_PAGE} onPage={setTaskPage} noun="tasks" />}
        </Card>
      </PageLayout>

      {/* task detail drawer */}
      {sel && (
        <Drawer size="sm" label="Task" title={sel.title} sub={sel.completed_at ? `Completed ${fmt(sel.completed_at)}` : `Due ${fmt(sel.due_at)}`} onClose={() => setSel(null)}>
          <div className="flex flex-wrap items-center gap-2">
            <Tabs look="segmented" label="Task status" value={sel.completed_at ? "done" : (sel.status || "todo")}
              onChange={(s) => patch(sel.id, { status: s }, `Marked ${STATUS_LABEL[s]}`)}
              tabs={["todo", "in_progress", "done"].map((s) => ({ id: s, label: STATUS_LABEL[s] }))} />
            {clockOk && <Button size="sm" className="ml-auto" onClick={() => startTimer(sel.id)} icon={<M n="play_arrow" c="text-sm" />}>Start timer</Button>}
          </div>
          {sel.description && <div className="p-3 bg-slate-50/80 rounded-xl border border-slate-200 text-xs leading-relaxed text-ink-primary">{sel.description}</div>}
          <div>
            <span className={labelClass}>Assigned technicians</span>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {selIds.length === 0 && <span className="text-xs text-ink-tertiary">Unassigned</span>}
              {selIds.map((id) => assigneePill(id, () => toggleAssignee(id)))}
            </div>
            <div className="max-h-32 overflow-y-auto rounded-xl border border-slate-200 divide-y divide-slate-100">
              {emps.map((e) => (
                <label key={e.id} className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-slate-50 text-xs">
                  <input type="checkbox" checked={selIds.includes(e.id)} onChange={() => toggleAssignee(e.id)} className="w-4 h-4 rounded text-primary border-slate-300" />
                  <span className="text-ink-primary font-medium">{e.name}</span>
                </label>
              ))}
            </div>
          </div>
          <div><span className={labelClass}>Linked work order</span>
            <JobPicker value={sel.order_id ? { id: sel.order_id, order_number: sel.order_number } : null}
              onChange={(j) => patch(sel.id, { order_id: j ? j.id : "" }, j ? "Work order linked" : "Work order unlinked")} />
          </div>
          {sel.client_name && <div className="text-xs text-ink-tertiary"><span className="font-semibold text-ink-secondary">Client:</span> {sel.client_name}</div>}

          {/* activity + comments */}
          <div className="pt-4 border-t border-slate-100">
            <h3 className="text-xs font-semibold text-ink-secondary mb-2">Activity &amp; comments</h3>
            <div className="flex items-center gap-2 mb-3">
              <Input value={comment} onChange={(e) => setComment(e.target.value)} onKeyDown={(e) => e.key === "Enter" && postComment(sel.id)} aria-label="Comment" placeholder="Add a comment…" />
              <Button variant="primary" onClick={() => postComment(sel.id)}>Post</Button>
            </div>
            <div className="space-y-2.5">
              {feed.length === 0 && <EmptyState size="inline" title="No activity yet." />}
              {feed.map((f) => (
                <div key={f.id} className="flex gap-2.5">
                  <div className={`w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center text-white text-2xs font-semibold ${f.kind === "comment" ? "bg-primary" : "bg-slate-400"}`}>{f.kind === "comment" ? (f.author_name || "?").slice(0, 1).toUpperCase() : <M n="bolt" c="text-xs" />}</div>
                  <div className="flex-1 min-w-0"><div className="text-xs text-ink-primary">{f.body}</div><div className="text-2xs text-ink-tertiary">{f.author_name || "System"}{f.kind !== "comment" ? " · system" : ""} · {fmt(f.created_at)}</div></div>
                </div>
              ))}
            </div>
          </div>
        </Drawer>
      )}

      {/* new task drawer */}
      {create && (
        <Drawer size="sm" z={55} label="New task" title="New task" onClose={() => setCreate(false)}
          footer={<><Button variant="primary" onClick={createTask}>Create task</Button><Button onClick={() => setCreate(false)}>Cancel</Button></>}>
          <Field label={<>Title <span className="text-rose-500">*</span></>} htmlFor="task-title"><Input id="task-title" autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <div>
            <span className={labelClass}>Assign to <span className="text-ink-tertiary font-normal">(one or more)</span></span>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {form.assignee_ids.length === 0 && <span className="text-ink-tertiary text-xs">Unassigned</span>}
              {form.assignee_ids.map((id: string) => assigneePill(id, () => setForm({ ...form, assignee_ids: form.assignee_ids.filter((x: string) => x !== id) })))}
            </div>
            <div className="max-h-32 overflow-y-auto rounded-xl border border-slate-200 divide-y divide-slate-100">
              {emps.map((e) => (
                <label key={e.id} className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-slate-50 text-xs">
                  <input type="checkbox" checked={form.assignee_ids.includes(e.id)} onChange={() => setForm({ ...form, assignee_ids: form.assignee_ids.includes(e.id) ? form.assignee_ids.filter((x: string) => x !== e.id) : [...form.assignee_ids, e.id] })} className="w-4 h-4 rounded text-primary border-slate-300" />
                  <span className="text-ink-primary font-medium">{e.name}</span>
                </label>
              ))}
            </div>
          </div>
          <Field label="Priority" htmlFor="task-priority"><Select id="task-priority" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}><option value={0}>Normal</option><option value={1}>High</option><option value={2}>Urgent</option></Select></Field>
          <div><span className={labelClass}>Link to work order <span className="text-ink-tertiary font-normal">(optional)</span></span><JobPicker value={form.order_id ? { id: form.order_id, order_number: form.order_number } : null} onChange={(j) => setForm({ ...form, order_id: j?.id || "", order_number: j?.order_number || "" })} /></div>
          <div><span className={labelClass}>Due</span><DateField mode="datetime" value={form.due_at} onChange={(v) => setForm({ ...form, due_at: v })} /></div>
        </Drawer>
      )}
      {toast && <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}
