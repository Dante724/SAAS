// Inquiries (leads) for Tekly Go: people who called, walked in or messaged before there is a job.
// Server: GET /api/leads?open_only=true (paged {items,total}), POST /api/leads (idempotent on
// idempotency_key, so the offline queue may replay it), PATCH /api/leads/{id} {status},
// POST /api/leads/{id}/convert → {order_id}. Pure helpers here; the screen is screens/Inquiries.tsx.
import { utc } from "./time";
import type { MutateResult } from "./offlineQueue";

export type InquiryStatus = "new" | "in_progress" | "won" | "lost";

export type Inquiry = {
  id: string; name: string; phone?: string | null; email?: string | null; source?: string;
  inquiry?: string | null; status: InquiryStatus; lead_type?: string | null; lead_type_id?: string | null;
  device_brand?: string | null; device_model?: string | null; serial?: string | null;
  call_count?: number; last_call_at?: string | null;
  sla_due_at?: string | null; sla_overdue?: boolean;
  converted_order_id?: string | null; converted_order_number?: string | null;
  created_at?: string | null;
};

export type InquiryType = { id: string; name: string; sla_minutes: number; active: boolean };
export type InquiryPage = { items: Inquiry[]; total: number };

export const PAGE_SIZE = 25;
export const STATUS_LABEL: Record<InquiryStatus, string> = { new: "New", in_progress: "In progress", won: "Won", lost: "Lost" };

export function listPath(q: string, page: number, size = PAGE_SIZE): string {
  const p = new URLSearchParams({ open_only: "true", page: String(page), page_size: String(size) });
  if (q.trim()) p.set("q", q.trim());
  return `/leads?${p}`;
}

/** Load one page and append it to what's shown (dropping any row the server moved between pages). */
export async function loadPage(get: <T>(p: string) => Promise<T>, q: string, page: number, prev: Inquiry[] = []) {
  const r = await get<InquiryPage>(listPath(q, page));
  const seen = new Set(prev.map((i) => i.id));
  const items = [...prev, ...(r.items || []).filter((i) => !seen.has(i.id))];
  return { items, total: r.total ?? items.length, more: items.length < (r.total ?? 0) && (r.items || []).length > 0 };
}

/** Late ones first (most overdue at the top), then the soonest reply-by, then newest. */
export function sortInquiries(list: Inquiry[]): Inquiry[] {
  const due = (i: Inquiry) => utc(i.sla_due_at)?.getTime() ?? Infinity;
  return [...list].sort((a, b) =>
    Number(!!b.sla_overdue) - Number(!!a.sla_overdue) || due(a) - due(b)
    || (utc(b.created_at)?.getTime() ?? 0) - (utc(a.created_at)?.getTime() ?? 0));
}

export const deviceText = (i: Pick<Inquiry, "device_brand" | "device_model">) =>
  [i.device_brand, i.device_model].map((s) => (s || "").trim()).filter(Boolean).join(" ");

export const callsText = (n?: number) => (n && n > 0 ? `${n} missed call${n > 1 ? "s" : ""}` : "");

/** When to get back to them. Server times are UTC without a "Z". */
export function replyBy(i: Pick<Inquiry, "sla_due_at" | "sla_overdue" | "status">, now = new Date()): { text: string; late: boolean } | null {
  if (i.status !== "new" && i.status !== "in_progress") return null;
  const d = utc(i.sla_due_at);
  if (!d) return null;
  const min = Math.round((d.getTime() - now.getTime()) / 60000);
  const span = (m: number) => (m < 60 ? `${m} min` : m < 60 * 24 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} d`);
  if (i.sla_overdue || min < 0) return { text: `Late ${span(Math.max(1, -min))}`, late: true };
  if (min < 60) return { text: `Reply in ${Math.max(1, min)} min`, late: false };
  return { text: `Reply by ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`, late: false };
}

export const telHref = (phone?: string | null) => {
  const p = (phone || "").replace(/[^\d+]/g, "");
  return p ? `tel:${p}` : "";
};

export type InquiryDraft = { name: string; phone: string; typeId: string; brand: string; model: string; serial: string; inquiry: string };
export const emptyDraft = (): InquiryDraft => ({ name: "", phone: "", typeId: "", brand: "", model: "", serial: "", inquiry: "" });

/** POST /api/leads body. The key is made once per form, so a replay can't make a second inquiry. */
export function inquiryBody(d: InquiryDraft, key: string): Record<string, unknown> {
  const t = (s: string) => s.trim() || null;
  return {
    name: d.name.trim(), phone: t(d.phone), source: "phone", lead_type_id: d.typeId || null,
    device_brand: t(d.brand), device_model: t(d.model), serial: t(d.serial), inquiry: t(d.inquiry),
    idempotency_key: key,
  };
}

type Mutate = (method: string, url: string, body?: unknown) => Promise<MutateResult>;

/** Create through the offline queue: sent now when online, kept and replayed (same key) when not. */
export const createInquiry = (mutate: Mutate, d: InquiryDraft, key: string) => mutate("POST", "/api/leads", inquiryBody(d, key));

/** Start working on it, or mark it lost. Queued when offline like any other status change. */
export const setInquiryStatus = (mutate: Mutate, id: string, status: "in_progress" | "lost") =>
  mutate("PATCH", `/api/leads/${encodeURIComponent(id)}`, { status });

/** Turn it into a job (needs a connection: the job's id is where we go next). */
export async function convertInquiry(post: <T>(p: string, b?: unknown) => Promise<T>, id: string): Promise<{ orderId: string; path: string; number?: string }> {
  const r = await post<{ order_id: string; order_number?: string }>(`/leads/${encodeURIComponent(id)}/convert?is_estimate=false`);
  return { orderId: r.order_id, number: r.order_number, path: `/job/${encodeURIComponent(r.order_id)}` };
}
