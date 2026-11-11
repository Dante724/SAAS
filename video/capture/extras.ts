// Online booking, memberships (service plans) and reports, for Dani's shop.
import { spawnSync } from "child_process";
import fs from "fs";
import path from "path";
import type { Browser, Page } from "playwright";
import { Api } from "./lib/api";
import { Clip, type ClipMeta } from "./lib/clip";
import { payOrder, setStatus } from "./seed";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
type Opts = { browser: Browser; base: string; out: string; work: string };
const PY = process.env.TEKLY_PYTHON || "/Users/dan/Desktop/repairos/.venv/bin/python";

function tomorrow() { const d = new Date(); d.setDate(d.getDate() + 1); return d; }
const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

// ── Online booking ─────────────────────────────────────────────────────────────────────────
export async function seedBooking(api: Api) {
  await api.put("/staff/public-pages", { appointment_enabled: true });
  await api.put("/staff/public-pages/subdomain", { subdomain: "danisrepair" });
  const locs = await api.get("/staff/locations"); const loc = (locs.items || locs)[0];
  const wk = { open: "09:00", close: "18:00" };
  await api.put(`/staff/locations/${loc.id}`, { name: loc.name, address: "1418 SE Hawthorne Blvd, Portland, OR 97214", phone: "(503) 555-0142",
    working_hours: { mon: wk, tue: wk, wed: wk, thu: wk, fri: wk, sat: { open: "10:00", close: "16:00" }, sun: { closed: true } } }).catch((e) => console.warn("[seed] location:", e.message));
  // a few bookings already on tomorrow's calendar
  const svcs = (await api.get("/catalog/services")).items || [];
  const sid = (n: RegExp) => svcs.find((s: any) => n.test(s.name))?.id || null;
  const day = ymd(tomorrow());
  for (const [who, re, at] of [["Ben Carter", /Diagnostic/, "09:00"], ["Ana Silva", /Battery/, "13:30"], ["Noah King", /Screen/, "15:00"]] as [string, RegExp, string][])
    await api.post("/appointments", { client_name: who, service_id: sid(re), scheduled_for: `${day}T${at}` }).catch((e) => console.warn("[seed] appt:", e.message));
  return "/book/danisrepair";
}

export async function bookingMobile(o: Opts, bookPath: string): Promise<ClipMeta> {
  const clip = await Clip.open(o.browser, "booking-mobile", { mobile: true, outDir: o.out, rawDir: o.work });
  const p = clip.page;
  await p.goto(o.base + bookPath);
  await p.getByText("What can we help with?").waitFor({ timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await clip.markStart();
  await clip.pause(1300);
  clip.beat("Book an appointment: the shop's services and prices");
  await clip.tap(p.getByRole("button", { name: /Charging port replacement/ }), 900);
  clip.beat("Picked: Charging port replacement · $89");
  const noPref = p.getByRole("button", { name: /No preference/ });
  if (await noPref.count()) { await clip.pause(500); await clip.tap(noPref, 900); clip.beat("No preference (first available)"); }
  await p.getByText("Pick a time").waitFor({ timeout: 15000 });
  await p.locator("button", { hasText: /free/ }).first().waitFor({ timeout: 15000 });
  await clip.pause(800);
  const t = tomorrow();
  const day = p.locator("button").filter({ has: p.locator("div", { hasText: new RegExp(`^${t.getDate()}$`) }) }).first();
  await clip.tap(day, 1000);
  clip.beat(`Tomorrow (${t.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })})`);
  const slot = p.getByRole("button", { name: /^(10|11):\d\d/ }).first();
  await slot.waitFor({ timeout: 15000 });
  await clip.scrollMobileTo(slot, 0.45, 600);
  await clip.tap(slot, 700);
  const time = (await slot.innerText()).trim();
  clip.beat(`Time slot ${time}`);
  await clip.tap(p.getByRole("button", { name: "Continue" }), 900);
  await clip.tap(p.locator("#ap-phone"), 300);
  await p.keyboard.type("(503) 555-0129", { delay: 90 });
  await clip.tap(p.locator("#ap-name"), 300);
  await p.keyboard.type("Olivia Park", { delay: 100 });
  await p.keyboard.press("Tab").catch(() => {});
  clip.beat("Name and phone");
  await clip.pause(500);
  const confirm = p.getByRole("button", { name: /Confirm appointment/ });
  await clip.scrollMobileTo(confirm, 0.6, 600);
  await clip.tap(confirm, 300);
  await p.getByText("You're booked!").waitFor({ timeout: 20000 });
  await clip.pause(500);
  clip.beat("You're booked! (with Add to calendar)", true);
  await clip.pause(2600);
  return clip.finish();
}

export async function bookingDesktop(o: Opts, token: string): Promise<ClipMeta> {
  const clip = await Clip.open(o.browser, "booking-desktop", { token, outDir: o.out, rawDir: o.work });
  const p = clip.page;
  await p.goto(o.base + "/appointments");
  const olivia = p.getByText(/Olivia Park/).first();
  await olivia.waitFor({ timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await clip.markStart();
  await clip.pause(900);
  await clip.hover(olivia);
  clip.beat("Agenda: Olivia Park's online booking is in", true);
  await clip.pause(1800);
  await clip.click(p.getByRole("button", { name: /Scheduler/ }).first(), { settle: 1500 });
  clip.beat("Scheduler view");
  const next = p.getByRole("button", { name: /next|forward|tomorrow/i }).first();
  if (await next.count() && !(await p.getByText(/Olivia/).count())) { await clip.click(next, { settle: 1300 }); }
  const card = p.getByText(/Olivia/).first();
  if (await card.count()) { await clip.hover(card); clip.beat("Tomorrow on the calendar: Olivia Park · Charging port replacement"); }
  await clip.pause(2600);
  return clip.finish();
}

// ── Memberships (service plans) ────────────────────────────────────────────────────────────
export async function seedMemberships(api: Api, hero: { id: string }, others: { id: string }[]) {
  const svcs = (await api.get("/catalog/services")).items || [];
  const diag = svcs.find((s: any) => /Diagnostic/.test(s.name));
  const care = await api.post("/memberships/plans", {
    name: "Care Plan", description: "Free diagnostics and 10% off parts, all year.", price_monthly: 12, price_yearly: 120, color: "#0059ba",
    items: [{ kind: "service", service_id: diag?.id, label: "Diagnostic", qty: 2 }, { kind: "discount", label: "10% off parts", pct: 10, applies_to: "parts" },
            { kind: "perk", label: "Priority bench slot" }] });
  const annual = await api.post("/memberships/plans", {
    name: "Annual Service Plan", description: "A yearly check-up and 15% off labour.", price_yearly: 99, color: "#0f766e",
    items: [{ kind: "service", service_id: diag?.id, label: "Yearly check-up", qty: 1 }, { kind: "discount", label: "15% off labour", pct: 15, applies_to: "labour" }] });
  const sold: { client: string; device: string; plan: string }[] = [];
  const jobs = [hero.id, ...others.map((x) => x.id)];
  for (const [i, oid] of jobs.slice(0, 6).entries()) {
    const o = await api.get(`/orders/${oid}`);
    if (!o.client?.id || !o.device?.id) continue;
    const plan = i % 3 === 2 ? annual.id : care.id;
    try {
      const m = await api.post("/memberships", { plan_id: plan, client_id: o.client.id, device_id: o.device.id, billing: plan === care.id && i % 2 ? "monthly" : "yearly" });
      if (m.invoice_id && i !== 4) {
        const inv = await api.get(`/finance/invoices/${m.invoice_id}`);
        const acc = (await api.get("/cash/accounts")); const card = (acc.items || acc).find((a: any) => /card/i.test(a.name)) || (acc.items || acc)[0];
        await api.post(`/finance/invoices/${m.invoice_id}/pay`, { method: "card", amount: inv.balance_due ?? inv.total, account_id: card.id });
      }
      sold.push({ client: o.client.name, device: o.device.id, plan });
    } catch (e: any) { console.warn("[seed] membership:", e.message); }
  }
  // a member's device back in: the plan covers the diagnostic and takes 10% off the part
  const jordan = await api.get(`/orders/${others[0].id}`);
  const part = ((await api.get("/inventory/parts?search=Galaxy")).items || [])[0];
  const job = await api.post("/orders", {
    client_id: jordan.client.id, device_id: jordan.device.id, complaint: "Charging is intermittent again",
    lines: [{ description: diag?.name || "Diagnostic", kind: "service", line_type: "labour", ref_id: diag?.id || null, qty: 1, unit_price: diag?.price || 35 },
            ...(part ? [{ description: part.name, kind: "product", line_type: "part", ref_id: part.id, qty: 1, unit_price: part.sell_price }] : [])],
  });
  return { memberJob: { id: job.id, number: job.order_number, client: jordan.client.name }, sold };
}

export async function membershipsClip(o: Opts, token: string, memberJob: { id: string; number: string; client: string }): Promise<ClipMeta> {
  const clip = await Clip.open(o.browser, "memberships-desktop", { token, outDir: o.out, rawDir: o.work });
  const p = clip.page;
  await p.goto(o.base + "/catalog");
  const plansTab = p.getByRole("button", { name: /^Plans/ }).or(p.getByRole("tab", { name: /^Plans/ })).first();
  await plansTab.waitFor({ timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await plansTab.click();
  await p.getByText("Care Plan").first().waitFor({ timeout: 20000 });
  await sleep(800);
  await clip.markStart();
  await clip.pause(900);
  await clip.hover(p.getByText("Care Plan").first());
  clip.beat("Plans: Care Plan $12/mo · Annual Service Plan $99/yr", true);
  await clip.pause(2000);
  await p.goto(o.base + "/clients?tab=memberships");
  await p.getByText("Active members").waitFor({ timeout: 20000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await clip.pause(800);
  clip.beat("Members: active members, monthly recurring, renewing soon");
  const row = p.getByText(memberJob.client).first();
  await clip.click(row, { settle: 1500 });
  clip.beat(`${memberJob.client}'s plan: what's included and what's used`);
  await clip.pause(2200);
  await p.keyboard.press("Escape").catch(() => {});
  await p.goto(o.base + "/orders");
  await p.locator("tbody tr").first().waitFor();
  await p.waitForLoadState("networkidle").catch(() => {});
  await clip.click(p.locator("tbody tr", { hasText: memberJob.number }).first().locator("td").nth(2), { settle: 1200 });
  await clip.click(p.getByRole("tab", { name: /Services/ }).first(), { settle: 1000 });
  const apply = p.getByRole("button", { name: /Apply plan to/ }).first();
  if (await apply.count()) {
    await clip.hover(apply);
    clip.beat("Work order: plan banner offers to apply the benefits");
    await clip.pause(900);
    await clip.click(apply, { settle: 1600 });
    clip.beat("Plan applied: diagnostic covered, 10% off the part");
  } else clip.beat("Work order: member's plan banner");
  await clip.pause(2600);
  return clip.finish();
}

// ── A month of history for the reports ─────────────────────────────────────────────────────
export async function seedHistory(api: Api, dbFile: string, work: string) {
  const mk = (name: string, price: number, mins = 60) => api.post("/catalog/services", { name, price, duration_min: mins }).catch(() => null);
  for (const [n, p] of [["Brake pads and rotors – front", 420], ["Carburetor clean and tune", 95], ["Outboard 100-hour service", 385],
    ["Laptop keyboard replacement", 180], ["Back glass replacement", 169], ["Glass and digitizer – tablet", 229], ["Oil change and inspection", 79], ["Chain and bar service", 65]] as [string, number][])
    await mk(n, p);
  const svcs: any[] = (await api.get("/catalog/services")).items || [];
  const parts: any[] = (await api.get("/inventory/parts")).items || [];
  const locs = await api.get("/staff/locations"); const loc = (locs.items || locs)[0].id;
  for (const pt of parts) await api.post(`/inventory/parts/${pt.id}/adjust`, { location_id: loc, qty: 20, movement_type: "receive", unit_cost: pt.cost_price || 10 }).catch(() => {});
  const er = await api.get("/staff/employees"); const emps: any[] = (er.items || er || []).filter((e: any) => e.active !== false);
  const S = (re: RegExp) => svcs.find((s) => re.test(s.name));
  const P = (re: RegExp) => parts.find((x) => re.test(x.name));
  const kinds: { tpl: string; asset: [string, string]; svc: RegExp; part?: RegExp; w: number }[] = [
    { tpl: "mobile_phone", asset: ["Apple", "iPhone 13"], svc: /Screen replacement/, w: 6 },
    { tpl: "mobile_phone", asset: ["Apple", "iPhone 12"], svc: /Battery replacement/, part: /iPhone 13 battery/, w: 5 },
    { tpl: "mobile_phone", asset: ["Samsung", "Galaxy S23"], svc: /Charging port/, part: /Galaxy S23/, w: 4 },
    { tpl: "mobile_phone", asset: ["Google", "Pixel 8"], svc: /Diagnostic/, part: /Pixel 8 battery/, w: 3 },
    { tpl: "mobile_phone", asset: ["Apple", "iPhone 15 Pro"], svc: /Back glass/, w: 3 },
    { tpl: "mobile_phone", asset: ["Apple", "iPad Air (M2)"], svc: /digitizer/, w: 2 },
    { tpl: "computer", asset: ["Apple", "MacBook Air 13-inch (M2)"], svc: /Laptop keyboard/, part: /MacBook Air M2 keyboard/, w: 3 },
    { tpl: "auto", asset: ["Honda", "Civic 2017"], svc: /Brake pads/, w: 3 },
    { tpl: "auto", asset: ["Ford", "F-150 2019"], svc: /Oil change/, w: 3 },
    { tpl: "small_engine", asset: ["Stihl", "MS 271 chainsaw"], svc: /Chain and bar/, w: 2 },
    { tpl: "small_engine", asset: ["Honda", "HRN216 mower"], svc: /Carburetor/, w: 3 },
    { tpl: "marine", asset: ["Yamaha", "F150 outboard"], svc: /Outboard 100-hour/, w: 2 },
  ];
  const pool = kinds.flatMap((k) => Array(k.w).fill(k));
  const first = ["Avery", "Jordan", "Riley", "Casey", "Morgan", "Taylor", "Jamie", "Drew", "Quinn", "Reese", "Logan", "Harper", "Rowan", "Elliot", "Sasha", "Devon", "Blake", "Cameron"];
  const last = ["Nguyen", "Patel", "Garcia", "Kim", "Johnson", "Lopez", "Brown", "Wilson", "Davis", "Martin", "Clark", "Lewis", "Young", "Hall", "Allen", "Wright", "Scott", "Green"];
  let seed = 42; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const spec: Record<string, [number, number]> = {};
  const N = 46;
  for (let i = 0; i < N; i++) {
    const k = pool[Math.floor(rnd() * pool.length)];
    const days = 1 + Math.floor(rnd() * 29);
    const name = `${first[Math.floor(rnd() * first.length)]} ${last[Math.floor(rnd() * last.length)]}`;
    const c = await api.post("/clients", { name, phone: `(503) 555-${String(1000 + Math.floor(rnd() * 8999)).slice(0, 4)}` });
    const s = S(k.svc); const pt = k.part ? P(k.part) : null;
    const lines: any[] = [{ description: s?.name || "Repair", kind: "service", line_type: "labour", ref_id: s?.id || null, qty: 1, unit_price: s?.price || 120 }];
    if (pt) lines.push({ description: pt.name, kind: "product", line_type: "part", ref_id: pt.id, qty: 1, unit_price: pt.sell_price });
    const tech = emps[Math.floor(rnd() * emps.length)];
    try {
      const o = await api.post("/orders", { client_id: c.id, template_key: k.tpl, asset_brand: k.asset[0], asset_model: k.asset[1],
        complaint: s?.name, assigned_to_id: tech?.id || null, lines });
      await setStatus(api, o.id, "ready");
      await payOrder(api, o.id);
      await setStatus(api, o.id, rnd() < 0.7 ? "delivered" : "done");
      spec[o.id] = [days, 3 + Math.floor(rnd() * 70)];
    } catch (e: any) { console.warn("[seed] history:", e.message); }
  }
  const f = path.join(work, "backdate.json");
  fs.mkdirSync(work, { recursive: true });
  fs.writeFileSync(f, JSON.stringify(spec));
  const r = spawnSync(PY, ["-I", path.join(__dirname, "lib/backdate.py"), dbFile, f], { encoding: "utf8" });
  console.log("  [history]", (r.stdout || r.stderr || "").trim());
}

async function scrollPage(clip: Clip, p: Page, dy: number, ms = 1200) {
  await p.evaluate(([dy, ms]) => new Promise<void>((done) => {
    const el = (document.querySelector("main") as HTMLElement) || document.scrollingElement as HTMLElement;
    let sc: HTMLElement = el; for (const c of Array.from(document.querySelectorAll<HTMLElement>("main, main *, body *"))) { if (c.scrollHeight > c.clientHeight + 40 && /(auto|scroll)/.test(getComputedStyle(c).overflowY) && c.clientHeight > 400) { sc = c; break; } }
    const s0 = sc.scrollTop, t0 = performance.now();
    const step = (now: number) => { const k = Math.min(1, (now - t0) / ms); const e = k < .5 ? 4*k*k*k : 1 - Math.pow(-2*k+2,3)/2; sc.scrollTop = s0 + dy * e; if (k < 1) requestAnimationFrame(step); else done(); };
    requestAnimationFrame(step); }), [dy, ms] as const);
  await sleep(300);
}

export async function reportsClip(o: Opts, token: string): Promise<ClipMeta> {
  const clip = await Clip.open(o.browser, "reports-desktop", { token, outDir: o.out, rawDir: o.work });
  const p = clip.page;
  await p.goto(o.base + "/reports");
  await p.getByText("Provided services (Ready+Closed)").first().waitFor({ timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await clip.markStart();
  await clip.pause(1000);
  clip.beat("Reports: jobs, customers, sales, still owed (top cards)");
  const run = p.getByRole("button", { name: "Run", exact: true }).first();
  const report = async (name: string, beat: string, scroll = 0) => {
    await clip.click(p.getByText(name, { exact: true }).first(), { settle: 500 });
    await clip.click(run, { settle: 1800 });
    clip.beat(beat, name.startsWith("Provided"));
    if (scroll) { await clip.moveTo(940, 640); await scrollPage(clip, p, scroll, 1800); await clip.pause(900); await scrollPage(clip, p, -scroll, 1200); }
    await clip.pause(1200);
  };
  await report("Provided services (Ready+Closed)", "Provided services: best-selling services, last 30 days", 380);
  await report("Sold products (Ready+Closed)", "Sold products: parts sold, last 30 days");
  await report("Technicians performance", "Technicians performance");
  await clip.click(p.getByText("Total profit", { exact: true }).first(), { settle: 2200 });
  clip.beat("Total profit: sales, profit, by location and technician");
  await clip.pause(2400);
  await clip.click(p.getByText("Business & analytics", { exact: true }).first(), { settle: 2200 });
  clip.beat("Business & analytics (30 days): revenue, orders closed, avg ticket, turnaround");
  await clip.pause(2600);
  return clip.finish();
}
