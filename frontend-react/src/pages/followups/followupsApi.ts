// Follow-ups (Phase 1): the types the API sends, the calls, and the plain words the three
// screens share (the job's Next step card, Settings › Follow-ups, Reports › Follow-ups).
import { api, ApiError } from "../../api/client";
import { money, currencySymbol } from "../orders/types";

export type StepAction = "sms" | "email" | "task";
export type PresetStep = { index: number; after_hours: number; action: StepAction; label: string; enabled: boolean };
export type Preset = {
  key: string; name: string; enabled: boolean; steps: PresetStep[];
  stop_conditions: string[]; start_status_ids: string[];
};
export type PreviewJob = { order_id: string; order_number: string | number; client_name: string; amount: number; since: string; days_waiting: number };
export type Preview = { count: number; total_amount: number; currency: string; jobs: PreviewJob[]; messages_today: number };

export type FollowupStepStatus = "done" | "sent" | "scheduled" | "skipped" | "cancelled";
export type FollowupStep = { index: number; label: string; action: StepAction; due_at: string | null; status: FollowupStepStatus; at: string | null; task_id: string | null };
export type Followup = {
  active: boolean; preset_key: string | null; state: "active" | "stopped" | "completed" | "none";
  started_at: string | null; stop_reason: string | null; steps: FollowupStep[];
  next_step: { index: number; label: string; due_at: string | null; task_id: string | null } | null;
};

export type ReportItem = { order_id: string; order_number: string | number; client_name: string; preset_key: string; amount: number; outcome: "collected" | "approved"; outcome_at: string };
export type FollowupsReport = {
  recovered_amount: number; currency: string; collected_after_reminder: number; quotes_approved_after_nudge: number;
  messages_sent: number; texts_after_collection: number; items: ReportItem[];
};

export const followupsApi = {
  presets: () => api.get<{ items: Preset[] }>("/followups/presets"),
  savePreset: (key: string, body: { enabled?: boolean; steps?: { index: number; after_hours: number; enabled: boolean }[] }) =>
    api.put<Preset>(`/followups/presets/${encodeURIComponent(key)}`, body),
  preview: (key: string) => api.post<Preview>(`/followups/presets/${encodeURIComponent(key)}/preview`, {}),
  forOrder: (orderId: string) => api.get<Followup>(`/orders/${orderId}/followup`),
  skip: (orderId: string) => api.post<Followup>(`/orders/${orderId}/followup/skip`, {}),
  report: (from: string, to: string) =>
    api.get<FollowupsReport>(`/reports/followups?${new URLSearchParams({ date_from: from, date_to: to })}`),
};

/** True when the server answered "not here" — the feature isn't deployed on this server yet. */
export const isMissing = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.status === 405);
/** True when the server refused: no permission, or the plan doesn't include it. */
export const isRefused = (e: unknown) => e instanceof ApiError && (e.status === 403 || e.status === 402);

/** The symbol for a currency code: the shop's own when it matches (or is missing), else Intl's. */
export function symbolFor(code?: string | null): string | undefined {
  if (!code) return undefined;
  try {
    const parts = new Intl.NumberFormat("en", { style: "currency", currency: code }).formatToParts(0);
    const sym = parts.find((p) => p.type === "currency")?.value;
    // Intl writes "US$" / "CA$" for dollars outside the US; the shop's own symbol reads better.
    if (!sym || /^[A-Z]{1,3}\$$/.test(sym)) return currencySymbol();
    return sym;
  } catch { return undefined; }
}
export const moneyIn = (n: number | null | undefined, code?: string | null) => money(n ?? 0, symbolFor(code));

/** "After 24 h", "Day 3": how long after the start a step runs. */
export function whenText(hours: number): string {
  if (hours <= 48 || hours % 24 !== 0) return `After ${hours} h`;
  return `Day ${hours / 24}`;
}

/** What a step does, in counter words. */
export const ACTION_WORD: Record<StepAction, string> = { sms: "text", email: "email", task: "call task" };

/** Is this preset the one that chases quotes (vs. devices waiting to be collected)? */
export const isQuotePreset = (key?: string | null) => /quote|estimate|approv/i.test(key || "");

/** One line on how the follow-up ends, by preset. */
export function closesText(key?: string | null): string {
  return isQuotePreset(key)
    ? "Closes by itself when the customer approves or declines."
    : "Closes by itself when the job is paid and collected.";
}

/** "Nov 5", or "Today" / "Tomorrow" / "Yesterday". */
export function shortDay(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((day.getTime() - t.getTime()) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** A stored reason like "paid_and_collected" in words. */
export function reasonText(r?: string | null): string {
  const map: Record<string, string> = {
    skipped: "Someone skipped this job.", manual: "Someone skipped this job.", skip: "Someone skipped this job.",
    collected: "The job was paid and collected.", paid_and_collected: "The job was paid and collected.", closed: "The job was closed.",
    approved: "The customer approved the quote.", declined: "The customer declined the quote.",
    status_changed: "The job moved to another status.", opted_out: "The customer asked not to get texts.",
    preset_disabled: "The follow-up was switched off in Settings.",
  };
  if (!r) return "";
  if (map[r]) return map[r];
  const s = r.replace(/_/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) + "." : "";
}
