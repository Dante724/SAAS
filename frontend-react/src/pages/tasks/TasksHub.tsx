import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Shell from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { loadFailed } from "../../lib/notice";
import type { Task } from "./taskModel";
import TaskPanel, { type Emp } from "./TaskPanel";
import MyDay from "./MyDay";

type Toast = { msg: string; undo?: () => void; label?: string };

// Tasks: one page (quick add, Today / Upcoming / Later / Done, whose tasks, one search). The page
// owns what outlives a view: the open task (deep link), whose tasks (in the URL), the staff list
// and the toast.
export default function TasksHub() {
  const { me, can } = useMe();
  const isManager = can("can_assign_staff");      // seeing everyone's tasks is a manager view
  const [dayMissing, setDayMissing] = useState(false);   // server has no /tasks/my-day yet
  const [listPaged, setListPaged] = useState(false);     // the list API pages, so it soft-deletes too
  const [emps, setEmps] = useState<Emp[]>([]);
  const [seed, setSeed] = useState<Task | null>(null);
  const [reload, setReload] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  // A toast, optionally with one action button: "Undo" by default, or another label ("Try again").
  const flash = useCallback((msg: string, undo?: () => void, label?: string) => {
    clearTimeout(toastTimer.current);
    setToast({ msg, undo, label });
    toastTimer.current = setTimeout(() => setToast(null), undo ? 6000 : 2400);
  }, []);
  const onDayMissing = useCallback(() => setDayMissing(true), []);

  // Deep link: /tasks?task=<id> opens that task's panel (on load, from the bell, or from another
  // page); closing the panel takes the parameter off again. Opening a row sets it too, so the URL
  // of an open task can be copied and shared.
  const [sp, setSp] = useSearchParams();
  const openId = sp.get("task");
  // Whose tasks: kept in the URL so the view can be shared. Until we know who is signed in, and for
  // anyone who isn't a manager, it is "me" or "pool" (the server refuses anyone else's day).
  const askedFor = sp.get("assignee") || "me";
  const dayPerson = me && isManager ? askedFor : askedFor === "pool" ? "pool" : "me";
  const setDayPerson = useCallback((v: string) => { setSp((p) => { if (v === "me") p.delete("assignee"); else p.set("assignee", v); return p; }, { replace: true }); }, [setSp]);
  // A link to a task you can't open (gone, never existed, or not yours to see) gets one neutral
  // message, so it never confirms that a hidden task exists.
  const taskUnavailable = useCallback(() => { setSp((p) => { p.delete("task"); return p; }, { replace: true }); flash("This task isn't available"); }, [setSp, flash]);
  function openTask(t: Task) {
    setSeed(t);
    setSp((p) => { p.set("task", t.id); return p; });
  }
  const closeTask = useCallback(() => { setSp((p) => { p.delete("task"); return p; }, { replace: true }); }, [setSp]);

  useEffect(() => { api.get<any>("/staff/employees").then((r) => setEmps(r.items || r || [])).catch(loadFailed("the staff list")); }, []);
  const changed = useCallback(() => setReload((n) => n + 1), []);

  return (
    <Shell title="Tasks">
      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5">
        <MyDay meId={me?.id} emps={emps} isManager={isManager} person={dayPerson} onPerson={setDayPerson} onOpen={openTask} flash={flash}
          missing={dayMissing} onUnavailable={onDayMissing} reloadKey={reload} onListPaged={setListPaged} />
      </div>

      {openId && (
        <TaskPanel taskId={openId} seed={seed} emps={emps} onClose={closeTask} onUnavailable={taskUnavailable} onOpen={openTask} onChanged={changed} flash={flash}
          softDelete={(r) => !dayMissing || listPaged || !!(r && (r.archived || r.archived_at || r.deleted_at || r.restorable))} />
      )}
      {/* One polite live region that is always on the page, so screen readers announce each
          message (Done, Undo, errors) even though the toast comes and goes. Light surface, like
          Tekly Go's toasts; a problem gets the soft rose tint. */}
      <div role="status" aria-live="polite" className="fixed inset-x-0 bottom-6 z-[70] flex justify-center px-4 pointer-events-none">
        {toast && (
          <div className={`pointer-events-auto flex items-center gap-3 pl-4 pr-2 py-2 rounded-xl border text-xs font-semibold shadow-clay-drawer max-w-md
            ${/^Couldn't|isn't available|lost the connection/.test(toast.msg) ? "bg-rose-50 border-rose-200 text-rose-800" : "bg-white border-slate-200 text-ink-primary"}`}>
            <span className="py-0.5">{toast.msg}</span>
            {toast.undo && <button onClick={() => { const u = toast.undo!; setToast(null); u(); }} className="px-2.5 py-1 rounded-lg text-primary font-bold hover:bg-primary/10">{toast.label || "Undo"}</button>}
          </div>
        )}
      </div>
    </Shell>
  );
}
