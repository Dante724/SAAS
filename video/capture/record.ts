// Records the app half of the Tekly promo: starts a fresh app server, seeds a demo shop through
// the API, drives the real UI with Playwright, and writes trimmed webm clips + posters + a
// manifest to video/public/clips/.
//
//   cd video && npx tsx capture/record.ts            # everything
//   npx tsx capture/record.ts --only=intake,approve  # a subset (earlier steps still run, unrecorded via API where needed)
//   npx tsx capture/record.ts --keep                  # leave the server running at the end
import { chromium, type Browser, type Page } from "playwright";
import fs from "fs";
import path from "path";
import { startServer, ensureFrontendBuilt, REPO } from "./lib/server";
import { Clip, type ClipMeta, countFrames } from "./lib/clip";
import { makePhotos } from "./lib/photos";
import { seedBase, prepareApproval, setStatus, payOrder, type Seed } from "./seed";

const OUT = path.resolve(__dirname, "../public/clips");
const args = process.argv.slice(2);
const only = (args.find((a) => a.startsWith("--only="))?.split("=")[1] || "").split(",").filter(Boolean);
const want = (n: string) => !only.length || only.includes(n);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type Ctx = { browser: Browser; base: string; seed: Seed; work: string; photos: { cracked: string; battery: string };
             hero?: { id: string; number: string; track?: string } };

async function waitTable(p: Page) {
  await p.locator("tbody tr").first().waitFor({ state: "visible", timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await sleep(600);
}
const heroRow = (p: Page, num: string) => p.locator("tbody tr", { hasText: num }).first();
const drawer = (p: Page) => p.locator("div.fixed").filter({ has: p.getByRole("tab", { name: /Services/ }) }).last();

// ── 1. Intake ──────────────────────────────────────────────────────────────────────────────
async function intake(c: Ctx): Promise<ClipMeta | null> {
  const clip = await Clip.open(c.browser, "intake-desktop", { token: c.seed.token, outDir: OUT, rawDir: c.work });
  const p = clip.page;
  await p.goto(c.base + "/orders"); await waitTable(p);
  await clip.markStart();
  await clip.pause(700);
  await clip.click(p.getByRole("button", { name: "New work order" }).first());
  clip.beat("New work order drawer opens");
  const search = p.getByPlaceholder(/Search by name, phone or email/);
  await clip.type(search, "Maya", 110);
  const opt = p.getByRole("button", { name: /Maya Chen/ }).or(p.getByRole("option", { name: /Maya Chen/ })).first();
  await clip.click(opt, { settle: 700 });
  clip.beat("Customer Maya Chen picked");

  const complaint = p.getByPlaceholder("Screen cracked after a drop, touch still works");
  await clip.scrollTo(complaint, "start", 900);
  await clip.type(complaint, "Dropped it on the sidewalk. Screen cracked, touch still works.", 38);
  const field = (label: string) => p.locator("label", { hasText: new RegExp(`^${label}`) }).locator("xpath=following::input[1]").first();
  await clip.scrollTo(field("Brand"), "center", 700);
  await clip.type(field("Brand"), "Apple", 90);
  await p.keyboard.press("Escape").catch(() => {});
  await clip.type(field("Model"), "iPhone 13", 80);
  await p.keyboard.press("Escape").catch(() => {});
  await clip.type(field("IMEI"), "353912110123451", 70);
  clip.beat("IMEI entered");

  // condition photo
  const drop = p.getByText("Condition photos", { exact: true }).first();
  await clip.scrollTo(drop, "center", 700);
  await clip.hover(drop);
  await clip.pause(400);
  await p.locator('input[type="file"][accept="image/*"]').first().setInputFiles(c.photos.cracked);
  const thumb = p.locator('img[alt="intake"]').first();
  await thumb.waitFor({ state: "visible", timeout: 20000 });
  await clip.pause(500);
  clip.beat("Photo attached");
  await clip.click(thumb, { settle: 400 });
  await p.getByText("Mark up photo").waitFor();
  const canvas = p.locator("canvas").last();
  await p.waitForFunction(() => { const cs = Array.from(document.querySelectorAll("canvas")); const cv = cs[cs.length - 1] as HTMLCanvasElement; return cv && cv.width > 300 && !cv.classList.contains("hidden"); });
  await clip.pause(700);
  clip.beat("Markup tool open");
  const b = await clip.box(canvas);
  const cx = b.x + b.width * 0.577, cy = b.y + b.height * 0.705, r = b.width * 0.13;
  // circle tool: press at the impact point, drag out to the radius
  const pts = Array.from({ length: 34 }, (_, i) => { const k = i / 33; const e = 1 - Math.pow(1 - k, 2); return { x: cx + r * e, y: cy + r * 0.85 * e }; });
  await clip.drag(pts, 22);
  await clip.pause(300);
  clip.beat("Crack circled");
  await clip.pause(700);
  await clip.click(p.getByRole("button", { name: "Save markup" }), { settle: 1000 });
  clip.beat("Markup saved");

  await clip.click(p.getByRole("button", { name: /Next: services and products/ }), { settle: 900 });
  const pick = p.getByPlaceholder(/Search a service or product to add|Search a service to add|Search services/).first();
  await clip.type(pick, "Screen", 110);
  const svc = p.getByText("Screen replacement – iPhone 13").first();
  await svc.waitFor({ timeout: 15000 });
  await clip.pause(400);
  await clip.click(svc, { settle: 800 });
  clip.beat("Screen replacement $140 added");
  await clip.pause(500);
  await clip.click(p.getByRole("button", { name: /Create work order/ }), { settle: 300 });
  const row = p.locator("tbody tr").first();
  await p.getByRole("button", { name: /Create work order/ }).waitFor({ state: "hidden", timeout: 20000 });
  await sleep(900);
  const num = (await row.locator("td").nth(1).innerText()).trim();
  await clip.hover(row, 0.35, 0.5);
  clip.beat(`Work order ${num} appears on the board`, true);
  await clip.pause(1800);
  const meta = await clip.finish();
  // hand the job to the rest of the story
  const list = await c.seed.api.get(`/orders?search=${encodeURIComponent("Maya")}`);
  const o = (list.items || list).find((x: any) => x.order_number === num) || (list.items || list)[0];
  c.hero = { id: o.id, number: o.order_number };
  return meta;
}

/** Without the intake clip: make the same job through the API. */
async function intakeViaApi(c: Ctx) {
  const img = "data:image/jpeg;base64," + fs.readFileSync(c.photos.cracked).toString("base64");
  const m = await c.seed.api.post("/media", { data: img, name: "check-in.jpg", mime: "image/jpeg" });
  const svc = (await c.seed.api.get("/catalog/services?search=Screen")).items?.[0];
  const o = await c.seed.api.post("/orders", {
    client_id: c.seed.maya.id, complaint: "Dropped it on the sidewalk. Screen cracked, touch still works.",
    asset_brand: "Apple", asset_model: "iPhone 13", asset_imei: "353912110123451", intake_photos: [m.id],
    lines: [{ description: "Screen replacement – iPhone 13", kind: "service", line_type: "labour", ref_id: svc?.id || null, qty: 1, unit_price: 140 }],
  });
  c.hero = { id: o.id, number: o.order_number };
}

// ── 2. Board with the new job ──────────────────────────────────────────────────────────────
async function board(c: Ctx, name: string, approved: boolean): Promise<ClipMeta> {
  const clip = await Clip.open(c.browser, name, { token: c.seed.token, outDir: OUT, rawDir: c.work });
  const p = clip.page;
  await p.goto(c.base + "/orders"); await waitTable(p);
  await clip.markStart();
  await clip.pause(600);
  const row = heroRow(p, c.hero!.number);
  const pill = row.getByText(approved ? "Approved" : "Awaiting approval", { exact: true });
  await clip.hover(pill);
  clip.beat(approved ? "Status pill: Approved" : "New job on the board · Awaiting approval", !approved ? false : true);
  await clip.pause(1200);
  await clip.click(row.locator("td").nth(2), { settle: 1200 });
  clip.beat("Work order opens");
  if (!approved) {
    const photos = p.getByText("Intake photos", { exact: true }).first();
    await clip.scrollTo(photos, "start", 1200);
    const img = p.locator('img[alt="intake"]').last();
    await clip.hover(img);
    clip.beat("Intake photo with the circled crack", true);
    await clip.pause(600);
    await clip.click(img, { settle: 400 });
    await p.getByText("Mark up photo").waitFor().catch(() => {});
    await clip.pause(2200);
    clip.beat("Marked-up photo full size");
  } else {
    await clip.pause(600);
    await clip.click(p.getByRole("tab", { name: /Activity/ }).or(p.getByText("Activity", { exact: true })).first(), { settle: 1200 });
    clip.beat("Activity: customer approved and signed", true);
    const sig = p.locator("img").filter({ has: p.locator("xpath=self::*[contains(@src,'data:image/png')]") }).first();
    if (await sig.count()) { await clip.scrollTo(sig, "center", 900); await clip.hover(sig); }
    await clip.pause(2200);
  }
  return clip.finish();
}

// ── 3. Maya approves on her phone ──────────────────────────────────────────────────────────
async function approve(c: Ctx): Promise<ClipMeta> {
  const clip = await Clip.open(c.browser, "approve-mobile", { mobile: true, outDir: OUT, rawDir: c.work });
  const p = clip.page;
  await p.goto(c.base + c.hero!.track!);
  await p.getByText("We need your approval to continue").waitFor({ timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await sleep(500);
  await clip.markStart();
  await clip.pause(1200);
  clip.beat("Tracking page: We need your approval");
  // the photo the shop shared
  const shared = p.locator("section").filter({ hasText: "Updates" }).locator("img").first();
  await clip.scrollMobileTo(shared, 0.3, 700);
  await clip.pause(900);
  clip.beat("Photo of the cracked screen");
  await clip.tap(shared, 1600);
  clip.beat("Photo full screen");
  await clip.tap(p.getByRole("dialog", { name: "Photo" }), 700);
  // the battery recommendation
  const rec = p.locator("#recommendations");
  await clip.scrollMobileTo(rec, 0.12, 700);
  await clip.pause(1200);
  clip.beat("Battery at 71% – replace? +$45");
  const notNow = p.getByRole("button", { name: "Not now" });
  await clip.scrollMobileTo(notNow, 0.55, 600);
  await clip.pause(500);
  await clip.tap(notNow, 1400);
  clip.beat("Battery declined: Not now");
  // the estimate: Screen replacement $140
  const est = p.getByText("What we're doing").first();
  await clip.scrollMobileTo(est, 0.3, 700);
  await clip.pause(1300);
  clip.beat("Screen replacement – $140");
  await clip.tap(p.getByRole("button", { name: "Review and sign" }), 900);
  clip.beat("Review and sign");
  const pad = p.locator("canvas").first();
  await pad.waitFor();
  const b = await clip.box(pad);
  // a handwritten "Maya" squiggle, scaled into the pad
  const sig = signaturePath().map(([x, y]) => ({ x: b.x + 18 + x * (b.width - 36), y: b.y + 18 + y * (b.height - 36) }));
  let i = 0;
  for (const stroke of splitStrokes(sig)) { await clip.touchPath(stroke, 13); i++; await sleep(140); }
  await clip.pause(600);
  clip.beat("Signature done", true);
  await clip.tap(p.getByRole("button", { name: "Approve and sign" }), 300);
  clip.beat("Approve and sign tapped");
  await p.getByText(/approved/i).first().waitFor({ timeout: 15000 });
  await clip.pause(1800);
  await p.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await clip.pause(1200);
  await p.reload(); await p.waitForLoadState("networkidle").catch(() => {});
  await clip.pause(500);
  clip.beat("Status: Approved");
  await clip.pause(2000);
  return clip.finish();
}

function signaturePath(): [number, number][] {
  // normalized 0..1 points; strokes separated by NaN
  const pts: [number, number][] = [];
  const add = (f: (t: number) => [number, number], n: number) => { for (let i = 0; i <= n; i++) pts.push(f(i / n)); pts.push([NaN, NaN]); };
  // "M"
  add((t) => { const seg = Math.min(3, Math.floor(t * 4)); const k = t * 4 - seg;
    const P = [[0.02, 0.85], [0.08, 0.15], [0.15, 0.7], [0.22, 0.12], [0.27, 0.82]];
    const a = P[seg], bb = P[seg + 1]; return [a[0] + (bb[0] - a[0]) * k, a[1] + (bb[1] - a[1]) * k]; }, 40);
  // "aya" as a looping cursive wave
  add((t) => { const x = 0.3 + t * 0.48; const y = 0.62 - 0.22 * Math.sin(t * Math.PI * 6) + (t > 0.5 ? 0.12 * Math.sin((t - 0.5) * Math.PI * 2) : 0);
    return [x + 0.025 * Math.cos(t * Math.PI * 6), y]; }, 70);
  // descender + flourish
  add((t) => [0.78 + 0.18 * t, 0.55 + 0.25 * Math.sin(t * Math.PI) - 0.35 * t], 26);
  return pts;
}
function splitStrokes(p: { x: number; y: number }[]) {
  const out: { x: number; y: number }[][] = []; let cur: { x: number; y: number }[] = [];
  for (const q of p) { if (Number.isNaN(q.x)) { if (cur.length) out.push(cur); cur = []; } else cur.push(q); }
  if (cur.length) out.push(cur);
  return out;
}

// ── 4. Stock + timer ───────────────────────────────────────────────────────────────────────
async function stock(c: Ctx): Promise<ClipMeta> {
  const clip = await Clip.open(c.browser, "stock-desktop", { token: c.seed.token, outDir: OUT, rawDir: c.work });
  const p = clip.page;
  await p.goto(c.base + "/inventory");
  const row = p.locator("tr", { hasText: "iPhone 13 screen" }).first();
  await row.waitFor({ timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await sleep(500);
  await clip.markStart();
  await clip.pause(700);
  await clip.hover(row, 0.3);
  clip.beat("iPhone 13 screen: 1 in stock, 1 reserved", true);
  await clip.pause(1200);
  await clip.click(row.locator("td").nth(1), { settle: 1400 });
  clip.beat("Product opens");
  const stockTab = p.locator("button", { hasText: /^Stock$/ }).last();
  await clip.click(stockTab, { settle: 1200 });
  const loc = p.getByText("Main location", { exact: false }).last();
  if (await loc.count()) await clip.hover(loc);
  clip.beat("Stock by location: 1 available, 1 reserved for the job");
  await clip.pause(1800);
  // the job: start the labour timer
  await p.goto(c.base + "/orders"); await waitTable(p);
  await clip.click(heroRow(p, c.hero!.number).locator("td").nth(2), { settle: 1200 });
  const timer = p.getByRole("button", { name: /^Timer/ }).first();
  await clip.click(timer, { settle: 600 });
  clip.beat("Labour timer started");
  await clip.hover(p.getByTitle("Stop the job timer").first()).catch(() => {});
  await clip.pause(3200);
  return clip.finish("Starts with the inventory page; at the 'Labour timer started' beat a page navigation to the work order happens about 1.5 s earlier (cut there if needed).");
}

// ── 5. Done → invoice → pay link ───────────────────────────────────────────────────────────
async function invoice(c: Ctx): Promise<ClipMeta> {
  const clip = await Clip.open(c.browser, "invoice-desktop", { token: c.seed.token, outDir: OUT, rawDir: c.work });
  const p = clip.page;
  await p.goto(c.base + "/orders"); await waitTable(p);
  await p.locator("tbody tr", { hasText: c.hero!.number }).first().locator("td").nth(2).click();
  await p.getByRole("tab", { name: /Invoices/ }).or(p.getByText("Invoices & payments")).first().waitFor();
  await sleep(1200);
  await clip.markStart();
  await clip.pause(700);
  // header status pill → Ready (the shop's "repair done" status; it raises the invoice)
  const pill = p.locator("button", { hasText: /^(Approved|In repair)$/ }).first();
  await clip.click(pill, { settle: 700 });
  const filter = p.getByPlaceholder("Filter statuses");
  await filter.waitFor();
  await clip.pause(500);
  await filter.pressSequentially("Rea", { delay: 120 });
  await clip.pause(400);
  const ready = p.getByText("Ready", { exact: true }).locator("visible=true").last();
  await clip.click(ready, { settle: 1500 });
  clip.beat("Marked Ready (repair done)");
  await clip.click(p.getByText("Invoices & payments", { exact: true }).first(), { settle: 1200 });
  clip.beat("Invoice created automatically", true);
  await clip.pause(900);
  await clip.click(p.getByRole("button", { name: /Payment link/ }).first(), { settle: 1500 });
  clip.beat("Pay link ready");
  const link = p.getByText(/Payment link ·/).first();
  if (await link.count()) await clip.hover(link);
  await clip.pause(1800);
  const inv = p.getByText(/^INV-\d+/).last();
  if (await inv.count()) { await clip.hover(inv); clip.beat("Invoice row: $140, ready to send"); }
  await clip.pause(1800);
  return clip.finish();
}

// ── 6. End of day ──────────────────────────────────────────────────────────────────────────
async function eod(c: Ctx): Promise<ClipMeta> {
  const clip = await Clip.open(c.browser, "eod-board-desktop", { token: c.seed.token, outDir: OUT, rawDir: c.work });
  const p = clip.page;
  await p.goto(c.base + "/orders"); await waitTable(p);
  await clip.markStart();
  await clip.pause(1000);
  clip.beat("End of day board");
  await clip.hover(heroRow(p, c.hero!.number).locator("td").nth(4));
  clip.beat(`${c.hero!.number} Maya Chen · Done`, true);
  await clip.pause(1200);
  await clip.moveTo(900, 700);
  await p.mouse.wheel(0, 0);
  for (let i = 0; i < 20; i++) { await p.mouse.wheel(0, 22); await sleep(40); }
  await clip.pause(1200);
  for (let i = 0; i < 20; i++) { await p.mouse.wheel(0, -22); await sleep(40); }
  await clip.pause(700);
  const doneChip = p.getByRole("button", { name: /^Done\s*\d+/ }).first();
  await clip.click(doneChip, { settle: 1400 });
  clip.beat("Filter: Done");
  await clip.pause(1800);
  return clip.finish();
}

// ── main ───────────────────────────────────────────────────────────────────────────────────
(async () => {
  ensureFrontendBuilt();
  fs.mkdirSync(OUT, { recursive: true });
  const srv = await startServer(8131);
  const browser = await chromium.launch();
  const manifest: Record<string, ClipMeta> = {};
  const manifestFile = path.join(OUT, "manifest.json");
  try { Object.assign(manifest, JSON.parse(fs.readFileSync(manifestFile, "utf8")).clips || {}); } catch { /* fresh */ }
  try {
    const work = path.join(srv.dir, "raw");
    const photos = await makePhotos(browser, path.join(srv.dir, "photos"), path.join(REPO, "website/assets/photos/iphone-mat-960.webp"));
    console.log("[seed] creating the shop…");
    const seed = await seedBase(srv.base, path.join(srv.dir, "test-credentials.json"));
    const c: Ctx = { browser, base: srv.base, seed, work, photos };
    const save = (m: ClipMeta | null) => { if (!m) return; manifest[m.file.replace(/\.webm$/, "")] = m;
      fs.writeFileSync(manifestFile, JSON.stringify({ generated: new Date().toISOString(), note: "beats t = seconds from clip start", clips: manifest }, null, 2)); };

    if (want("intake")) save(await intake(c)); else await intakeViaApi(c);
    c.hero!.track = await prepareApproval(seed, c.hero!.id, photos.battery);
    if (want("board")) save(await board(c, "board-desktop", false));
    if (want("approve")) save(await approve(c));
    else {
      const tok = c.hero!.track!.split("/track/")[1];
      const png = "data:image/png;base64," + fs.readFileSync(photos.battery).toString("base64");
      const rec = (await seed.api.get(`/orders/${c.hero!.id}/recommendations`)).items[0];
      await fetch(`${srv.base}/api/public/track/${tok}/recommendations/decide`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ later_ids: [rec.id] }) });
      await fetch(`${srv.base}/api/public/track/${tok}/respond`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision: "accept", signature: png }) });
    }
    if (want("board-approved")) save(await board(c, "board-approved-desktop", true));
    // hold the screen for this job (the shop reserves the part when the customer approves)
    await seed.api.post("/inventory/reserve", { part_id: seed.partId, location_id: seed.locationId, qty: 1, order_id: c.hero!.id, hold_days: 7 });
    if (want("stock")) save(await stock(c));
    if (want("invoice")) save(await invoice(c));
    else await setStatus(seed.api, c.hero!.id, "ready");
    // the rest of the day happens: Maya pays and collects, the bench clears
    await payOrder(seed.api, c.hero!.id);
    await setStatus(seed.api, c.hero!.id, "done");
    for (const o of seed.others) {
      if (["done", "delivered"].includes(o.eod)) await payOrder(seed.api, o.id);
      await setStatus(seed.api, o.id, o.eod);
    }
    if (want("eod")) save(await eod(c));

    // verify
    console.log("\n[verify]");
    for (const m of Object.values(manifest)) {
      const f = path.join(OUT, m.file);
      if (!fs.existsSync(f)) continue;
      const frames = countFrames(f);
      console.log(`  ${m.file}: ${m.durationSec}s, ${frames} frames, ${m.width}x${m.height}`);
      if (!(m.durationSec > 2) || frames < 50) throw new Error(`${m.file} looks broken`);
    }
  } catch (e) {
    let i = 0;
    for (const cx of browser.contexts()) for (const pg of cx.pages()) await pg.screenshot({ path: path.join(srv.dir, `fail-${i++}.png`) }).catch(() => {});
    console.error(`[fail] screenshots in ${srv.dir}`);
    throw e;
  } finally {
    await browser.close();
    if (args.includes("--keep")) console.log(`[server] left running at ${srv.base}`);
    else await srv.stop();
  }
})().catch((e) => { console.error(e); process.exit(1); });
