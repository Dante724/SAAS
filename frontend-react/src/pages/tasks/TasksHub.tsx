import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api } from "../../api/client";
import Pager from "../../components/Pager";

type Task = { id: string; title?: string; description?: string; status?: string; priority?: number; assigned_to_id?: string | null; assignee_name?: string; assignee_ids?: string[]; assignee_names?: string[]; due_at?: string | null; completed_at?: string | null; overdue?: boolean; order_id?: string | null; order_number?: string | null; client_name?: string };
type Emp = { id: string; name: string };
type Feed = { id: string; body?: string; kind?: string; author_name?: string; created_at?: string };

const fmt = (iso?: string | null) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");
const STATUS_LABEL: Record<string, string> = { todo: "To do", in_progress: "In progress", done: "Done" };

export default function TasksHub() {
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
  useEffect(() => { load(); api.get<any>("/staff/employees").then((r) => setEmps(r.items || r || [])).catch(() => {}); }, []);

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
  const inp = "glass-input w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg";
  const TASK_PAGE = 50;
  const [taskPage, setTaskPage] = useState(1);
  useEffect(() => { setTaskPage(1); }, [filter]);
  const taskPages = Math.max(1, Math.ceil(shown.length / TASK_PAGE));
  const pageTasks = shown.slice((Math.min(taskPage, taskPages) - 1) * TASK_PAGE, Math.min(taskPage, taskPages) * TASK_PAGE);

  return (
    <Shell title="Tasks">
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={() => setCreate(true)} className="h-[38px] px-4 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-sm" />New task</button>
          <div className="flex items-center gap-1.5 ml-1">
            {[["all", "All"], ...statuses.filter((s) => s !== "done").map((s) => [s, STATUS_LABEL[s] || humanize(s)] as [string, string]), ["done", "Done"], ["overdue", "Late"]].map(([id, label]) => (
              <button key={id} onClick={() => setFilter(id)} className={`px-3 py-1.5 rounded-full text-[11px] font-semibold ${filter === id ? "bg-primary text-white shadow-xs" : "bg-slate-100 text-ink-secondary hover:bg-slate-200"}`}>{label}</button>
            ))}
          </div>
        </div>
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          <table className="w-full text-left border-collapse text-xs">
            <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold ">
              <th className="py-3 pl-5 pr-2 w-10"></th><th className="py-3 px-3">Task</th><th className="py-3 px-3">Status</th><th className="py-3 px-3">Links</th><th className="py-3 px-3">Assigned to</th><th className="py-3 px-3">Due</th>
            </tr></thead>
            <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
              {!tasks && <SkeletonRows cols={6} />}
              {shown.length === 0 && tasks && <tr><td colSpan={6} className="py-10 px-4 text-center text-ink-tertiary">No tasks here.</td></tr>}
              {pageTasks.map((t) => (
                <tr key={t.id} className="hover:bg-blue-50/30 cursor-pointer" onClick={() => openTask(t)}>
                  <td className="py-3 pl-5 pr-2" onClick={(e) => e.stopPropagation()}><button onClick={() => toggleDone(t)} className={`w-5 h-5 rounded-md border-2 flex items-center justify-center ${t.completed_at ? "bg-emerald-500 border-emerald-500 text-white" : "border-slate-300 hover:border-primary"}`}>{t.completed_at && <M n="check" c="text-xs" />}</button></td>
                  <td className="py-3 px-3"><span className={`font-bold ${t.completed_at ? "text-ink-tertiary line-through" : "text-ink-primary"}`}>{t.title}</span></td>
                  <td className="py-3 px-3"><span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${t.completed_at ? "bg-emerald-100 text-emerald-800" : t.status === "in_progress" ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-700"}`}>{t.completed_at ? "Done" : STATUS_LABEL[t.status || "todo"]}</span></td>
                  <td className="py-3 px-3">{t.order_id ? <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-blue-100 text-blue-800 inline-flex items-center gap-1"><M n="handyman" c="text-[11px]" />{t.order_number || "WO"}</span> : "—"}</td>
                  <td className="py-3 px-3">{(t.assignee_names && t.assignee_names.length) ? (t.assignee_names.length > 2 ? `${t.assignee_names.slice(0, 2).join(", ")} +${t.assignee_names.length - 2}` : t.assignee_names.join(", ")) : (t.assignee_name || "—")}</td>
                  <td className={`py-3 px-3 ${t.overdue ? "text-rose-600 font-bold" : ""}`}>{fmt(t.due_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {tasks && shown.length > 0 && <Pager total={shown.length} page={Math.min(taskPage, taskPages)} pageSize={TASK_PAGE} onPage={setTaskPage} noun="tasks" />}
        </div>
      </div>

      {/* task detail drawer */}
      {sel && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
          <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={() => setSel(null)} />
          <div className="relative h-full w-full max-w-[480px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
            <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
            <header className="px-6 py-4 border-b border-slate-100 flex items-start justify-between bg-slate-50/70">
              <div><h3 className="text-base font-bold text-ink-primary pr-4">{sel.title}</h3><p className="text-[11px] text-ink-tertiary mt-0.5">{sel.completed_at ? `Completed ${fmt(sel.completed_at)}` : `Due ${fmt(sel.due_at)}`}</p></div>
              <button aria-label="Close" onClick={() => setSel(null)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary flex-shrink-0"><M n="close" c="text-base" /></button>
            </header>
            <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
              {/* status buttons */}
              <div className="flex items-center gap-1.5">
                {["todo", "in_progress", "done"].map((s) => (
                  <button key={s} onClick={() => patch(sel.id, { status: s }, `Marked ${STATUS_LABEL[s]}`)} className={`px-3 py-1.5 rounded-lg text-[11px] font-bold ${(sel.completed_at ? "done" : sel.status) === s ? "bg-primary text-white shadow-clay-sm" : "bg-slate-100 text-ink-secondary hover:bg-slate-200"}`}>{STATUS_LABEL[s]}</button>
                ))}
                <button onClick={() => startTimer(sel.id)} className="ml-auto px-3 py-1.5 rounded-lg bg-emerald-50 text-emerald-700 border border-emerald-200 text-[11px] font-bold flex items-center gap-1 hover:bg-emerald-100"><M n="play_arrow" c="text-sm" />Start timer</button>
              </div>
              {sel.description && <div className="p-3 bg-slate-50/80 rounded-xl border border-slate-200 text-xs leading-relaxed text-ink-primary">{sel.description}</div>}
              <div>
                <label className="block font-semibold text-ink-tertiary mb-1">Assigned technicians</label>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {selIds.length === 0 && <span className="text-ink-tertiary">Unassigned</span>}
                  {selIds.map((id) => { const e = emps.find((x) => x.id === id); return <span key={id} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-50 text-primary border border-blue-200 text-[11px] font-semibold">{e?.name || "?"}<button aria-label="Close" onClick={() => toggleAssignee(id)} className="hover:text-rose-600"><M n="close" c="text-[11px]" /></button></span>; })}
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
                <div className="flex items-center gap-2">
                  <input defaultValue={sel.order_id || ""} onBlur={(e) => { if (e.target.value !== (sel.order_id || "")) patch(sel.id, { order_id: e.target.value || null }, "Work order linked"); }} className={`${inp} font-mono`} placeholder="Work order id" />
                  {sel.order_number && <span className="px-2 py-1 rounded-lg bg-blue-100 text-blue-800 text-[11px] font-bold whitespace-nowrap">{sel.order_number}</span>}
                </div>
              </div>
              {sel.client_name && <div className="text-[11px] text-ink-tertiary"><b className="text-ink-secondary">Client:</b> {sel.client_name}</div>}

              {/* activity + comments */}
              <div className="pt-2 border-t border-slate-100">
                <div className="text-[11px] font-bold text-ink-tertiary mb-2">Activity &amp; comments</div>
                <div className="flex items-center gap-2 mb-3">
                  <input value={comment} onChange={(e) => setComment(e.target.value)} onKeyDown={(e) => e.key === "Enter" && postComment(sel.id)} className={inp} placeholder="Add a comment…" />
                  <button onClick={() => postComment(sel.id)} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:brightness-110">Post</button>
                </div>
                <div className="space-y-2.5">
                  {feed.length === 0 && <p className="text-[11px] text-ink-tertiary">No activity yet.</p>}
                  {feed.map((f) => (
                    <div key={f.id} className="flex gap-2.5">
                      <div className={`w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center text-white text-[11px] font-bold ${f.kind === "comment" ? "bg-primary" : "bg-slate-400"}`}>{f.kind === "comment" ? (f.author_name || "?").slice(0, 1).toUpperCase() : <M n="bolt" c="text-xs" />}</div>
                      <div className="flex-1 min-w-0"><div className="text-xs text-ink-primary">{f.body}</div><div className="text-[11px] text-ink-tertiary">{f.author_name || "System"}{f.kind !== "comment" ? " · system" : ""} · {fmt(f.created_at)}</div></div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* new task drawer */}
      {create && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 55 }}>
          <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={() => setCreate(false)} />
          <div className="relative h-full w-full max-w-[440px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
            <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70"><h3 className="text-base font-bold text-ink-primary">New task</h3><button aria-label="Close" onClick={() => setCreate(false)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
            <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
              <div><label className="block font-semibold text-ink-secondary mb-1">Title <span className="text-rose-500">*</span></label><input autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="glass-input w-full px-3 py-2.5 rounded-lg text-sm font-semibold" /></div>
              <div>
                <label className="block font-semibold text-ink-secondary mb-1">Assign to <span className="text-ink-tertiary font-normal">(one or more)</span></label>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {form.assignee_ids.length === 0 && <span className="text-ink-tertiary text-xs">Unassigned</span>}
                  {form.assignee_ids.map((id: string) => { const e = emps.find((x) => x.id === id); return <span key={id} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-50 text-primary border border-blue-200 text-[11px] font-semibold">{e?.name || "?"}<button aria-label="Close" onClick={() => setForm({ ...form, assignee_ids: form.assignee_ids.filter((x: string) => x !== id) })} className="hover:text-rose-600"><M n="close" c="text-[11px]" /></button></span>; })}
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
              <div><label className="block font-semibold text-ink-secondary mb-1">Priority</label><select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className={inp}><option value={0}>Normal</option><option value={1}>High</option><option value={2}>Urgent</option></select></div>
              <div><label className="block font-semibold text-ink-secondary mb-1">Link to work order <span className="text-ink-tertiary font-normal">(optional)</span></label><input value={form.order_id} onChange={(e) => setForm({ ...form, order_id: e.target.value })} className={`${inp} font-mono`} placeholder="Work order id" /></div>
              <div><label className="block font-semibold text-ink-secondary mb-1">Due</label><DateField mode="datetime" value={form.due_at} onChange={(v) => setForm({ ...form, due_at: v })} /></div>
            </div>
            <div className="p-4 border-t border-slate-100 bg-slate-50/70 flex items-center justify-end gap-2"><button onClick={() => setCreate(false)} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs">Cancel</button><button onClick={createTask} className="px-5 py-2 rounded-lg bg-primary text-white font-bold text-xs shadow-clay-sm">Create task</button></div>
          </div>
        </div>
      )}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}
