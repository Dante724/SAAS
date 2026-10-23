// Tekly Go shell: routes, bottom tab bar, offline banner, toasts.
// Screens fill the space above the tab bar (hidden inside the /new/* create flows), so a docked
// footer on a screen sits on the bar instead of under it.
// iPad / tablet (≥768px): Today and a job share the screen — list on the left, job on the right.
import { useCallback, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { CloudOff, MousePointerClick } from "lucide-react";
import TabBar from "./components/TabBar";
import { Toast, type ToastMsg } from "./components/ui";
import { ToastCtx, useQueueStatus, useWide } from "./lib/useGo";
import Today from "./screens/Today";
import Scan from "./screens/Scan";
import Job from "./screens/Job";
import Clock from "./screens/Clock";
import More from "./screens/More";
import Tasks, { useTaskBadge } from "./screens/Tasks";
import NewJob from "./screens/NewJob";
import NewMenu from "./components/NewMenu";
import { LockProvider } from "./components/Lock";
import { AssetScreen, Part } from "./screens/PartAsset";

function SplitView({ jobOpen }: { jobOpen: boolean }) {
  return (
    <div className="flex h-full">
      <div className="w-[min(24rem,42%)] shrink-0 border-r-[0.5px] border-slate-200 h-full"><Today /></div>
      <div className="flex-1 min-w-0 h-full">
        {jobOpen ? <Job /> : (
          <div className="h-full flex flex-col items-center justify-center gap-3 text-center px-8 text-ink-tertiary">
            <MousePointerClick size={36} strokeWidth={1.5} />
            <p className="text-go-body">Pick a job to see it here</p>
          </div>
        )}
      </div>
    </div>
  );
}

function OfflineBar() {
  const q = useQueueStatus();
  if (q.online && !q.held) return null;
  return (
    <div className="fixed inset-x-0 top-0 z-30 pt-[env(safe-area-inset-top)] pointer-events-none flex justify-center">
      <div className="mt-1.5 rounded-full bg-ink-primary px-3 py-1.5 text-go-sub font-medium text-white shadow-menu inline-flex items-center gap-1.5 pointer-events-auto" role="status">
        <CloudOff size={14} />
        {!q.online ? "Offline" : "Off the shop network"}{q.pending ? ` · ${q.pending} waiting` : ""}
      </div>
    </div>
  );
}

export default function GoApp() {
  const wide = useWide();
  const q = useQueueStatus();
  const loc = useLocation();
  const tasksDue = useTaskBadge();
  const showNew = loc.pathname === "/today" || loc.pathname === "/tasks";
  const showTabs = !loc.pathname.startsWith("/new/");
  const [toast, setToast] = useState<ToastMsg | null>(null);
  const clear = useCallback(() => setToast(null), []);

  return (
    <ToastCtx.Provider value={setToast}>
      <LockProvider>
      <div className="fixed inset-0 bg-canvas text-ink-primary pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]">
        <OfflineBar />
        <div className={`absolute inset-x-0 top-0 ${showTabs ? "bottom-[calc(env(safe-area-inset-bottom)+3.75rem)]" : "bottom-[env(safe-area-inset-bottom)]"}`}>
        <Routes>
          <Route path="/" element={<Navigate to="/today" replace />} />
          <Route path="/today" element={wide ? <SplitView jobOpen={false} /> : <Today />} />
          <Route path="/job/:id" element={wide ? <SplitView jobOpen /> : <Job />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/new/job" element={<NewJob key="job" />} />
          <Route path="/new/estimate" element={<NewJob key="estimate" />} />
          <Route path="/scan" element={<Scan />} />
          <Route path="/part/:id" element={<Part />} />
          <Route path="/asset/:id" element={<AssetScreen />} />
          <Route path="/clock" element={<Clock />} />
          <Route path="/more" element={<More />} />
          <Route path="*" element={<Navigate to="/today" replace />} />
        </Routes>
        </div>
        {showNew && <NewMenu />}
        {showTabs && <TabBar moreBadge={q.conflicts || undefined} tasksBadge={tasksDue || undefined} />}
        <Toast msg={toast} onDone={clear} />
      </div>
      </LockProvider>
    </ToastCtx.Provider>
  );
}
