/* Customer status messages live in one place: the notification rules (/notifications/rules).
   Shared by Settings › Notifications (the "when this happens, send this" table) and the status
   editor (its read-out and quick switches), so both agree on what fires and how a switch flips.
   Matching mirrors backend/services/notification_rules._rule_matches. */

export type NotifRule = {
  id: string; module?: string; scope?: string; event: string; channel: string;
  trigger_status_key?: string | null; record_type?: string | null; location_id?: string | null;
  template_id?: string | null; active: boolean;
};
type Api = { post: (p: string, b?: unknown) => Promise<unknown>; patch: (p: string, b?: unknown) => Promise<unknown> };

export const CHANNEL_LABEL: Record<string, string> = { sms: "Text", email: "Email", whatsapp: "WhatsApp" };

/** A client-facing order rule (what the Customers table shows; reminders live elsewhere). */
export const isCustomerRule = (r: NotifRule) => (r.module || "order") === "order" && (r.scope || "client") === "client" && r.event !== "reminder";
/** The Jobs / Estimates view a rule is listed under. Unscoped rules show under Jobs. */
export const inView = (r: NotifRule, rt: string) => (rt === "estimate" ? r.record_type === "estimate" : r.record_type !== "estimate");
/** The event a status change evaluates: entering a finishing status is "closed". */
export const statusEvent = (s: { is_terminal?: boolean; status_group?: string }) =>
  (s.is_terminal ?? (s.status_group === "closed" || s.status_group === "dropped")) ? "closed" : "status_changed";

/** The rules behind one cell of the table: this event, this channel, this status (null = every status). */
export function cellRules(rules: NotifRule[], rt: string, event: string, statusKey: string | null, ch: string) {
  return rules.filter((r) => isCustomerRule(r) && inView(r, rt) && r.event === event && r.channel === ch
    && (statusKey ? r.trigger_status_key === statusKey : !r.trigger_status_key));
}

/** Flip a cell: turn every active rule off, else re-enable the first one, else create it. */
export async function toggleCell(api: Api, rules: NotifRule[], rt: string, event: string, statusKey: string | null, ch: string) {
  const existing = cellRules(rules, rt, event, statusKey, ch);
  const active = existing.filter((r) => r.active);
  if (active.length) { for (const r of active) await api.patch(`/notifications/rules/${r.id}`, { active: false }); return false; }
  if (existing.length) { await api.patch(`/notifications/rules/${existing[0].id}`, { active: true }); return true; }
  await api.post("/notifications/rules", { module: "order", scope: "client", record_type: rt, event, trigger_status_key: statusKey, channel: ch, active: true });
  return true;
}

export type Firing = { channel: string; own: boolean; rule: NotifRule };

/** What goes out when a job of record type `rt` enters status `key` (undefined = a status not
    saved yet: only the every-status rules apply). One entry per active matching rule. */
export function firesOnStatus(rules: NotifRule[], rt: string, event: string, key?: string): Firing[] {
  return rules.filter((r) => r.active && isCustomerRule(r) && r.event === event
    && (!r.record_type || r.record_type === rt)
    && (!r.trigger_status_key || r.trigger_status_key === key))
    .map((r) => ({ channel: r.channel, own: !!r.trigger_status_key, rule: r }));
}

/** "Email · via Every status change", "Text", "Text · one location" — one line per firing rule. */
export function firingLabel(f: Firing, rt: string) {
  const via = f.own ? "" : f.rule.event === "closed" ? ` · via ${rt === "estimate" ? "Estimate" : "Job"} finished` : " · via Every status change";
  return `${CHANNEL_LABEL[f.channel] || f.channel}${via}${f.rule.location_id ? " · one location" : ""}`;
}

/** "email and text" for the plain-English summary: "messages the customer (email and text)". */
export function channelPhrase(channels: string[]) {
  const list = [...new Set(channels)].map((c) => (c === "sms" ? "text" : c === "whatsapp" ? "WhatsApp" : c));
  return list.length <= 1 ? list.join("") : `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}
