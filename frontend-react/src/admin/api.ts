// The /admin app talks to /api/admin with the HttpOnly session cookie plus the
// X-Tekly-Admin header (a cross-site page can't add that header, so the cookie alone
// can't be used against us). The token is never kept in JavaScript or localStorage.

export class AdminApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let onSignedOut: () => void = () => {};
export function whenSignedOut(fn: () => void) { onSignedOut = fn; }

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/admin${path}`, {
    method,
    credentials: "same-origin",
    headers: { "X-Tekly-Admin": "1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data: any = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/login") && !path.startsWith("/mfa")) onSignedOut();
    const detail = typeof data?.detail === "string" ? data.detail
      : Array.isArray(data?.detail) ? "Check the highlighted fields and try again" : "Something went wrong";
    throw new AdminApiError(res.status, detail);
  }
  return data as T;
}

export const adminApi = {
  get: <T,>(p: string) => call<T>("GET", p),
  post: <T,>(p: string, b: unknown = {}) => call<T>("POST", p, b),
  patch: <T,>(p: string, b: unknown) => call<T>("PATCH", p, b),
};

export type Me = {
  id: string; name: string; email: string;
  role: { key: string | null; name: string };
  permissions: Record<string, boolean>;
  field_levels: Record<string, string>;
  limits: Record<string, number>;
};

export type Shop = {
  id: string; account_id: string; name: string; plan: string; sub_status: string | null; segment: string;
  sub_start: string | null; sub_end: string | null; country: string | null; employees: number;
  assigned_admin_id: string | null; assigned_to: string | null; last_contacted_at: string | null;
  discount_pct?: number; discount_until?: string | null; discount_reason?: string | null;
  company_email?: string | null; company_phone?: string | null; owner_name?: string | null; owner_email?: string | null;
  suspend_message?: string | null;
  // Subscription billing (services/billing.py). The note, price lock and Stripe id are billing-group fields.
  trial_ends_at?: string | null; billing_period?: string | null; billing_mode?: string;
  billing_period_end?: string | null; billing_note?: string | null; price_locked_until?: string | null;
  stripe_customer_id?: string | null;
  employees_detail?: { id: string; name: string; email: string; active: boolean; access_start: string | null; access_end: string | null }[];
  notes?: { id: string; kind: string; body: string; by: string | null; at: string }[];
  history?: { action: string; by: string | null; at: string; reason: string | null }[];
  jobs_7d?: number; last_activity_at?: string | null;
};

export const SEGMENTS: Record<string, string> = {
  new: "New trial", trial: "Trial", paid: "Paying", lost_trial: "Trial ended", lost_paid: "Cancelled", suspended: "Suspended",
};

export function fmtDate(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

// What an Action log or history entry did, in plain words (the server stores a short code).
const ACTIONS: Record<string, string> = {
  login: "Signed in", login_2step: "Signed in with 2-step", logout: "Signed out", mfa_enrolled: "2-step sign-in on",
  apply_code: "Applied a code", create_code: "Created a code", assign: "Handed over a shop",
  create_admin: "Added a person", edit_admin: "Changed a person", deactivate_admin: "Removed a person", change_role: "Changed a role",
  create_role: "Created a role", edit_role: "Changed a role's access",
  discount: "Gave a discount", discount_removed: "Removed a discount", extend_trial: "Extended a trial", plan_change: "Changed the plan",
  edit_subscription: "Changed billing terms", suspend: "Suspended a shop", unsuspend: "Unsuspended a shop",
  note: "Added a note", staff_access: "Changed staff access",
};
export function actionLabel(code: string): string {
  if (ACTIONS[code]) return ACTIONS[code];
  const m = code.match(/^(approve|decline|cancel)_(.+)$/);
  if (m) {
    const what = (ACTIONS[m[2]] || m[2].replace(/[_.]/g, " ")).toLowerCase();
    return `${m[1] === "approve" ? "Approved" : m[1] === "decline" ? "Declined" : "Cancelled"} a request: ${what}`;
  }
  const words = code.replace(/^platform\./, "").replace(/[_.]/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : code;
}

/** Date and time in the app's own style ("4 Nov 2026, 12:59"). */
export function fmtDateTime(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
  return d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
}
