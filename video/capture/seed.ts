// Demo data for the capture, created through the real API: Dani's shop, customers, a Tuesday's
// worth of jobs, the iPhone 13 screen part in stock, and the shop settings that let a customer
// approve and sign a work order online.
import crypto from "crypto";
import fs from "fs";
import path from "path";
import { Api } from "./lib/api";

export type Seed = {
  api: Api; token: string; locationId: string;
  maya: { id: string }; partId: string;
  techs: Record<string, string>;
  others: { id: string; eod: string }[];
};

const today = (h: number, m = 0) => { const d = new Date(); d.setHours(h, m, 0, 0); return d.toISOString(); };

export async function seedBase(base: string, credFile: string): Promise<Seed> {
  const api = new Api(base);
  // Local test credentials, generated per run and kept in a file next to the DB (never printed).
  const password = "Cap-" + crypto.randomBytes(12).toString("base64url");
  const email = "dani@danisrepair.test";
  const reg = await api.post("/auth/register", {
    org_name: "Dani's Repair", name: "Dani Reyes", email, password, accept_terms: true,
    country: "US", industry_template: "mobile_phone", company_phone: "(503) 555-0142",
  });
  fs.writeFileSync(credFile, JSON.stringify({ email, password }), { mode: 0o600 });
  api.token = reg.access_token;

  await api.put("/staff/company", { company_phone: "(503) 555-0142", company_address: "1418 SE Hawthorne Blvd, Portland, OR 97214" }).catch(() => {});
  const locs = await api.get("/staff/locations");
  const locationId = (locs.items || locs)[0].id;

  // Statuses for online approval: the job waits for the customer, then moves to Approved.
  await api.post("/statuses", { name: "Awaiting approval", key: "awaiting_approval", status_group: "new", color: "#f5a623", record_type: "work_order" });
  const approved = await api.post("/statuses", { name: "Approved", key: "approved", status_group: "in_progress", color: "#2563eb", record_type: "work_order" });
  const sts = await api.get("/statuses?record_type=work_order");
  const list: any[] = (sts.items || sts).filter((s: any) => (s.record_type || "work_order") === "work_order");
  const order = ["new", "diagnosed", "awaiting_approval", "approved", "waiting_parts", "in_repair", "ready", "done", "delivered", "dropped"];
  const ids = order.map((k) => list.find((s) => s.key === k)?.id).filter(Boolean);
  if (ids.length) await api.post("/statuses/reorder", { ordered_ids: ids }).catch((e) => console.warn("[seed] reorder:", e.message));
  void approved;

  await api.put("/staff/public-pages", {
    oe_wo_allow_approval: true, oe_wo_require_signature: true,
    oe_wo_accept_status: "approved", oe_allow_recommend: true, oe_allow_messages: true,
  });

  // Two technicians besides Dani
  const techs: Record<string, string> = {};
  for (const [name, mail] of [["Leo Park", "leo@danisrepair.test"], ["Sam Ortiz", "sam@danisrepair.test"]]) {
    try {
      const e = await api.post("/staff/employees", { name, email: mail, password: "Tech-" + crypto.randomBytes(9).toString("base64url"), location_id: locationId });
      techs[name] = e.id;
    } catch (e: any) { console.warn("[seed] employee:", e.message); }
  }
  const emps = await api.get("/staff/employees");
  for (const e of (emps.items || emps)) if (e.name === "Dani Reyes") techs["Dani Reyes"] = e.id;

  const client = (name: string, phone: string, email?: string) => api.post("/clients", { name, phone, email });
  const maya = await client("Maya Chen", "(503) 555-0187", "maya.chen@example.com");

  // The part: iPhone 13 screen, one in stock
  const part = await api.post("/inventory/parts", { name: "iPhone 13 screen (OLED)", sku: "SCR-IP13", brand: "Apple", cost_price: 62, sell_price: 140, min_stock: 1 });
  await api.post(`/inventory/parts/${part.id}/adjust`, { location_id: locationId, qty: 1, movement_type: "receive", unit_cost: 62, note: "Opening stock" });
  for (const [name, sku, cost, sell, qty] of [
    ["iPhone 13 battery", "BAT-IP13", 18, 45, 4], ["Galaxy S23 USB-C port", "PRT-S23", 9, 35, 3],
    ["MacBook Air M2 keyboard", "KB-MBA-M2", 54, 120, 2], ["Pixel 8 battery", "BAT-PX8", 21, 49, 2],
  ] as [string, string, number, number, number][]) {
    const p = await api.post("/inventory/parts", { name, sku, cost_price: cost, sell_price: sell, min_stock: 1 });
    await api.post(`/inventory/parts/${p.id}/adjust`, { location_id: locationId, qty, movement_type: "receive", unit_cost: cost });
  }

  // Catalog services (picked at intake)
  for (const [name, price, mins] of [["Screen replacement – iPhone 13", 140, 45], ["Battery replacement – iPhone 13", 45, 30],
    ["Diagnostic", 35, 30], ["Charging port replacement", 89, 45]] as [string, number, number][])
    await api.post("/catalog/services", { name, price, duration_min: mins }).catch((e) => console.warn("[seed] service:", e.message));

  // A plausible Tuesday: phones, a laptop, a car, a chainsaw, an outboard…
  type J = { who: [string, string]; tpl: string; brand: string; model: string; complaint: string; line: [string, number];
             status: string; eod: string; tech: string; at: [number, number]; imei?: string; vin?: string; hin?: string; serial?: string };
  const jobs: J[] = [
    { who: ["Jordan Ellis", "(503) 555-0110"], tpl: "mobile_phone", brand: "Samsung", model: "Galaxy S23", complaint: "Won't charge, port is loose", line: ["Charging port replacement", 89], status: "in_repair", eod: "done", tech: "Leo Park", at: [9, 0], imei: "350123451234567" },
    { who: ["Priya Nair", "(503) 555-0123"], tpl: "computer", brand: "Apple", model: "MacBook Air 13-inch (M2)", complaint: "Coffee spill, sticky keys", line: ["Keyboard replacement", 260], status: "waiting_parts", eod: "waiting_parts", tech: "Sam Ortiz", at: [9, 30], serial: "C02FK3XJQ6L4" },
    { who: ["Marcus Bell", "(503) 555-0131"], tpl: "auto", brand: "Toyota", model: "Camry 2018", complaint: "Grinding when braking", line: ["Front brake pads and rotors", 420], status: "in_repair", eod: "ready", tech: "Sam Ortiz", at: [8, 30], vin: "4T1B11HK5JU512345" },
    { who: ["Tom Avery", "(503) 555-0145"], tpl: "small_engine", brand: "Stihl", model: "MS 271 chainsaw", complaint: "Starts then dies", line: ["Carburetor clean and tune", 95], status: "diagnosed", eod: "done", tech: "Leo Park", at: [10, 0], serial: "1145-200-0123" },
    { who: ["Lisa Moreno", "(503) 555-0152"], tpl: "marine", brand: "Yamaha", model: "F115 outboard", complaint: "100-hour service", line: ["100-hour service", 385], status: "ready", eod: "delivered", tech: "Sam Ortiz", at: [8, 0], hin: "YAM45821F818" },
    { who: ["Ben Carter", "(503) 555-0166"], tpl: "mobile_phone", brand: "Apple", model: "iPad Air (M2)", complaint: "Cracked glass, touch works", line: ["Glass and digitizer replacement", 229], status: "new", eod: "in_repair", tech: "Leo Park", at: [11, 0], serial: "DMPXK2LQ1F" },
    { who: ["Ana Silva", "(503) 555-0170"], tpl: "mobile_phone", brand: "Google", model: "Pixel 8", complaint: "Battery drains by noon", line: ["Battery replacement", 89], status: "in_repair", eod: "done", tech: "Dani Reyes", at: [10, 30], imei: "357912345678903" },
    { who: ["Ethan Brooks", "(503) 555-0181"], tpl: "mobile_phone", brand: "Nintendo", model: "Switch OLED", complaint: "Left Joy-Con drifts", line: ["Joy-Con stick replacement", 49], status: "ready", eod: "delivered", tech: "Leo Park", at: [9, 15], serial: "XTW70012345678" },
    { who: ["Chris Young", "(503) 555-0193"], tpl: "mobile_phone", brand: "Sony", model: "PlayStation 5", complaint: "No picture, HDMI port bent", line: ["HDMI port replacement", 149], status: "waiting_parts", eod: "in_repair", tech: "Sam Ortiz", at: [12, 0], serial: "E21234567890" },
    { who: ["Sofia Ruiz", "(503) 555-0104"], tpl: "mobile_phone", brand: "Apple", model: "iPhone 15 Pro", complaint: "Back glass shattered", line: ["Back glass replacement", 169], status: "diagnosed", eod: "done", tech: "Dani Reyes", at: [11, 30], imei: "356789012345672" },
    { who: ["Noah King", "(503) 555-0119"], tpl: "computer", brand: "Dell", model: "XPS 13", complaint: "Several keys not working", line: ["Keyboard repair", 135], status: "new", eod: "ready", tech: "Leo Park", at: [13, 0], serial: "8XK2LM3" },
  ];
  const others: { id: string; eod: string }[] = [];
  for (const j of jobs) {
    const c = await client(j.who[0], j.who[1]);
    const o = await api.post("/orders", {
      client_id: c.id, complaint: j.complaint, template_key: j.tpl,
      asset_brand: j.brand, asset_model: j.model, asset_imei: j.imei, asset_vin: j.vin, asset_hin: j.hin, asset_serial: j.serial,
      assigned_to_id: techs[j.tech] || null, scheduled_for: today(...j.at), duration_min: 60,
      lines: [{ description: j.line[0], kind: "service", line_type: "labour", qty: 1, unit_price: j.line[1] }],
    });
    if (j.status !== "new") await setStatus(api, o.id, j.status);
    others.push({ id: o.id, eod: j.eod });
  }
  return { api, token: reg.access_token, locationId, maya, partId: part.id, techs, others };
}

export async function setStatus(api: Api, id: string, status: string) {
  try { await api.patch(`/orders/${id}/status`, { status, allow_unpaid: true }); }
  catch (e: any) { console.warn(`[seed] status ${status}:`, e.message); }
}

/** After intake: the screen line, the battery recommendation, the photo shared with Maya, the
 *  public link, and the job set to wait for her approval. Returns the track URL path. */
export async function prepareApproval(s: Seed, orderId: string, batteryJpg: string) {
  const { api } = s;
  const o = await api.get(`/orders/${orderId}`);
  if (!(o.lines || []).some((l: any) => /screen/i.test(l.description || "")))
    await api.post(`/orders/${orderId}/lines`, { description: "Screen replacement", kind: "service", line_type: "labour", qty: 1, unit_price: 140 });
  const bat = "data:image/jpeg;base64," + fs.readFileSync(batteryJpg).toString("base64");
  const rec = await api.post(`/orders/${orderId}/recommendations`, {
    title: "Battery at 71% – replace?", note: "Battery health is 71%. A new one brings back all-day charge.",
    kind: "custom", qty: 1, unit_price: 45, media_data: bat, urgency: "soon",
  });
  await api.post(`/orders/${orderId}/recommendations/${rec.id}/publish`).catch((e) => console.warn("[seed] publish rec:", e.message));
  // share the marked-up check-in photo on Maya's page
  const ph = (o.intake_photos || []).map((p: any) => p.marked_id || p.id);
  const pid = ph[ph.length - 1];
  if (pid) {
    const { buf, mime } = await api.bytes(`/media/${pid}`);
    await api.post(`/feed/order/${orderId}`, { item_type: "photo", body: "Your iPhone at check-in: the crack we'll fix.", media_data: `data:${mime};base64,${buf.toString("base64")}`, media_name: "check-in.jpg", media_mime: mime, visibility: "client" });
  }
  await setStatus(api, orderId, "awaiting_approval");
  const link = await api.post(`/orders/${orderId}/public-link`);
  return link.path || `/track/${link.token}`;
}

export function photoPath(...p: string[]) { return path.join(...p); }

/** Take the balance of a job by card (end of the day: customers pay and collect). */
export async function payOrder(api: Api, id: string) {
  try {
    const acc = await api.get("/cash/accounts");
    const list: any[] = acc.items || acc;
    const card = list.find((a) => /card/i.test(a.name || "")) || list[0];
    await api.post(`/cash/orders/${id}/pay`, { method: "card", account_id: card.id });
  } catch (e: any) { console.warn("[seed] pay:", e.message); }
}
