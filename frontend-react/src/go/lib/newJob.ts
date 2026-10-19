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

export type Draft = {
  isEstimate: boolean;
  clientId?: string | null;
  deviceId?: string | null;
  newDevice?: NewDevice | null;
  complaint: string;
  assignedToId?: string | null;
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
  if (d.clientId && d.deviceId) body.device_id = d.deviceId;
  else if (d.clientId && d.newDevice) {
    const nd = d.newDevice;
    const imei = nd.imei.replace(/\s+/g, "");
    if (nd.brand.trim()) body.asset_brand = nd.brand.trim();
    if (nd.model.trim()) body.asset_model = nd.model.trim();
    if (nd.serial.trim()) body.asset_serial = nd.serial.trim();
    if (imei) body.asset_imei = imei;
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
