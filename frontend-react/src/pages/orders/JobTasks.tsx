import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { M } from "../../components/Shell";
import { Avatar } from "./look";

// J4 — the job's tasks, on the job: add one, assign it, tick it off.
type Task = { id: string; title: string; status?: string; assigned_to_id?: string | null; assignee_name?: string | null; due_at?: string | null; completed_at?: string | null; overdue?: boolean };
type Emp = { id: string; name: string; in_assignees?: boolean };

export default function JobTasks({ orderId, emps, onFlash }: { orderId: string; emps: Emp[]; onFlash: (m: string, tone?: "warn") => void }) {
  const [items, setItems] = useState<Task[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [who, setWho] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.get<{ items: Task[] }>(`/tasks?order_id=${orderId}`).then((r) => setItems(r.items || [])).catch(() => setItems([]));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [orderId]);

  async function add() {
    const t = title.trim(); if (!t) return;
    setBusy(true);
    try {
      await api.post("/tasks", { title: t, order_id: orderId, assigned_to_id: who || null, assignee_ids: who ? [who] : [] });
      setTitle(""); setWho(""); setAdding(false); onFlash("Task added"); load();
    } catch (e: any) { onFlash(e.message || "Couldn't add the task", "warn"); }
    finally { setBusy(false); }
  }
  async function toggle(t: Task) {
    const done = !t.completed_at;
    setItems((xs) => (xs || []).map((x) => (x.id === t.id ? { ...x, completed_at: done ? new Date().toISOString() : null, status: done ? "done" : "todo" } : x)));
    try { await api.patch(`/tasks/${t.id}`, { status: done ? "done" : "todo" }); load(); }
    catch (e: any) { onFlash(e.message || "Couldn't update the task", "warn"); load(); }
  }
  async function assign(t: Task, id: string) {
    // clearing goes through the assignee list: an empty list leaves the task unassigned
    try { await api.patch(`/tasks/${t.id}`, id ? { assigned_to_id: id } : { assignee_ids: [] }); load(); }
    catch (e: any) { onFlash(e.message || "Couldn't assign the task", "warn"); }
  }

  const open = (items || []).filter((t) => !t.completed_at).length;
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 p-5 shadow-clay-sm space-y-3">
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => setAdding((v) => !v)}
          className="px-3 py-1.5 rounded-lg text-xs font-bold text-white bg-primary hover:opacity-90 flex items-center gap-1.5"><M n="add" c="text-xs" />Add task</button>
        <h4 className="text-sm font-semibold text-ink-primary ml-1">Tasks</h4>
        {items && items.length > 0 && <span className="text-[11px] text-ink-tertiary">{open ? `${open} open` : "All done"}</span>}
      </div>
      {adding && (
        <div className="flex flex-wrap items-center gap-2">
          <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") add(); }}
            aria-label="Task" placeholder="What needs doing? e.g. Order the screen" className="glass-input flex-1 min-w-[200px] py-2 px-3 text-xs rounded-xl" />
          <select value={who} onChange={(e) => setWho(e.target.value)} aria-label="Assign to" className="glass-input py-2 px-2 text-xs rounded-xl">
            <option value="">Nobody yet</option>
            {emps.filter((e) => e.in_assignees !== false).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
          <button type="button" onClick={add} disabled={busy || !title.trim()} className="px-3 py-2 rounded-xl text-xs font-bold text-white bg-primary disabled:opacity-50">{busy ? "Adding…" : "Add"}</button>
          <button type="button" onClick={() => { setAdding(false); setTitle(""); }} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-50">Cancel</button>
        </div>
      )}
      {items === null ? <p className="text-xs text-ink-tertiary">Loading…</p>
        : items.length === 0 ? (!adding && <p className="text-xs text-ink-tertiary">No tasks on this job yet.</p>)
        : (
          <ul className="divide-y divide-slate-100">
            {items.map((t) => (
              <li key={t.id} className="flex items-center gap-3 py-2">
                <input type="checkbox" checked={!!t.completed_at} onChange={() => toggle(t)} aria-label={`Done: ${t.title}`} className="h-4 w-4 accent-[rgb(var(--c-primary))]" />
                <span className={`flex-1 min-w-0 truncate text-xs ${t.completed_at ? "line-through text-ink-tertiary" : "text-ink-primary font-medium"}`}>{t.title}</span>
                {t.overdue && !t.completed_at && <span className="text-[11px] font-semibold text-rose-600">Overdue</span>}
                <span className="flex items-center gap-1.5">
                  {t.assigned_to_id && <Avatar name={t.assignee_name || ""} size={20} />}
                  <select value={t.assigned_to_id || ""} onChange={(e) => assign(t, e.target.value)} aria-label={`Assignee for ${t.title}`}
                    className="bg-transparent text-[11px] text-ink-secondary max-w-[130px] truncate">
                    <option value="">Unassigned</option>
                    {emps.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                  </select>
                </span>
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}
