import { useEffect, useRef } from "react";

// Poll while the tab is visible: runs `fn` now, then every `ms`; stops while the tab is hidden
// and runs once straight away when it's shown again. A shop leaves Tekly open in background
// tabs all day — each one used to keep polling the API.
export function useVisibleInterval(fn: () => void, ms: number, enabled = true) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!enabled) return;
    let id: ReturnType<typeof setInterval> | null = null;
    const start = () => { if (id == null) id = setInterval(() => ref.current(), ms); };
    const stop = () => { if (id != null) { clearInterval(id); id = null; } };
    const onVis = () => {
      if (document.visibilityState === "hidden") stop();
      else { ref.current(); start(); }
    };
    ref.current();
    if (document.visibilityState !== "hidden") start();
    document.addEventListener("visibilitychange", onVis);
    return () => { stop(); document.removeEventListener("visibilitychange", onVis); };
  }, [ms, enabled]);
}
