// Pure helpers for the New job screen (unit-tested).

/** "Maria 0412 345 678" → { name: "Maria", phone: "0412 345 678" }. A phone is a run with 6+ digits. */
export function splitNamePhone(text: string): { name: string; phone: string } {
  const t = (text || "").trim();
  const m = t.match(/(\+?[\d][\d\s().-]{4,}\d)/);
  if (m && m[1].replace(/\D/g, "").length >= 6) {
    const phone = m[1].trim();
    const name = (t.slice(0, m.index) + t.slice((m.index ?? 0) + m[1].length)).replace(/\s+/g, " ").trim();
    return { name, phone };
  }
  return { name: t, phone: "" };
}

export type NewDevice = { brand: string; model: string; serial: string; imei: string };

export type QuoteLine = { kind: "service" | "product" | "bundle" | "custom"; ref_id?: string | null; description: string; qty: number; unit_price: number };

export type Draft = {
  isEstimate: boolean;
  clientId?: string | null;
  deviceId?: string | null;
  newDevice?: NewDevice | null;
  complaint: string;
  assignedToId?: string | null;
  // tablet intake (all optional)
  priority?: number;
  dueAt?: string | null;            // shop-local wall clock "YYYY-MM-DDTHH:mm" (the API stores due times that way)
  typeId?: string | null;
  customData?: Record<string, unknown>;
  condition?: string;               // the device's condition at drop-off (new device only)
  lines?: QuoteLine[];
};

/** The POST /api/orders body for a draft. A new device is sent inline (the server creates it for
 *  the customer); with no customer there's nobody to own it, so it's left out. */
export function orderBody(d: Draft): Record<string, unknown> {
  const body: Record<string, unknown> = {
    complaint: d.complaint.trim() || null,
    is_estimate: d.isEstimate,
    client_id: d.clientId || null,
    assigned_to_id: d.assignedToId || null,
  };
  if (d.priority) body.priority = d.priority;
  if (d.dueAt) body.due_at = d.dueAt.length === 16 ? `${d.dueAt}:00` : d.dueAt;
  if (d.typeId) body.type_id = d.typeId;
  const cd = Object.fromEntries(Object.entries(d.customData || {}).filter(([, v]) => !isBlank(v)));
  if (Object.keys(cd).length) body.custom_data = cd;
  if (d.lines?.length) {
    body.lines = d.lines.map((l) => ({
      description: l.description, qty: l.qty, unit_price: l.unit_price,
      kind: l.kind, line_type: l.kind === "service" || l.kind === "custom" ? "labour" : "part",
      ...(l.ref_id ? { ref_id: l.ref_id } : {}),
    }));
  }
  if (d.clientId && d.deviceId) body.device_id = d.deviceId;
  else if (d.clientId && d.newDevice) {
    const nd = d.newDevice;
    const imei = nd.imei.replace(/\s+/g, "");
    if (nd.brand.trim()) body.asset_brand = nd.brand.trim();
    if (nd.model.trim()) body.asset_model = nd.model.trim();
    if (nd.serial.trim()) body.asset_serial = nd.serial.trim();
    if (imei) body.asset_imei = imei;
    if (d.condition?.trim()) body.asset_condition = d.condition.trim();
  }
  return body;
}

/** Quick problem chips append to what's typed instead of replacing it. */
export function addProblem(current: string, chip: string): string {
  const c = current.trim();
  if (!c) return chip;
  if (c.toLowerCase().includes(chip.toLowerCase())) return c;
  return `${c.replace(/[.,;\s]+$/, "")}, ${chip.toLowerCase()}`;
}

const isBlank = (v: unknown) => v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0);

const pad = (n: number) => String(n).padStart(2, "0");
export const toLocalInput = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** One-tap "promised by" choices: today at 5pm (if it's still before 4pm), tomorrow 5pm, in 3 days, in a week. */
export function duePresets(now: Date): { label: string; value: string }[] {
  const at5 = (days: number) => { const d = new Date(now); d.setDate(d.getDate() + days); d.setHours(17, 0, 0, 0); return toLocalInput(d); };
  const out: { label: string; value: string }[] = [];
  if (now.getHours() < 16) out.push({ label: "Today 5pm", value: at5(0) });
  out.push({ label: "Tomorrow", value: at5(1) }, { label: "In 3 days", value: at5(3) }, { label: "In a week", value: at5(7) });
  return out;
}

/** A shop-defined intake field (from GET /forms/layout?form=order). */
export type IntakeField = { key: string; label: string; field_type: string; options: string[]; required: boolean };

/** Labels of required intake fields still empty (yes/no fields can't be "missing", like the server). */
export function missingFields(fields: IntakeField[], values: Record<string, unknown>): string[] {
  return fields.filter((f) => f.required && f.field_type !== "bool" && isBlank(values[f.key])).map((f) => f.label);
}

export const quoteTotal = (lines: QuoteLine[]) => lines.reduce((s, l) => s + l.qty * l.unit_price, 0);
