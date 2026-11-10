// Self check-in (QR at the door): the customer's form on a phone, the staff walk-in queue, the
// shop's real QR + printable poster, and the same page for three other kinds of shop.
import crypto from "crypto";
import fs from "fs";
import path from "path";
import type { Browser, Locator } from "playwright";
import { Api } from "./lib/api";
import { Clip, type ClipMeta } from "./lib/clip";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export type Point = { id: string; token: string; path: string; url: string; name: string };

/** The shop's "Front door" point (made for every shop), pinned to walk-in so the page doesn't
 *  switch to "key drop" when the capture runs outside shop hours. */
export async function frontDoor(api: Api): Promise<Point> {
  const pts = await api.get("/checkins/points");
  const p = pts.items[0];
  await api.put(`/checkins/points/${p.id}`, { name: p.name, mode: "walk_in", kiosk: false, job_type_ids: p.job_type_ids || [],
    welcome_text: p.welcome_text || null, terms_template_id: p.terms_template_id || null, require_photo: false, show_wait: true, active: true });
  return { id: p.id, token: p.path.split("/c/")[1], path: p.path, url: p.url, name: p.name };
}

type Opts = { browser: Browser; base: string; out: string; work: string };

async function type(clip: Clip, l: Locator, text: string, delay = 85) {
  await clip.tap(l, 350);
  await clip.page.keyboard.type(text, { delay });
  await sleep(350);
}

function squiggle(b: { x: number; y: number; width: number; height: number }) {
  const pts: { x: number; y: number }[][] = [];
  const W = b.width - 40, H = b.height - 50, x0 = b.x + 20, y0 = b.y + 14;
  const P = [[0.02, 0.9], [0.08, 0.1], [0.15, 0.75], [0.22, 0.08], [0.28, 0.88]];
  const m: { x: number; y: number }[] = [];
  for (let s = 0; s < 4; s++) for (let i = 0; i <= 10; i++) { const k = i / 10; m.push({ x: x0 + W * (P[s][0] + (P[s + 1][0] - P[s][0]) * k), y: y0 + H * (P[s][1] + (P[s + 1][1] - P[s][1]) * k) }); }
  pts.push(m);
  const w: { x: number; y: number }[] = [];
  for (let i = 0; i <= 70; i++) { const t = i / 70; w.push({ x: x0 + W * (0.32 + t * 0.62 + 0.025 * Math.cos(t * Math.PI * 6)), y: y0 + H * (0.62 - 0.25 * Math.sin(t * Math.PI * 6)) }); }
  pts.push(w);
  return pts;
}

// ── Maya checks herself in at Dani's ───────────────────────────────────────────────────────
export async function checkinMobile(o: Opts, point: Point): Promise<ClipMeta> {
  const clip = await Clip.open(o.browser, "checkin-mobile", { mobile: true, outDir: o.out, rawDir: o.work });
  const p = clip.page;
  await p.goto(o.base + point.path);
  await p.locator("#ci-issue").waitFor({ timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await sleep(400);
  await clip.markStart();
  await clip.pause(1300);
  clip.beat("Dani's Repair check-in page (scanned the QR at the door)");
  await type(clip, p.locator("#ci-issue"), "Dropped it, screen cracked", 95);
  clip.beat("What's wrong: Dropped it, screen cracked");
  await p.keyboard.press("Tab").catch(() => {});
  const phone = p.getByRole("button", { name: "Phone", exact: true });
  await clip.scrollMobileTo(phone, 0.3, 800);
  if (await phone.count()) await clip.tap(phone, 600);
  await type(clip, p.locator("#f-brand"), "Apple", 110);
  await type(clip, p.locator("#f-model"), "iPhone 13", 95);
  clip.beat("Device: Apple iPhone 13");
  await p.keyboard.press("Tab").catch(() => {});
  await clip.scrollMobileTo(p.locator("#ci-name"), 0.3, 800);
  await type(clip, p.locator("#ci-name"), "Maya Chen", 100);
  await type(clip, p.locator("#ci-phone"), "(503) 555-0187", 90);
  clip.beat("Name and mobile");
  await p.keyboard.press("Tab").catch(() => {});
  const pad = p.getByLabel("Sign here");
  await clip.scrollMobileTo(pad, 0.45, 700);
  await clip.pause(400);
  const b = await clip.box(pad);
  for (const s of squiggle(b)) { await clip.touchPath(s, 13); await sleep(150); }
  await clip.pause(600);
  clip.beat("Signed (agrees to the shop's terms)");
  await clip.tap(p.getByRole("button", { name: "Check in", exact: true }), 200);
  clip.beat("Check in tapped");
  await p.waitForURL(/\/c\/s\//, { timeout: 20000 });
  await p.getByText(/checked in/i).first().waitFor({ timeout: 20000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await clip.pause(600);
  clip.beat("Confirmation: You're checked in, place in the queue", true);
  await clip.pause(2600);
  return clip.finish();
}

// ── Staff: the walk-in queue → call → work order ───────────────────────────────────────────
export async function checkinArrives(o: Opts, token: string): Promise<ClipMeta> {
  const clip = await Clip.open(o.browser, "checkin-arrives-desktop", { token, outDir: o.out, rawDir: o.work });
  const p = clip.page;
  await p.goto(o.base + "/inquiries?view=walkins");
  const row = p.getByText(/^Maya Chen/).first();
  await row.waitFor({ timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await sleep(500);
  await clip.markStart();
  await clip.pause(900);
  await clip.hover(row);
  clip.beat("Walk-ins queue: Maya Chen #1 waiting · iPhone 13 · Dropped it, screen cracked", true);
  await clip.pause(1500);
  await clip.click(p.getByRole("button", { name: "Call next" }).first(), { settle: 1400 });
  clip.beat("Call next (moves her to Being served)");
  await clip.pause(600);
  await clip.click(p.getByRole("button", { name: "Create work order" }).first(), { settle: 300 });
  clip.beat("Create work order clicked");
  await p.waitForURL(/\/orders/, { timeout: 20000 });
  await p.getByRole("tab", { name: /Services/ }).first().waitFor({ timeout: 20000 }).catch(() => {});
  await p.waitForLoadState("networkidle").catch(() => {});
  await clip.pause(700);
  clip.beat("Work order opens with her details filled in");
  await clip.moveTo(1000, 260);
  await clip.pause(2600);
  return clip.finish();
}

// ── The shop's real QR (app's own SVG) and printable poster ────────────────────────────────
export async function qrAssets(o: Opts, api: Api, point: Point) {
  const r = await fetch(`${o.base}/api/checkins/points/${point.id}/qr.svg`, { headers: { Authorization: `Bearer ${api.token}` } });
  const svg = await r.text();
  const page = await o.browser.newPage({ viewport: { width: 1200, height: 1200 }, deviceScaleFactor: 1 });
  await page.setContent(`<html><body style="margin:0;background:#fff;display:grid;place-items:center;height:100vh">
    <div style="width:1040px;height:1040px">${svg.replace(/<svg /, '<svg style="width:100%;height:100%" ')}</div></body></html>`);
  await page.screenshot({ path: path.join(o.out, "checkin-qr.png") });
  const poster = await api.get(`/checkins/points/${point.id}/poster`);
  await page.setViewportSize({ width: 816, height: 1056 });      // US Letter at 96 dpi
  const pp = await o.browser.newPage({ viewport: { width: 816, height: 1056 }, deviceScaleFactor: 2 });
  await pp.setContent(poster.html);
  await pp.addStyleTag({ content: "body{margin:0;padding:60px 48px;background:#fff}" });
  await pp.screenshot({ path: path.join(o.out, "checkin-poster.png"), fullPage: false });
  await page.close(); await pp.close();
  console.log(`  [qr] checkin-qr.png + checkin-poster.png (QR encodes ${point.url})`);
  return point.url;
}

// ── Three other trades: their own shop, colour and form ────────────────────────────────────
export const TRADES = [
  { file: "checkin-auto-mobile", org: "Northside Auto", owner: "Marcus Hale", tpl: "auto", color: "#b91c1c", chip: null as string | null, beat: "VIN, make/model/year, then trim, plate and mileage" },
  { file: "checkin-engine-mobile", org: "Ridge Small Engine", owner: "Jen Ridge", tpl: "small_engine", color: "#166534", chip: "Chainsaw", beat: "Chainsaw: serial, brand/model, engine model and hours" },
  { file: "checkin-marine-mobile", org: "Harbor Marine", owner: "Rafael Ortiz", tpl: "marine", color: "#0f766e", chip: "Outboard Motor", beat: "Outboard motor: engine serial, make/model/year, length, registration" },
];

export async function seedTrade(base: string, t: (typeof TRADES)[number]) {
  const api = new Api(base);
  const slug = t.org.toLowerCase().replace(/[^a-z]+/g, "");
  const r = await api.post("/auth/register", { org_name: t.org, name: t.owner, email: `owner@${slug}.test`,
    password: "Cap-" + crypto.randomBytes(12).toString("base64url"), accept_terms: true, country: "US", industry_template: t.tpl });
  api.token = r.access_token;
  await api.put("/staff/public-pages", { page_color: t.color });
  return { api, point: await frontDoor(api) };
}

export async function tradeClip(o: Opts, t: (typeof TRADES)[number], point: Point): Promise<ClipMeta> {
  const clip = await Clip.open(o.browser, t.file, { mobile: true, outDir: o.out, rawDir: o.work });
  const p = clip.page;
  await p.goto(o.base + point.path);
  await p.locator("#ci-issue").waitFor({ timeout: 30000 });
  await p.waitForLoadState("networkidle").catch(() => {});
  await sleep(300);
  await clip.markStart();
  await clip.pause(1100);
  clip.beat(`${t.org} check-in page`);
  const sec = p.getByRole("heading", { name: /^Your / }).first();
  await clip.scrollMobileTo(sec, 0.12, 700);
  if (t.chip) { const chip = p.getByRole("button", { name: t.chip, exact: true }); if (await chip.count()) await clip.tap(chip, 500); }
  const more = p.getByRole("button", { name: /Add more details/ });
  if (await more.count()) await clip.tap(more, 500);
  clip.beat(t.beat, true);
  await clip.pause(2200);
  return clip.finish();
}

export function exists(f: string) { return fs.existsSync(f); }
