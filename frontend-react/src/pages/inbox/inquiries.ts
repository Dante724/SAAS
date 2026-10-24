// Inquiries (leads): shared types and small helpers for the board, the drawers and Tekly Go.
export type Lead = {
  id: string; name?: string; phone?: string | null; email?: string | null; source?: string; inquiry?: string | null;
  status?: string; client_id?: string | null; manager_id?: string | null;
  lead_type_id?: string | null; lead_type?: string | null;
  device_brand?: string | null; device_model?: string | null; serial?: string | null;
  custom_data?: Record<string, any>; call_count?: number; last_call_at?: string | null;
  converted_order_id?: string | null; converted_order_number?: string | null;
  sla_due_at?: string | null; sla_overdue?: boolean; created_at?: string; updated_at?: string | null;
};
export type LeadType = { id: string; name: string; sla_minutes: number; sort_order: number; active: boolean };
export type Column = { key: string; label: string; cards: Lead[] };
export type FeedItem = { id: string; item_type: string; body?: string | null; author_name?: string | null; visibility?: string; created_at: string };

export const CHANNELS: [string, string][] = [
  ["phone", "Phone"], ["whatsapp", "WhatsApp"], ["sms", "SMS"], ["email", "Email"], ["instagram", "Instagram"],
  ["facebook", "Facebook"], ["web", "Web form"], ["walk_in", "Walk-in"], ["api", "Connected app"], ["other", "Other"],
];
export const channelLabel = (s?: string) => CHANNELS.find(([k]) => k === s)?.[1] || "Other";
export const SRC_ICON: Record<string, string> = { phone: "call", whatsapp: "chat", sms: "sms", email: "mail", instagram: "photo_camera", facebook: "thumb_up", web: "public", walk_in: "directions_walk", api: "hub", other: "help" };

export const fmt = (iso?: string | null) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");

/** Respond-by chip: null once the inquiry is closed. */
export function sla(l: Lead, now = Date.now()) {
  if (!l.sla_due_at || l.status === "won" || l.status === "lost") return null;
  const ms = new Date(l.sla_due_at).getTime() - now;
  if (l.sla_overdue || ms < 0) return { cls: "bg-rose-100 text-rose-700 border-rose-200", label: "Overdue" };
  const mins = Math.round(ms / 60000);
  return { cls: "bg-amber-50 text-amber-700 border-amber-200/60", label: mins > 90 ? `${Math.round(mins / 60)}h left` : `${mins}m left` };
}

/** "3 missed calls" for inquiries opened by missed calls; null for one or none. */
export function callsLabel(l: Lead): string | null {
  const n = l.call_count || 0;
  if (n < 1) return null;
  return n === 1 ? "Missed call" : `${n} missed calls`;
}

export const deviceLine = (l: Lead) => [l.device_brand, l.device_model].filter(Boolean).join(" ");

/** A key that stays the same when the same form is retried, so a retry never creates twice. */
export function newIdempotencyKey(): string {
  try { return `web-${crypto.randomUUID()}`; } catch { return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`; }
}

/** The fields the drawer may send back: only what changed. */
export function changes(before: Lead, after: Lead): Partial<Lead> {
  const keys: (keyof Lead)[] = ["name", "phone", "email", "source", "inquiry", "lead_type_id", "device_brand", "device_model", "serial", "custom_data"];
  const out: Partial<Lead> = {};
  for (const k of keys) {
    const norm = (v: any) => (v === "" || v === undefined ? null : v);   // empty and missing are the same
    const a = norm(before[k]), b = norm(after[k]);
    if (JSON.stringify(a) !== JSON.stringify(b)) (out as any)[k] = b;
  }
  return out;
}
