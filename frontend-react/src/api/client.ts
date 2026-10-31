// Tekly API client — reuses the EXISTING session (same-origin cookie/token).
// No auth redesign, no CORS: the React bundle is served from the same origin as
// the live app, so the browser sends the logged-in session automatically.
// Endpoints are the existing FastAPI ones (see inventory/endpoints.txt).

const BASE = "/api";

// Global branch scope (legacy App.locationId). When set, every GET gets
// ?location_id=… appended so lists show one branch — the switcher lives in the
// Shell top bar. ponytail: GET-only + string-append; writes carry their own
// location_id in the body where the backend needs it.
export function getLocationScope(): string { try { return localStorage.getItem("ros_loc") || ""; } catch { return ""; } }
export function setLocationScope(id: string) { try { localStorage.setItem("ros_loc", id || ""); } catch { /* ignore */ } }
function scoped(method: string, path: string): string {
  const loc = getLocationScope();
  if (!loc || method !== "GET" || path.startsWith("/staff/locations") || path.startsWith("/auth/")) return path;
  return path + (path.includes("?") ? "&" : "?") + "location_id=" + encodeURIComponent(loc);
}

// The vanilla app stores its bearer token in localStorage under this key in some
// builds; if present we forward it. Cookie-based sessions work without it.
function authHeader(): Record<string, string> {
  try {
    // The vanilla app stores its bearer token under "ros_tok" (see core.js).
    // We keep the older keys as fallbacks for safety.
    const t =
      localStorage.getItem("ros_tok") ||
      localStorage.getItem("ros_token") ||
      localStorage.getItem("token") ||
      "";
    return t ? { Authorization: `Bearer ${t}` } : {};
  } catch {
    return {};
  }
}

// Which app is calling, sent as X-Tekly-Client. Tekly Go sets "go/<version>" at start-up so the
// server can tell actions made on a phone from the web app's (the Go pilot report); the web app
// leaves it empty and sends nothing.
let clientTag = "";
export function setClientTag(tag: string) { clientTag = tag; }
export function clientHeader(): Record<string, string> { return clientTag ? { "X-Tekly-Client": clientTag } : {}; }

async function request<T>(method: string, path: string, body?: unknown, extra?: Record<string, string>): Promise<T> {
  const res = await fetch(BASE + scoped(method, path), {
    method,
    credentials: "include", // send the existing session cookie
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...clientHeader(),
      ...(extra || {}),
    },
    body: body != null ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    // An expired or invalid session goes back to the sign-in page.
    if (res.status === 401) {
      try {
        localStorage.removeItem("ros_tok");
      } catch {
        /* ignore */
      }
      if (typeof window !== "undefined" && window.location.pathname !== "/login") {
        // Tekly Go comes back to itself after signing in; the web app lands on its home page.
        const p = window.location.pathname;
        window.location.replace(p === "/go" || p.startsWith("/go/") ? "/login?next=" + encodeURIComponent(p) : "/login");
      }
    }
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// One validation problem in words that name the field: {"loc":["body","lines",0,"line_type"],
// "msg":"Field required"} → "Line 1: line type is required".
function fieldProblem(x: any): string {
  const msg = String(x?.msg ?? x ?? "").replace(/^Value error, /, "");
  const loc: (string | number)[] = Array.isArray(x?.loc) ? x.loc.filter((p: any) => p !== "body" && p !== "query" && p !== "path") : [];
  if (!loc.length) return msg;
  const parts: string[] = [];
  loc.forEach((p, i) => {
    if (typeof p === "number") {
      const prev = parts.pop() || "item";
      parts.push(`${prev.replace(/s$/, "")} ${p + 1}`);
    } else parts.push(String(p).replace(/_id$/, "").replace(/_/g, " "));
    if (i === 0 && parts.length) parts[0] = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
  });
  const where = parts.length > 1 ? `${parts.slice(0, -1).join(", ")}: ${parts[parts.length - 1]}` : parts[0];
  if (/^field required$/i.test(msg)) return `${where} is required`;
  return `${where}: ${msg.charAt(0).toLowerCase()}${msg.slice(1)}`;
}

// The server answers errors as {"detail": "..."} (or a list of field problems). Show the
// sentence, not the JSON; the raw body stays on `.body` for anything that needs it.
export function readableError(raw: string): string {
  try {
    const j = JSON.parse(raw);
    const d = j?.detail ?? j?.message ?? j?.error;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return [...new Set(d.map(fieldProblem))].join("; ");
    if (d && typeof d === "object" && typeof d.message === "string") return d.message;
  } catch { /* not JSON */ }
  return raw;
}

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, message: string) {
    super(readableError(message));
    this.status = status;
    this.body = message;
  }
}

// Print/PDF: the /print/* endpoints return PDF bytes and require the bearer
// token, so window.open(url) would 401. Fetch with auth, then open the blob in a
// new tab. Reused by WO, PO, invoice and payroll print buttons.
export async function openAuthedDoc(path: string): Promise<void> {
  const res = await fetch(BASE + path, { credentials: "include", headers: { ...authHeader(), ...clientHeader() } });
  if (!res.ok) throw new ApiError(res.status, (await res.text().catch(() => "")) || res.statusText);
  const blob = await res.blob();
  // A blob URL runs with this app's origin, so only open types that can't execute script;
  // anything else (HTML, SVG, XML, unknown) is downloaded as an opaque file instead.
  if (!isInlineSafeType(blob.type)) {
    const url = URL.createObjectURL(new Blob([blob], { type: "application/octet-stream" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = downloadName(res.headers.get("content-disposition"));
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    return;
  }
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

const INLINE_SAFE = /^(application\/pdf|image\/(png|jpeg|gif|webp|heic|heif)|audio\/[\w.+-]+|video\/[\w.+-]+|text\/plain)(;|$)/i;

export function isInlineSafeType(type: string): boolean {
  return INLINE_SAFE.test((type || "").trim());
}

function downloadName(disposition: string | null): string {
  const m = /filename\*=UTF-8''([^;]+)/i.exec(disposition || "");
  try { return m ? decodeURIComponent(m[1]) : "download"; } catch { return "download"; }
}

// Multipart upload (no JSON Content-Type → browser sets the boundary). Reuses the session.
async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(BASE + path, { method: "POST", credentials: "include", headers: { ...authHeader(), ...clientHeader() }, body: form });
  if (!res.ok) throw new ApiError(res.status, (await res.text().catch(() => "")) || res.statusText);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b),
  // Creates and payments a phone may retry: the server runs one request per Idempotency-Key and
  // answers a retry with the first result (backend/services/idempotency.py).
  postOnce: <T>(p: string, b: unknown, key: string) => request<T>("POST", p, b, { "Idempotency-Key": key }),
  postForm,
  patch: <T>(p: string, b?: unknown) => request<T>("PATCH", p, b),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};
