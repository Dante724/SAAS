import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";

// App-wide error notice. Replaces silent `.catch(() => {})` on loads and saves the user started:
//   api.get("/staff/locations").then(setLocs).catch(loadFailed("locations"));
//   await api.del(`/directories/${id}`).catch(saveFailed("delete the list"));
// The same sentence shown again while it's still on screen is not stacked (a dead network
// otherwise makes every list on the page add its own copy).

type Notice = { id: number; text: string };

let host: HTMLDivElement | null = null;
let root: Root | null = null;
let notices: Notice[] = [];
let nextId = 1;
const SHOW_MS = 7000;

function errText(e: unknown): string {
  const m = (e instanceof Error ? e.message : typeof e === "string" ? e : "").trim();
  // A proxy's HTML error page (502/504) is not a reason a person can read.
  if (m.startsWith("<")) return "";
  return m.length > 200 ? m.slice(0, 200) + "…" : m;
}

// 401: the API client is already sending the user to sign in. 403: the screen shows what this
// role may see, and a missing optional list (e.g. cash accounts for a cashier) isn't an error.
function quiet(e: unknown): boolean {
  const s = (e as { status?: number } | null)?.status;
  return s === 401 || s === 403;
}

function render() {
  if (!notices.length) {
    if (root) { root.unmount(); root = null; }
    if (host) { host.remove(); host = null; }
    return;
  }
  if (!host) {
    host = document.createElement("div");
    host.setAttribute("data-notice-host", "");
    document.body.appendChild(host);
    root = createRoot(host);
  }
  const list = notices;
  flushSync(() => root!.render(
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[210] flex flex-col items-center gap-2 w-[min(92vw,28rem)]" role="alert" aria-live="assertive">
      {list.map((n) => (
        <div key={n.id} className="w-full flex items-start gap-3 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer">
          <span className="flex-1 whitespace-pre-line">{n.text}</span>
          <button type="button" aria-label="Dismiss" className="shrink-0 text-white/70 hover:text-white" onClick={() => dismiss(n.id)}>✕</button>
        </div>
      ))}
    </div>
  ));
}

function dismiss(id: number) {
  notices = notices.filter((n) => n.id !== id);
  render();
}

/** Show an error sentence at the bottom of the screen for a few seconds. */
export function showNotice(text: string) {
  if (notices.some((n) => n.text === text)) return;
  const id = nextId++;
  notices = [...notices.slice(-2), { id, text }];
  render();
  setTimeout(() => dismiss(id), SHOW_MS);
}

/** A `.catch` handler for a load: "Couldn't load locations. <server reason>". */
export function loadFailed(what: string) {
  return (e: unknown) => {
    if (quiet(e)) return;
    const why = errText(e);
    showNotice(`Couldn't load ${what}.${why ? ` ${why}` : ""}`);
  };
}

/** A `.catch` handler for a save or action: "Couldn't delete the list. <server reason>". */
export function saveFailed(what: string) {
  return (e: unknown) => {
    if ((e as { status?: number } | null)?.status === 401) return;
    const why = errText(e);
    showNotice(`Couldn't ${what}.${why ? ` ${why}` : ""}`);
  };
}

/** Test hook: clear every notice. */
export function _resetNotices() {
  notices = [];
  render();
}
